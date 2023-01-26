#!/usr/bin/env python3
"""Daemon to control filter wheels via the UT interface daemons."""

import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import errors
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, daemon_proxy
from gtecs.control.style import errortxt


class FiltDaemon(BaseDaemon):
    """Filter wheel hardware daemon class."""

    def __init__(self):
        super().__init__('filt')

        # filt is dependent on all the interfaces
        for interface_id in params.INTERFACES:
            self.dependencies.add(interface_id)

        # command flags
        self.set_filter_flag = 0
        self.home_filter_flag = 0

        # filter wheel variables
        self.uts = params.UTS_WITH_FILTERWHEELS.copy()
        self.active_uts = []
        self.new_filter = {ut: '' for ut in self.uts}
        self.filters = {ut: params.UT_DICT[ut]['FILTERS'] for ut in self.uts}

        self.last_move_time = {ut: None for ut in self.uts}

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

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
            # set the active filter
            if self.set_filter_flag:
                try:
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        new_filter_num = self.filters[ut].index(self.new_filter[ut])

                        self.log.info('Moving filter wheel {} ({}) to {} ({})'.format(
                                      ut, interface_id, self.new_filter[ut], new_filter_num))

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.set_filter_pos(new_filter_num, ut)
                                if c:
                                    self.log.info(c)
                            self.last_move_time[ut] = self.loop_time
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
                        interface_id = params.UT_DICT[ut]['INTERFACE']

                        self.log.info('Homing filter wheel {} ({})'.format(
                                      ut, interface_id))

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.home_filter(ut)
                                if c:
                                    self.log.info(c)
                            self.last_move_time[ut] = self.loop_time
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
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from each UT
        temp_info['uts'] = self.uts.copy()
        for ut in self.uts:
            try:
                ut_info = {}
                interface_id = params.UT_DICT[ut]['INTERFACE']
                ut_info['interface_id'] = interface_id

                with daemon_proxy(interface_id) as interface:
                    ut_info['serial_number'] = interface.get_filter_serial_number(ut)
                    ut_info['hw_class'] = interface.get_filter_class(ut)
                    ut_info['remaining'] = interface.get_filter_steps_remaining(ut)
                    ut_info['current_filter_num'] = interface.get_filter_number(ut)
                    ut_info['current_filter'] = self.filters[ut][ut_info['current_filter_num']]
                    ut_info['current_pos'] = interface.get_filter_position(ut)
                    ut_info['homed'] = interface.get_filter_homed(ut)

                if ut_info['remaining'] > 0:
                    ut_info['status'] = 'Moving'
                else:
                    ut_info['status'] = 'Ready'

                ut_info['filters'] = self.filters[ut]
                ut_info['last_move_time'] = self.last_move_time[ut]

                temp_info[ut] = ut_info
            except Exception:
                self.log.error('Failed to get filter wheel {} info'.format(ut))
                self.log.debug('', exc_info=True)
                temp_info[ut] = None

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(ut, temp_info[ut]['status'])
                        for ut in self.uts]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Filter wheels are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(ut, self.info[ut]['status'])
                            for ut in self.uts]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Filter wheels are {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    # Control functions
    def set_filters(self, new_filter):
        """Move filter wheel(s) to given filter(s)."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if not isinstance(new_filter, dict):
            new_filter = {ut: new_filter for ut in self.uts}

        self.wait_for_info()
        retstrs = []
        for ut in sorted(new_filter):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('Filter Wheel {}: '.format(ut) + errortxt(s))
                continue

            # Check the new filter is a valid input
            try:
                new_filt = new_filter[ut]
            except Exception:
                s = '"{}" is not a valid filter'.format(new_filter[ut])
                retstrs.append('Filter Wheel {}: '.format(ut) + errortxt(s))
                continue

            # Check the new filter is in the filter list
            if new_filt not in self.filters[ut]:
                s = 'New filter "{}" not in list ({})'.format(new_filt, ','.join(self.filters[ut]))
                retstrs.append('Filter Wheel {}: '.format(ut) + errortxt(s))
                continue

            # Check the new filter is different from the current filter
            # if new_filt == self.info[ut]['current_filter']:
            #     s = 'Filter Wheel is already at position {}'.format(new_filt)
            #     retstrs.append('Filter Wheel {}: '.format(ut) + errortxt(s))
            #     continue

            # Check the filter wheel is not already moving
            if self.info[ut]['remaining'] > 0:
                s = 'Filter Wheel is already moving'
                retstrs.append('Filter Wheel {}: '.format(ut) + errortxt(s))
                continue

            # Check the filter wheel is homed
            if not self.info[ut]['homed']:
                s = 'Filter Wheel is not homed'
                retstrs.append('Filter Wheel {}: '.format(ut) + errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            self.new_filter[ut] = new_filt
            s = 'Filter Wheel {}: Changing filter to {}'.format(ut, new_filt)
            retstrs.append(s)

        # Set flag
        self.set_filter_flag = 1

        # Format return string
        return '\n'.join(retstrs)

    def home_filters(self, ut_list=None):
        """Move filter wheel(s) to the home position."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if ut_list is None:
            ut_list = self.uts.copy()

        self.wait_for_info()
        retstrs = []
        for ut in sorted(ut_list):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('Filter Wheel {}: '.format(ut) + errortxt(s))
                continue

            # Check the filter wheel is not already moving
            if self.info[ut]['remaining'] > 0:
                s = 'Filter Wheel is already moving'
                retstrs.append('Filter Wheel {}: '.format(ut) + errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            s = 'Filter Wheel {}: Moving to home position'.format(ut)
            retstrs.append(s)

        # Set flag
        self.home_filter_flag = 1

        # Format return string
        return '\n'.join(retstrs)


if __name__ == '__main__':
    with make_pid_file('filt'):
        FiltDaemon()._run()
