#!/usr/bin/env python
"""Daemon to control filter wheels via the UT interface daemons."""

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

        # filt is dependent on all the interfaces
        for daemon_id in params.INTERFACES:
            self.dependencies.add(daemon_id)

        # command flags
        self.set_filter_flag = 0
        self.home_filter_flag = 0

        # filter wheel variables
        self.active_uts = []
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
                    for ut in self.active_uts:
                        interface_id = params.UT_INTERFACES[ut]
                        new_filter_num = params.FILTER_LIST.index(self.new_filter)

                        self.log.info('Moving filter wheel {} ({}) to {} ({})'.format(
                                      ut, interface_id, self.new_filter, new_filter_num))

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.set_filter_pos(new_filter_num, ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('set_filter command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.set_filter_flag = 0
                self.force_check_flag = True

            # home the filter
            if self.home_filter_flag:
                try:
                    for ut in self.active_uts:
                        interface_id = params.UT_INTERFACES[ut]

                        self.log.info('Homing filter wheel {} ({})'.format(
                                      ut, interface_id))

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.home_filter(ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('home_filter command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
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

        for ut in params.UTS:
            # Get info from each interface
            try:
                interface_id = params.UT_INTERFACES[ut]
                interface_info = {}
                interface_info['interface_id'] = interface_id

                with daemon_proxy(interface_id) as interface:
                    interface_info['remaining'] = interface.get_filter_steps_remaining(ut)
                    interface_info['current_filter_num'] = interface.get_filter_number(ut)
                    interface_info['current_pos'] = interface.get_filter_position(ut)
                    interface_info['serial_number'] = interface.get_filter_serial_number(ut)
                    interface_info['homed'] = interface.get_filter_homed(ut)

                if interface_info['remaining'] > 0:
                    interface_info['status'] = 'Moving'
                else:
                    interface_info['status'] = 'Ready'

                temp_info[ut] = interface_info
            except Exception:
                self.log.error('Failed to get filter wheel {} info'.format(ut))
                self.log.debug('', exc_info=True)
                temp_info[ut] = None

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(ut, temp_info[ut]['status'])
                        for ut in params.UTS]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Filter wheels are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(ut, self.info[ut]['status'])
                            for ut in params.UTS]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Filter wheels are {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    # Control functions
    def set_filter(self, new_filter, ut_list):
        """Move filter wheel to given filter."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if new_filter.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list {}'.format(params.FILTER_LIST))
        for ut in ut_list:
            if ut not in params.UTS:
                raise ValueError('Unit telescope ID not in list {}'.format(params.UTS))

        # Set values
        self.wait_for_info()
        for ut in ut_list:
            if self.info[ut]['remaining'] == 0 and self.info[ut]['homed']:
                self.active_uts += [ut]
        self.new_filter = new_filter

        # Set flag
        self.set_filter_flag = 1

        # Format return string
        s = 'Moving:'
        for ut in ut_list:
            hw_str = 'Filter Wheel {}'.format(ut)
            s += '\n  '
            if self.info[ut]['remaining'] > 0:
                s += misc.errortxt('"HardwareStatusError: {} is still moving"'.format(hw_str))
            elif not self.info[ut]['homed']:
                s += misc.errortxt('"HardwareStatusError: {} not homed"'.format(hw_str))
            else:
                s += 'Moving {}'.format(hw_str)
        return s

    def home_filter(self, ut_list):
        """Move filter wheel to home position."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for ut in ut_list:
            if ut not in params.UTS:
                raise ValueError('Unit telescope ID not in list {}'.format(params.UTS))

        # Set values
        self.wait_for_info()
        for ut in ut_list:
            if self.info[ut]['remaining'] == 0:
                self.active_uts += [ut]

        # Set flag
        self.home_filter_flag = 1

        # Format return string
        s = 'Moving:'
        for ut in ut_list:
            hw_str = 'Filter Wheel {}'.format(ut)
            s += '\n  '
            if self.info[ut]['remaining'] > 0:
                s += misc.errortxt('"HardwareStatusError: {} is still moving"'.format(hw_str))
            else:
                s += 'Homing {}'.format(hw_str)
        return s


if __name__ == "__main__":
    daemon_id = 'filt'
    with misc.make_pid_file(daemon_id):
        FiltDaemon()._run()
