#!/usr/bin/env python
"""Daemon to control FLI filter wheels via fli_interface."""

import threading
import time

from astropy.time import Time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon, daemon_proxy


class FiltDaemon(BaseDaemon):
    """Filter wheel hardware daemon class."""

    def __init__(self):
        super().__init__('filt')

        # filt is dependent on all the FLI interfaces
        for daemon_id in params.FLI_INTERFACES:
            self.dependencies.add(daemon_id)

        # command flags
        self.set_filter_flag = 0
        self.home_filter_flag = 0

        # filter wheel variables
        self.active_tel = []
        self.new_filter = ''

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

            # control functions
            # set the active filter
            if self.set_filter_flag:
                try:
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]
                        new_filter_num = params.FILTER_LIST.index(self.new_filter)

                        self.log.info('Moving filter wheel %i (%s-%i) to %s (%i)',
                                      tel, intf, hw, self.new_filter, new_filter_num)

                        try:
                            with daemon_proxy(intf) as fli:
                                c = fli.set_filter_pos(new_filter_num, hw)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('set_filter command failed')
                    self.log.debug('', exc_info=True)
                self.active_tel = []
                self.set_filter_flag = 0
                self.force_check_flag = True

            # home the filter
            if self.home_filter_flag:
                try:
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]

                        self.log.info('Homing filter wheel %i (%s-%i)',
                                      tel, intf, hw)

                        try:
                            with daemon_proxy(intf) as fli:
                                c = fli.home_filter(hw)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('home_filter command failed')
                    self.log.debug('', exc_info=True)
                self.active_tel = []
                self.home_filter_flag = 0
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

        for tel in params.TEL_DICT:
            # Get info from each interface
            try:
                intf, hw = params.TEL_DICT[tel]
                tel_info = {}
                tel_info['intf'] = intf
                tel_info['hw'] = hw

                with daemon_proxy(intf) as fli:
                    tel_info['remaining'] = fli.get_filter_steps_remaining(hw)
                    tel_info['current_filter_num'] = fli.get_filter_number(hw)
                    tel_info['current_pos'] = fli.get_filter_position(hw)
                    tel_info['serial_number'] = fli.get_filter_serial_number(hw)
                    tel_info['homed'] = fli.get_filter_homed(hw)

                if tel_info['remaining'] > 0:
                    tel_info['status'] = 'Moving'
                else:
                    tel_info['status'] = 'Ready'

                temp_info[tel] = tel_info
            except Exception:
                self.log.error('Failed to get filter wheel {} info'.format(tel))
                self.log.debug('', exc_info=True)
                temp_info[tel] = None

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(tel, temp_info[tel]['status'])
                        for tel in sorted(params.TEL_DICT)]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Filter wheels are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(tel, self.info[tel]['status'])
                            for tel in sorted(params.TEL_DICT)]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Filter wheels are {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    # Control functions
    def set_filter(self, new_filter, tel_list):
        """Move filter wheel to given filter."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if new_filter.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list %s' % str(params.FILTER_LIST))
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.wait_for_info()
        for tel in tel_list:
            if self.info[tel]['remaining'] == 0 and self.info[tel]['homed']:
                self.active_tel += [tel]
        self.new_filter = new_filter

        # Set flag
        self.set_filter_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            s += '\n  '
            if self.info[tel]['remaining'] > 0:
                s += misc.errortxt('"HardwareStatusError: Filter wheel %i is still moving"' % tel)
            elif not self.info[tel]['homed']:
                s += misc.errortxt('"HardwareStatusError: Filter wheel %i not homed"' % tel)
            else:
                s += 'Moving filter wheel %i' % tel
        return s

    def home_filter(self, tel_list):
        """Move filter wheel to home position."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.wait_for_info()
        for tel in tel_list:
            if self.info[tel]['remaining'] == 0:
                self.active_tel += [tel]

        # Set flag
        self.home_filter_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            s += '\n  '
            if self.info[tel]['remaining'] > 0:
                s += misc.errortxt('"HardwareStatusError: Filter wheel %i is still moving"' % tel)
            else:
                s += 'Homing filter wheel %i' % tel
        return s


if __name__ == "__main__":
    daemon_id = 'filt'
    with misc.make_pid_file(daemon_id):
        FiltDaemon()._run()
