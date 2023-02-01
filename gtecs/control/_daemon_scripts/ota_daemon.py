#!/usr/bin/env python3
"""Daemon to control OTA hardware (e.g. mirror covers)."""

import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import errors
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, daemon_proxy
from gtecs.control.style import errortxt


class OTADaemon(BaseDaemon):
    """OTA hardware daemon class."""

    def __init__(self):
        super().__init__('ota')

        # command flags
        self.open_cover_flag = 0
        self.close_cover_flag = 0
        self.stop_cover_flag = 0

        # OTA variables
        self.uts = params.UTS.copy()
        self.uts_with_covers = params.UTS_WITH_COVERS.copy()
        self.active_uts = []
        self.interfaces = {f'foc{ut}' for ut in self.uts_with_covers}

        self.last_move_time = {ut: None for ut in self.uts}

        # dependencies
        for interface_id in self.interfaces:
            self.dependencies.add(interface_id)

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

            # control functions
            # open the mirror cover
            if self.open_cover_flag:
                try:
                    for ut in self.active_uts:
                        self.log.info('Opening mirror cover {}'.format(ut))
                        try:
                            with daemon_proxy(f'foc{ut}') as interface:
                                c = interface.open_cover()
                                if c:
                                    self.log.info(c)
                            self.last_move_time[ut] = self.loop_time
                        except Exception:
                            self.log.error('No response from interface foc{}'.format(ut))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('open_cover command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.open_cover_flag = 0
                self.force_check_flag = True

            # close the mirror cover
            if self.close_cover_flag:
                try:
                    for ut in self.active_uts:
                        self.log.info('Closing mirror cover {}'.format(ut))
                        try:
                            with daemon_proxy(f'foc{ut}') as interface:
                                c = interface.close_cover()
                                if c:
                                    self.log.info(c)
                            self.last_move_time[ut] = self.loop_time
                        except Exception:
                            self.log.error('No response from interface foc{}'.format(ut))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('close_cover command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.close_cover_flag = 0
                self.force_check_flag = True

            # stop the mirror cover
            if self.stop_cover_flag:
                try:
                    for ut in self.active_uts:
                        self.log.info('Stopping mirror cover {}'.format(ut))
                        try:
                            with daemon_proxy(f'foc{ut}') as interface:
                                c = interface.stop_cover()
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface foc{}'.format(ut))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('stop_cover command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.stop_cover_flag = 0
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from each UT
        temp_info['uts'] = self.uts.copy()
        temp_info['uts_with_covers'] = self.uts_with_covers.copy()
        for ut in self.uts:
            try:
                ut_info = {}
                ut_info['interface_id'] = f'foc{ut}'
                if ut in self.uts_with_covers:
                    with daemon_proxy(f'foc{ut}') as interface:
                        ut_info['position'] = interface.get_cover_position()
                        # See `H400.get_cover_position`
                else:
                    ut_info['position'] = 'NA'
                ut_info['serial_number'] = params.UT_DICT[ut]['OTA']['SERIAL']
                ut_info['hw_class'] = params.UT_DICT[ut]['OTA']['CLASS']
                ut_info['last_move_time'] = self.last_move_time[ut]

                temp_info[ut] = ut_info
            except Exception:
                self.log.error('Failed to get OTA {} info'.format(ut))
                self.log.debug('', exc_info=True)
                temp_info[ut] = None

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(ut, temp_info[ut]['position'])
                        for ut in self.uts_with_covers]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Mirror covers are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(ut, self.info[ut]['position'])
                            for ut in self.uts_with_covers]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Mirror covers are {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    # Control functions
    def open_covers(self, ut_list=None):
        """Open the mirror covers."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if ut_list is None:
            ut_list = self.uts_with_covers.copy()

        self.wait_for_info()
        retstrs = []
        for ut in sorted(ut_list):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('OTA {}: '.format(ut) + errortxt(s))
                continue

            # Check the UT has a mirror cover
            if ut not in self.uts_with_covers:
                s = 'Unit telescope {} does not have a mirror cover'.format(ut)
                retstrs.append('OTA {}: '.format(ut) + errortxt(s))
                continue

            # Check the mirror cover is not already moving
            # TODO: We can't do that, but does it matter?

            # Set values
            self.active_uts += [ut]
            s = 'OTA {}: Opening mirror cover'.format(ut)
            retstrs.append(s)

        # Set flag
        self.open_cover_flag = 1

        # Format return string
        return '\n'.join(retstrs)

    def close_covers(self, ut_list=None):
        """Close the mirror covers."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if ut_list is None:
            ut_list = self.uts_with_covers.copy()

        self.wait_for_info()
        retstrs = []
        for ut in sorted(ut_list):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('OTA {}: '.format(ut) + errortxt(s))
                continue

            # Check the UT has a mirror cover
            if ut not in self.uts_with_covers:
                s = 'Unit telescope {} does not have a mirror cover'.format(ut)
                retstrs.append('OTA {}: '.format(ut) + errortxt(s))
                continue

            # Check the mirror cover is not already moving
            # TODO: We can't do that, but does it matter?

            # Set values
            self.active_uts += [ut]
            s = 'OTA {}: Closing mirror cover'.format(ut)
            retstrs.append(s)

        # Set flag
        self.close_cover_flag = 1

        # Format return string
        return '\n'.join(retstrs)

    def stop_covers(self, ut_list=None):
        """Stop the mirror covers moving."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if ut_list is None:
            ut_list = self.uts_with_covers.copy()

        self.wait_for_info()
        retstrs = []
        for ut in sorted(ut_list):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('OTA {}: '.format(ut) + errortxt(s))
                continue

            # Check the UT has a mirror cover
            if ut not in self.uts_with_covers:
                s = 'Unit telescope {} does not have a mirror cover'.format(ut)
                retstrs.append('OTA {}: '.format(ut) + errortxt(s))
                continue

            # Check if the mirror cover is moving
            # TODO: We can't do that, but does it matter?

            # Set values
            self.active_uts += [ut]
            s = 'OTA {}: Stopping mirror cover'.format(ut)
            retstrs.append(s)

        # Set flag
        self.stop_cover_flag = 1

        # Format return string
        return '\n'.join(retstrs)


if __name__ == '__main__':
    daemon = OTADaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
