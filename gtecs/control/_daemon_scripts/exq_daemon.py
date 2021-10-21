#!/usr/bin/env python3
"""Daemon to control the exposure queue."""

import os
import threading
import time

from astropy.time import Time

from gtecs.control import errors
from gtecs.control import misc
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, daemon_proxy
from gtecs.control.exposures import Exposure, ExposureQueue


class ExqDaemon(BaseDaemon):
    """Exposure queue hardware daemon class."""

    def __init__(self):
        super().__init__('exq')

        # exq is dependent on all the interfaces, cam and filt
        for interface_id in params.INTERFACES:
            self.dependencies.add(interface_id)
        self.dependencies.add('cam')
        self.dependencies.add('filt')

        # exposure queue variables
        self.paused = True  # start paused
        self.exp_queue = ExposureQueue()
        self.current_exposure = None
        self.exposure_state = 'none'

        self.set_number_file = os.path.join(params.FILE_PATH, 'set_number')
        try:
            with open(self.set_number_file, 'r') as f:
                self.latest_set_number = int(f.read())
        except Exception:
            self.latest_set_number = 0

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

        while(self.running):
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time
                self.force_check_flag = False

                # Check the dependencies
                self._check_dependencies()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's successful
                if self.dependency_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

            # exposure queue processes
            # only do anything if we're not paused
            if not self.paused:
                # If we're not currently doing anything and there are exposures in the
                # queue then pop off the first one and make that the current
                if self.current_exposure is None and len(self.exp_queue) > 0:
                    self.log.info('Starting new exposure')
                    self.current_exposure = self.exp_queue.pop(0)
                    self.exposure_state = 'init'

                # Exposure state machine
                if self.exposure_state == 'init':
                    # First check if we need to change the filters
                    if self.current_exposure.filt is None:
                        # Filter doesn't matter, e.g. dark, so skip to the exposures
                        self.log.info('No need to move filter wheel')
                        self.exposure_state = 'filters_set'
                    else:
                        # Get the filter wheel info
                        with daemon_proxy('filt') as filt_daemon:
                            filt_info = filt_daemon.get_info()
                        filt_uts = [ut for ut in self.current_exposure.ut_list
                                    if ut in params.UTS_WITH_FILTERWHEELS]

                        # Check if we need to home the filters
                        if all(filt_info[ut]['homed'] for ut in filt_uts):
                            self.exposure_state = 'filters_homed'
                        else:
                            self.log.info('Homing filter wheels')
                            try:
                                with daemon_proxy('filt') as filt_daemon:
                                    filt_daemon.home_filters(filt_uts)
                                    self.exposure_state = 'filters_homing'
                            except Exception:
                                self.log.error('No response from filter wheel daemon')
                                self.log.debug('', exc_info=True)

                elif self.exposure_state == 'filters_homing':
                    # Get the filter wheel info
                    with daemon_proxy('filt') as filt_daemon:
                        filt_info = filt_daemon.get_info()
                    filt_uts = [ut for ut in self.current_exposure.ut_list
                                if ut in params.UTS_WITH_FILTERWHEELS]

                    # Check if the filters are homed
                    if all(filt_info[ut]['homed'] for ut in filt_uts):
                        self.log.info('Filter wheels homed')
                        self.exposure_state = 'filters_homed'

                elif self.exposure_state == 'filters_homed':
                    # Get the filter wheel info
                    with daemon_proxy('filt') as filt_daemon:
                        filt_info = filt_daemon.get_info()
                    filt_uts = [ut for ut in self.current_exposure.ut_list
                                if ut in params.UTS_WITH_FILTERWHEELS]

                    # Check if we need to change the filters
                    if all(filt_info[ut]['current_filter'] == self.current_exposure.filt
                           for ut in filt_uts):
                        self.exposure_state = 'filters_set'
                    else:
                        self.log.info('Setting filter wheels to {}'.format(
                                      self.current_exposure.filt))
                        try:
                            with daemon_proxy('filt') as filt_daemon:
                                filt_dict = {ut: self.current_exposure.filt for ut in filt_uts}
                                filt_daemon.set_filters(filt_dict)
                                self.exposure_state = 'filters_setting'
                        except Exception:
                            self.log.error('No response from filter wheel daemon')
                            self.log.debug('', exc_info=True)

                elif self.exposure_state == 'filters_setting':
                    # Get the filter wheel info
                    with daemon_proxy('filt') as filt_daemon:
                        filt_info = filt_daemon.get_info()
                    filt_uts = [ut for ut in self.current_exposure.ut_list
                                if ut in params.UTS_WITH_FILTERWHEELS]

                    # Check if the filters are set
                    if all(filt_info[ut]['current_filter'] == self.current_exposure.filt
                           for ut in filt_uts):
                        self.log.info('Filter wheels set')
                        self.exposure_state = 'filters_set'

                elif self.exposure_state == 'filters_set':
                    # Ready to take the exposure
                    if not self.current_exposure.glance:
                        self.log.info('Starting {:.0f}s exposure'.format(
                            self.current_exposure.exptime))
                    else:
                        self.log.info('Starting {:.0f}s glance'.format(
                            self.current_exposure.exptime))
                    try:
                        with daemon_proxy('cam') as cam_daemon:
                            cam_daemon.take_exposure(self.current_exposure)
                            self.exposure_state = 'cameras_exposing'
                    except Exception:
                        self.log.error('No response from camera daemon')
                        self.log.debug('', exc_info=True)

                elif self.exposure_state == 'cameras_exposing':
                    # Get the camera info
                    with daemon_proxy('cam') as cam_daemon:
                        cam_exposing = cam_daemon.is_exposing()

                    # Check if the exposure has finished
                    if not cam_exposing:
                        self.log.info('Exposure complete')
                        self.current_exposure = None
                        self.exposure_state = 'none'
                        self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the heardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get internal info
        if self.paused:
            temp_info['status'] = 'Paused'
        elif self.current_exposure is not None:
            temp_info['status'] = 'Working'
        else:
            temp_info['status'] = 'Ready'
        temp_info['queue_length'] = len(self.exp_queue)
        if self.current_exposure is not None:
            temp_info['exposing'] = True
            current_info = {}
            current_info['ut_list'] = self.current_exposure.ut_list
            current_info['exptime'] = self.current_exposure.exptime
            current_info['filter'] = self.current_exposure.filt
            current_info['binning'] = self.current_exposure.binning
            current_info['frametype'] = self.current_exposure.frametype
            current_info['target'] = self.current_exposure.target
            current_info['imgtype'] = self.current_exposure.imgtype
            current_info['glance'] = self.current_exposure.glance
            current_info['set_num'] = self.current_exposure.set_num
            current_info['set_pos'] = self.current_exposure.set_pos
            current_info['set_tot'] = self.current_exposure.set_tot
            current_info['from_db'] = self.current_exposure.from_db
            current_info['db_id'] = self.current_exposure.db_id
            temp_info['current_exposure'] = current_info
        else:
            temp_info['exposing'] = False
            temp_info['current_exposure'] = None
        temp_info['latest_set_number'] = self.latest_set_number

        # Write debug log line
        try:
            now_str = '{} ({:.0f} in queue)'.format(temp_info['status'],
                                                    temp_info['queue_length'])
            if not self.info:
                self.log.debug('Exposure queue is {}'.format(now_str))
            else:
                old_str = '{} ({:.0f} in queue)'.format(self.info['status'],
                                                        self.info['queue_length'])
                if now_str != old_str:
                    self.log.debug('Exposure queue is {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    # Control functions
    def add(self, ut_list, exptime, nexp=1,
            filt=None, binning=1, frametype='normal',
            target='NA', imgtype='SCIENCE', glance=False,
            db_id=None):
        """Add exposures to the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for ut in ut_list:
            if ut not in params.UTS_WITH_CAMERAS:
                raise ValueError('Unit telescope ID not in list {}'.format(params.UTS_WITH_CAMERAS))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt and filt.upper() == 'X':
            filt = None
        if filt and filt.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list {}'.format(params.FILTER_LIST))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError('Frame type must be in {}'.format(params.FRAMETYPE_LIST))

        # Find and update set number
        with open(self.set_number_file, 'r') as f:
            old_set_number = int(f.read())
        new_set_number = old_set_number + 1
        with open(self.set_number_file, 'w') as f:
            f.write('{:d}'.format(new_set_number))
        self.latest_set_number = new_set_number

        # Call the command
        for i in range(1, nexp + 1):
            exposure = Exposure(ut_list,
                                exptime,
                                filt.upper() if filt else None,
                                binning,
                                frametype,
                                target.replace(';', ''),
                                imgtype.replace(';', '').upper(),
                                glance,
                                set_num=new_set_number,
                                set_pos=i,
                                set_tot=nexp,
                                db_id=db_id,
                                )
            self.exp_queue.append(exposure)
            if not glance:
                self.log.info('Added {:.0f}s {} exposure, now {:.0f} in queue'.format(
                              exptime, filt.upper() if filt else 'X', len(self.exp_queue)))
            else:
                self.log.info('Added {:.0f}s {} glance, now {:.0f} in queue'.format(
                              exptime, filt.upper() if filt else 'X', len(self.exp_queue)))

        # Format return string
        s = 'Added {}{:.0f}s {} {}{},'.format('{}x '.format(nexp) if nexp > 1 else '',
                                              exptime,
                                              filt.upper() if filt else 'X',
                                              'exposure' if not glance else 'glance',
                                              's' if nexp > 1 else '',
                                              )
        s += ' now {} items in queue'.format(len(self.exp_queue))
        if self.paused:
            s += ' [paused]'
        return s

    def clear(self):
        """Empty the exposure queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Call the command
        num_in_queue = len(self.exp_queue)
        self.exp_queue.clear()

        self.log.info('Cleared {} items from queue'.format(num_in_queue))
        return 'Queue cleared'

    def get(self):
        """Return info on exposures in the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Call the command
        queue_info = self.exp_queue.get()

        return queue_info

    def get_simple(self):
        """Return simple info on exposures in the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Call the command
        queue_info_simple = self.exp_queue.get_simple()

        return queue_info_simple

    def pause(self):
        """Pause the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if self.paused:
            return 'Queue already paused'

        # Set values
        self.paused = True

        self.log.info('Queue paused')
        return 'Queue paused'

    def resume(self):
        """Unpause the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if not self.paused:
            return 'Queue already resumed'

        # Set values
        self.paused = False

        self.log.info('Queue resumed')
        return 'Queue resumed'


if __name__ == '__main__':
    daemon_id = 'exq'
    with misc.make_pid_file(daemon_id):
        ExqDaemon()._run()
