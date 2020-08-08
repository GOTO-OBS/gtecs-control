#!/usr/bin/env python3
"""Daemon to control the exposure queue."""

import os
import threading
import time

from astropy.time import Time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon, daemon_proxy
from gtecs.exposures import Exposure, ExposureQueue


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
        self.exp_queue = ExposureQueue()
        self.current_exposure = None

        self.set_number_file = os.path.join(params.FILE_PATH, 'set_number')
        self.latest_set_number = 0

        self.working = 0
        self.paused = 1  # start paused

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
            # check the queue, take off the first entry (if not paused)
            queue_len = len(self.exp_queue)
            if (queue_len > 0) and not self.paused and not self.working:
                # OK - time to add a new exposure
                self.log.info('Taking exposure')
                self.working = 1
                self.current_exposure = self.exp_queue.pop(0)

                # set the filter, if needed
                if self._need_to_change_filter():
                    try:
                        self._set_filter()
                    except Exception:
                        self.log.error('set_filter command failed')
                        self.log.debug('', exc_info=True)
                    # sleep briefly, to make sure the filter wheel has stopped
                    time.sleep(0.5)
                else:
                    self.log.info('No need to move filter wheel')

                # take the image
                try:
                    self._take_image()
                except Exception:
                    self.log.error('take_image command failed')
                    self.log.debug('', exc_info=True)

                # done!
                self.working = 0
                self.current_exposure = None
                self.force_check_flag = True

            elif queue_len == 0 or self.paused:
                # either we are paused, or nothing in the queue
                time.sleep(1.0)

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
        elif self.working:
            temp_info['status'] = 'Working'
        else:
            temp_info['status'] = 'Ready'
        temp_info['queue_length'] = len(self.exp_queue)
        if self.current_exposure is not None:
            temp_info['exposing'] = True
            temp_info['current_ut_list'] = self.current_exposure.ut_list
            temp_info['current_exptime'] = self.current_exposure.exptime
            temp_info['current_filter'] = self.current_exposure.filt
            temp_info['current_binning'] = self.current_exposure.binning
            temp_info['current_frametype'] = self.current_exposure.frametype
            temp_info['current_target'] = self.current_exposure.target
            temp_info['current_imgtype'] = self.current_exposure.imgtype
        else:
            temp_info['exposing'] = False
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

    def _need_to_change_filter(self):
        new_filt = self.current_exposure.filt
        if new_filt is None:
            # filter doesn't matter, e.g. dark
            return False

        ut_list = [ut for ut in self.current_exposure.ut_list
                   if ut in params.UTS_WITH_FILTERWHEELS]
        with daemon_proxy('filt') as filt_daemon:
            filt_info = filt_daemon.get_info()
        homed_check = [filt_info[ut]['homed'] for ut in ut_list]
        if not all(homed_check):
            self.log.info('Need to home filter wheels')
            self._home_filter_wheels()

        check = [params.FILTER_LIST[filt_info[ut]['current_filter_num']] == new_filt
                 for ut in ut_list]
        if all(check):
            return False
        else:
            return True

    def _home_filter_wheels(self):
        self.log.info('Homing filter wheels')
        ut_list = [ut for ut in self.current_exposure.ut_list
                   if ut in params.UTS_WITH_FILTERWHEELS]
        try:
            with daemon_proxy('filt') as filt_daemon:
                filt_daemon.home_filters(ut_list)
        except Exception:
            self.log.error('No response from filter wheel daemon')
            self.log.debug('', exc_info=True)

        self._get_info()
        time.sleep(3)
        with daemon_proxy('filt') as filt_daemon:
            filt_info = filt_daemon.get_info()
        homed_check = [filt_info[ut]['homed'] for ut in ut_list]
        while not all(homed_check):
            with daemon_proxy('filt') as filt_daemon:
                filt_info = filt_daemon.get_info()
            homed_check = [filt_info[ut]['homed'] for ut in ut_list]
            time.sleep(0.5)

            # keep ping alive
            self.loop_time = time.time()
            self._get_info()
        self.log.info('Filter wheels homed')

    def _set_filter(self):
        new_filt = self.current_exposure.filt
        ut_list = [ut for ut in self.current_exposure.ut_list
                   if ut in params.UTS_WITH_FILTERWHEELS]
        self.log.info('Setting filter to {} on {!r}'.format(new_filt, ut_list))
        filt_dict = {ut: new_filt for ut in ut_list}
        try:
            with daemon_proxy('filt') as filt_daemon:
                filt_daemon.set_filters(filt_dict)
        except Exception:
            self.log.error('No response from filter wheel daemon')
            self.log.debug('', exc_info=True)

        self._get_info()
        time.sleep(3)
        with daemon_proxy('filt') as filt_daemon:
            filt_info = filt_daemon.get_info()
        check = [params.FILTER_LIST[filt_info[ut]['current_filter_num']] == new_filt
                 for ut in ut_list]
        while not all(check):
            with daemon_proxy('filt') as filt_daemon:
                filt_info = filt_daemon.get_info()
            check = [params.FILTER_LIST[filt_info[ut]['current_filter_num']] == new_filt
                     for ut in ut_list]
            time.sleep(0.5)

            # keep ping alive
            self.loop_time = time.time()
            self._get_info()
        self.log.info('Filter wheel move complete, now at {}'.format(new_filt))

    def _take_image(self):
        exptime = self.current_exposure.exptime
        binning = self.current_exposure.binning
        frametype = self.current_exposure.frametype
        ut_list = [ut for ut in self.current_exposure.ut_list
                   if ut in params.UTS_WITH_CAMERAS]
        glance = self.current_exposure.glance
        if not glance:
            self.log.info('Taking exposure ({:.0f}s, {:.0f}x{:.0f}, {}) on {!r}'.format(
                          exptime, binning, binning, frametype, ut_list))
        else:
            self.log.info('Taking glance ({:.0f}s, {:.0f}x{:.0f}, {}) on {!r}'.format(
                          exptime, binning, binning, frametype, ut_list))
        try:
            with daemon_proxy('cam') as cam_daemon:
                cam_daemon.take_exposure(self.current_exposure)
        except Exception:
            self.log.error('No response from camera daemon')
            self.log.debug('', exc_info=True)

        self._get_info()
        time.sleep(3)
        with daemon_proxy('cam') as cam_daemon:
            cam_exposing = cam_daemon.is_exposing()
        while cam_exposing:
            with daemon_proxy('cam') as cam_daemon:
                cam_exposing = cam_daemon.is_exposing()

            time.sleep(0.5)
            # keep main thread alive
            self.loop_time = time.time()
            self._get_info()
        self.log.info('Camera exposure complete')

    # Control functions
    def add(self, ut_list, exptime,
            filt=None, binning=1, frametype='normal',
            target='NA', imgtype='SCIENCE', glance=False):
        """Add an exposure to the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for ut in ut_list:
            if ut not in params.UTS_WITH_CAMERAS:
                raise ValueError('Unit telescope ID not in list {}'.format(params.UTS_WITH_CAMERAS))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt and filt.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list {}'.format(params.FILTER_LIST))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError('Frame type must be in {}'.format(params.FRAMETYPE_LIST))

        # Find and update set number
        with open(self.set_number_file, 'r') as f:
            set_number = int(f.read())
        self.latest_set_number = set_number + 1
        with open(self.set_number_file, 'w') as f:
            f.write('{:07d}'.format(self.latest_set_number))

        # Call the command
        exposure = Exposure(ut_list,
                            exptime,
                            filt.upper() if filt else None,
                            binning,
                            frametype,
                            target.replace(';', ''),
                            imgtype.replace(';', ''),
                            glance,
                            set_num=self.latest_set_number,
                            set_pos=1,
                            set_tot=1,
                            )
        self.exp_queue.append(exposure)
        if not glance:
            self.log.info('Added {:.0f}s {} exposure, now {:.0f} in queue'.format(
                          exptime, filt.upper() if filt else 'X', len(self.exp_queue)))
        else:
            self.log.info('Added {:.0f}s {} glance, now {:.0f} in queue'.format(
                          exptime, filt.upper() if filt else 'X', len(self.exp_queue)))

        # Format return string
        if not glance:
            s = 'Added {:.0f}s {} exposure,'.format(exptime, filt.upper() if filt else 'X')
        else:
            s = 'Added {:.0f}s {} glance,'.format(exptime, filt.upper() if filt else 'X')
        s += ' now {} items in queue'.format(len(self.exp_queue))
        if self.paused:
            s += ' [paused]'
        return s

    def add_multi(self, nexp, ut_list, exptime,
                  filt=None, binning=1, frametype='normal',
                  target='NA', imgtype='SCIENCE',
                  db_id=None):
        """Add multiple exposures to the queue as a set."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for ut in ut_list:
            if ut not in params.UTS_WITH_CAMERAS:
                raise ValueError('Unit telescope ID not in list {}'.format(params.UTS_WITH_CAMERAS))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt and filt.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list {}'.format(params.FILTER_LIST))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError('Frame type must be in {}'.format(params.FRAMETYPE_LIST))

        # Find and update set number
        with open(self.set_number_file, 'r') as f:
            set_number = int(f.read())
        self.latest_set_number = set_number + 1
        with open(self.set_number_file, 'w') as f:
            f.write('{:07d}'.format(self.latest_set_number))

        # Call the command
        for i in range(1, nexp + 1):
            exposure = Exposure(ut_list,
                                exptime,
                                filt.upper() if filt else None,
                                binning, frametype,
                                target.replace(';', ''),
                                imgtype.replace(';', ''),
                                glance=False,
                                set_num=self.latest_set_number,
                                set_pos=i,
                                set_tot=nexp,
                                db_id=db_id,
                                )
            self.exp_queue.append(exposure)
            self.log.info('Added {:.0f}s {} exposure, now {:.0f} in queue'.format(
                          exptime, filt.upper() if filt else 'X', len(self.exp_queue)))

        # Format return string
        s = 'Added {}x {:.0f}s {} exposure(s),'.format(nexp, exptime,
                                                       filt.upper() if filt else 'X')
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
        self.paused = 1

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
        self.paused = 0

        self.log.info('Queue resumed')
        return 'Queue resumed'


if __name__ == '__main__':
    daemon_id = 'exq'
    with misc.make_pid_file(daemon_id):
        ExqDaemon()._run()
