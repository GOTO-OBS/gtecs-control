#!/usr/bin/env python3
"""Daemon to control the exposure queue."""

import os
import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, DaemonDependencyError, daemon_proxy
from gtecs.control.exposures import Exposure, ExposureQueue


class ExqDaemon(BaseDaemon):
    """Exposure queue hardware daemon class."""

    def __init__(self):
        super().__init__('exq')

        # exposure queue variables
        self.uts = params.UTS_WITH_CAMERAS.copy()
        self.paused = True  # start paused
        self.exp_queue = ExposureQueue()
        self.current_exposure = None
        self.exposure_state = 'none'

        # dithering
        self.dithering_enabled = params.EXQ_DITHERING  # TODO: should be per exposure, also in db
        self.dither_pattern = [('N', 1.00),  # TODO: should be in params
                               ('E', 1.32),
                               ('S', 1.54),
                               ('W', 1.61),
                               ('N', 1.21),
                               ('E', 1.22),
                               ]
        self.dithering = False
        self.dither_time = 0
        self.dither_delay = params.EXQ_DITHER_DELAY

        self.set_number_file = os.path.join(params.FILE_PATH, 'set_number')
        if not os.path.exists(self.set_number_file):
            with open(self.set_number_file, 'w') as f:
                f.write('0')
                f.close()
        with open(self.set_number_file, 'r') as f:
            self.latest_set_number = int(f.read())

        # dependencies
        self.dependencies.add('cam')
        self.dependencies.add('filt')
        if self.dithering_enabled:
            self.dependencies.add('mnt')

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')
        self.check_period = params.DAEMON_CHECK_PERIOD
        self.check_time = 0
        self.force_check_flag = True

        while self.running:
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
            # only do anything if we're not paused (and we're not in the middle of an exposure)
            if (not self.paused) or (self.exposure_state != 'none'):
                # If we're not currently doing anything and there are exposures in the
                # queue then pop off the first one and make that the current
                if self.current_exposure is None and len(self.exp_queue) > 0:
                    self.current_exposure = self.exp_queue.pop(0)
                    setstr = self.current_exposure.setstr.capitalize()
                    self.log.info('{}: Beginning new exposure'.format(setstr))
                    self.log.debug('{}: {}'.format(
                        setstr, self.current_exposure.as_line().strip()))
                    self.exposure_state = 'init'  # continue to state 1

                # Exposure state machine
                if self.exposure_state == 'init':
                    # STATE 1: Start the mount dithering (if required)
                    if self.dithering_enabled and self.current_exposure.frametype != 'dark':
                        try:
                            with daemon_proxy('mnt', timeout=10) as daemon:
                                info = daemon.get_info(force_update=True)

                            # Check if the mount can move
                            if info['status'] in ['Parked', 'Stopped',
                                                  'IN BLINKY MODE', 'MOTORS OFF']:
                                self.log.warning(
                                    '{}: Cannot move mount ({}), skipping dither'.format(
                                    setstr, info['status']))
                                self.dithering = False
                            elif self.current_exposure.set_pos == 1:
                                # If it's the start of a new set then make sure we're in position
                                # If we give no coordinates it will slew to the current target,
                                # which will reset any offsets from previous dithers
                                # However, we only want to do this if we've been dithering
                                # since the last slew command. So we check the last move type first.
                                if info['last_move_type'] == 'guide':
                                    msg = f'{setstr}: Recentring mount on target position'
                                    self.log.info(msg)
                                    with daemon_proxy('mnt') as daemon:
                                        daemon.slew(coords=None)
                                    self.dither_time = self.loop_time
                                    self.dithering = True
                            else:
                                # For subsequent exposures in a set, offset the mount slightly
                                # using pulse guiding
                                i = (self.current_exposure.set_pos - 2) % len(self.dither_pattern)
                                direction = self.dither_pattern[i][0]
                                duration = self.dither_pattern[i][1]
                                msg = f'{setstr}: Offsetting the mount {duration:.2f}s {direction}'
                                self.log.info(msg)
                                with daemon_proxy('mnt') as daemon:
                                    daemon.pulse_guide(direction, duration * 1000)
                                self.dither_time = self.loop_time
                                self.dithering = True
                            self.exposure_state = 'mount_dithering'  # continue to state 2
                        except Exception:
                            self.log.error('Error connecting to mount daemon')
                            self.log.debug('', exc_info=True)
                            self.dithering = False
                    else:
                        self.dithering = False
                        self.exposure_state = 'mount_dithering'  # continue to state 2

                if self.exposure_state == 'mount_dithering':
                    # STATE 2: Home the filter wheels (if required)
                    if self.current_exposure.filt is not None:
                        try:
                            with daemon_proxy('filt') as daemon:
                                info = daemon.get_info(force_update=False)
                            # exclude uts without filter wheels
                            filt_uts = [ut for ut in self.current_exposure.uts if ut in info]

                            # Check if we need to home the filters
                            if all(info[ut]['homed'] for ut in filt_uts):
                                self.exposure_state = 'filters_homed'  # skip to state 4
                            else:
                                self.log.info('{}: Homing filter wheels'.format(setstr))
                                with daemon_proxy('filt') as daemon:
                                    daemon.home_filters(filt_uts)
                                self.exposure_state = 'filters_homing'  # continue to state 3
                        except Exception:
                            self.log.error('Error connecting to filter wheel daemon')
                            self.log.debug('', exc_info=True)
                    else:
                        # Filter doesn't matter, e.g. dark, so skip to state 6
                        self.exposure_state = 'filters_set'

                if self.exposure_state == 'filters_homing':
                    # STATE 3: Wait for filter wheels to finish homing
                    try:
                        with daemon_proxy('filt', timeout=10) as daemon:
                            info = daemon.get_info(force_update=True)
                        filt_uts = [ut for ut in self.current_exposure.uts if ut in info]

                        # Continue when all the filters are homed
                        if all(info[ut]['homed'] for ut in filt_uts):
                            self.log.info('{}: Filter wheels homed'.format(setstr))
                            self.exposure_state = 'filters_homed'  # continue to state 4
                    except Exception:
                        self.log.error('Error connecting to filter wheel daemon')
                        self.log.debug('', exc_info=True)

                if self.exposure_state == 'filters_homed':
                    # STATE 4: Change filter (if required)
                    try:
                        with daemon_proxy('filt') as daemon:
                            info = daemon.get_info(force_update=False)
                        filt_uts = [ut for ut in self.current_exposure.uts if ut in info]

                        # Check if we need to change the filters
                        if all(info[ut]['current_filter'] == self.current_exposure.filt
                               for ut in filt_uts):
                            self.exposure_state = 'filters_set'  # skip to state 6
                        else:
                            self.log.info('{}: Setting filter wheels to {}'.format(
                                          setstr, self.current_exposure.filt))
                            with daemon_proxy('filt') as daemon:
                                filt_dict = {ut: self.current_exposure.filt for ut in filt_uts}
                                daemon.set_filters(filt_dict)
                            self.exposure_state = 'filters_setting'  # continue to state 5
                    except Exception:
                        self.log.error('Error connecting to filter wheel daemon')
                        self.log.debug('', exc_info=True)

                if self.exposure_state == 'filters_setting':
                    # STATE 5: Wait for filter wheels to finish moving
                    try:
                        with daemon_proxy('filt', timeout=10) as daemon:
                            info = daemon.get_info(force_update=True)
                        filt_uts = [ut for ut in self.current_exposure.uts if ut in info]

                        # Continue when the filters are set
                        if all(info[ut]['current_filter'] == self.current_exposure.filt
                               for ut in filt_uts):
                            self.log.info('{}: Filter wheels set'.format(setstr))
                            self.exposure_state = 'filters_set'  # continue to state 6
                    except Exception:
                        self.log.error('Error connecting to filter wheel daemon')
                        self.log.debug('', exc_info=True)

                if self.exposure_state == 'filters_set':
                    # STATE 6: Wait for the mount to finish dithering
                    if self.dithering is True:
                        try:
                            with daemon_proxy('mnt', timeout=10) as daemon:
                                info = daemon.get_info(force_update=True)

                            # Continue when the mount is tracking, and the last move was after the
                            # dithering command (otherwise the status doesn't change fast enough).
                            # We also add a delay since the mount tracking status can be
                            # set too early before it's properly settled.
                            if (info['status'] == 'Tracking' and
                                    'last_move_time' in info and
                                    info['last_move_time'] > self.dither_time and
                                    self.loop_time > self.dither_time + self.dither_delay):
                                self.log.info('{}: Mount tracking'.format(setstr))
                                self.dithering = False
                                self.exposure_state = 'mount_tracking'  # continue to state 7
                        except Exception:
                            self.log.error('Error connecting to mount daemon')
                            self.log.debug('', exc_info=True)
                    else:
                        self.exposure_state = 'mount_tracking'  # continue to state 7

                if self.exposure_state == 'mount_tracking':
                    # STATE 7: Start the exposure
                    if not self.current_exposure.glance:
                        self.log.info('{}: Starting {:.0f}s exposure'.format(
                            setstr, self.current_exposure.exptime))
                    else:
                        self.log.info('{}: Starting {:.0f}s glance'.format(
                            setstr, self.current_exposure.exptime))
                    try:
                        with daemon_proxy('cam') as daemon:
                            daemon.take_exposure(self.current_exposure)
                            self.exposure_state = 'cameras_exposing'  # continue to state 8
                    except Exception:
                        self.log.error('Error connecting to camera daemon')
                        self.log.debug('', exc_info=True)

                if self.exposure_state == 'cameras_exposing':
                    # STATE 8: Wait for the exposure to finish
                    try:
                        with daemon_proxy('cam') as daemon:
                            cam_exposing = daemon.is_exposing()

                        # Continue if the exposure has finished
                        if not cam_exposing:
                            self.log.info('{}: Exposure complete'.format(setstr))
                            self.current_exposure = None
                            self.exposure_state = 'none'  # return to start
                            self.force_check_flag = True
                    except Exception:
                        self.log.error('Error connecting to camera daemon')
                        self.log.debug('', exc_info=True)

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the hardware."""
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
            current_info['exptime'] = self.current_exposure.exptime
            current_info['filter'] = self.current_exposure.filt
            current_info['binning'] = self.current_exposure.binning
            current_info['frametype'] = self.current_exposure.frametype
            current_info['target'] = self.current_exposure.target
            current_info['imgtype'] = self.current_exposure.imgtype
            current_info['glance'] = self.current_exposure.glance
            current_info['uts'] = self.current_exposure.uts
            current_info['set_num'] = self.current_exposure.set_num
            current_info['set_pos'] = self.current_exposure.set_pos
            current_info['set_tot'] = self.current_exposure.set_tot
            current_info['set_id'] = self.current_exposure.set_id
            current_info['pointing_id'] = self.current_exposure.pointing_id
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
    def add(self, exptime, nexp=1, filt=None, binning=1, frametype='normal',
            target='NA', imgtype='SCIENCE', glance=False, uts=None,
            set_id=None, pointing_id=None):
        """Add exposures to the queue."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt == 'X':
            filt = None
        if filt is not None:
            # We could check all UTs and raise an error if the filter isn't in its list.
            # Instead we'll just quietly remove it from the exposure.
            # When we set we'll move the filter wheels to that filter, while any static ones
            # will only be included here if the filter is the one we're asking for.
            uts = [ut for ut in uts if filt in params.UT_DICT[ut]['FILTERS']]
            if len(uts) == 0:
                raise ValueError('Unknown filter: {}'.format(filt))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in ['normal', 'dark']:
            raise ValueError('Invalid frame type: "{}"'.format(frametype))
        if uts is None:
            uts = self.uts.copy()
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')

        # Find and update set number
        with open(self.set_number_file, 'r') as f:
            old_set_number = int(f.read())
        new_set_number = old_set_number + 1
        with open(self.set_number_file, 'w') as f:
            f.write('{:d}'.format(new_set_number))
        self.latest_set_number = new_set_number

        self.log.info('Adding new exposure set s{:07d}'.format(new_set_number))
        for i in range(1, nexp + 1):
            exposure = Exposure(exptime,
                                filt,
                                binning,
                                frametype,
                                target.replace(';', ''),
                                imgtype.replace(';', '').upper(),
                                glance,
                                uts,
                                set_num=new_set_number,
                                set_pos=i,
                                set_tot=nexp,
                                set_id=set_id,
                                pointing_id=pointing_id,
                                )
            self.exp_queue.append(exposure)
            if not glance:
                self.log.info('Added {:.0f}s {} exposure, now {:.0f} in queue'.format(
                              exptime, filt if filt is not None else 'X', len(self.exp_queue)))
            else:
                self.log.info('Added {:.0f}s {} glance, now {:.0f} in queue'.format(
                              exptime, filt if filt is not None else 'X', len(self.exp_queue)))

    def clear(self):
        """Empty the exposure queue."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')

        queue_length = len(self.exp_queue)
        self.log.info(f'Clearing {queue_length:.0f} items from queue')
        self.exp_queue.clear()
        return queue_length

    def get(self):
        """Fetch info on exposures in the queue."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')

        return self.exp_queue.get()

    def get_simple(self):
        """Fetch simple info on exposures in the queue."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')

        return self.exp_queue.get_simple()

    def pause(self):
        """Pause the queue."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')

        if not self.paused:
            self.log.info('Pausing queue')
            self.paused = True

    def resume(self):
        """Unpause the queue."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')

        if self.paused:
            self.log.info('Resuming queue')
            self.paused = False

    def switch_dithering(self, command):
        """Enable or disable dithering between images."""
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        if command == 'on' and self.dithering_enabled is False:
            self.log.info('Enabling dithering')
            self.dithering_enabled = True
            self.dependencies.add('mnt')
        elif command == 'off' and self.dithering_enabled is True:
            self.log.info('Disabling dithering')
            self.dithering_enabled = False
            self.dependencies.discard('mnt')

    # Info function
    def get_info_string(self, verbose=False, force_update=False):
        """Get a string for printing status info."""
        info = self.get_info(force_update)
        if not verbose:
            msg = 'QUEUE: [{}]\n'.format(info['status'])
            msg += '  Current exposure: '
            current_exposure = info['current_exposure']
            if current_exposure is not None:
                msg += '   {}, {:.2f}, {}, {}, {}, {}, {}\n'.format(
                    current_exposure['uts'],
                    current_exposure['exptime'],
                    current_exposure['filter'],
                    current_exposure['binning'],
                    current_exposure['frametype'],
                    current_exposure['target'],
                    current_exposure['imgtype'])
                msg += '  Other items in queue: {}\n'.format(info['queue_length'])
            else:
                msg += '   None\n'
                msg += '  Items in queue: {}'.format(info['queue_length'])
        else:
            msg = '####### QUEUE INFO #######\n'
            msg += 'Status: {}\n'.format(info['status'])
            msg += '~~~~~~~\n'
            msg += 'Current exposure:\n'
            current_exposure = info['current_exposure']
            if current_exposure is not None:
                msg += '   {}, {:.2f}, {}, {}, {}, {}, {}\n'.format(
                    current_exposure['uts'],
                    current_exposure['exptime'],
                    current_exposure['filter'],
                    current_exposure['binning'],
                    current_exposure['frametype'],
                    current_exposure['target'],
                    current_exposure['imgtype'])
                msg += 'Other items in queue:     {}\n'.format(info['queue_length'])
            else:
                msg += '   None\n'
                msg += 'Items in queue:     {}\n'.format(info['queue_length'])
            msg += 'Latest set number:  {:d}\n'.format(info['latest_set_number'])
            msg += '~~~~~~~\n'
            msg += 'Uptime: {:.1f}s\n'.format(info['uptime'])
            msg += 'Timestamp: {}\n'.format(info['timestamp'])
            msg += '###########################'
        return msg


if __name__ == '__main__':
    daemon = ExqDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
