#!/usr/bin/env python3
"""Daemon to control filter wheels."""

import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.daemons import (BaseDaemon, DaemonDependencyError, HardwareError,
                                   daemon_proxy, get_daemon_host)


class FiltDaemon(BaseDaemon):
    """Filter wheel hardware daemon class."""

    def __init__(self):
        super().__init__('filt')

        # command flags
        self.set_filter_flag = 0
        self.home_filter_flag = 0

        # filter wheel variables
        self.uts = params.UTS_WITH_FILTERWHEELS.copy()
        self.active_uts = []
        self.interfaces = {f'filt{ut}' for ut in self.uts}

        self.new_filter = {ut: '' for ut in self.uts}
        self.filters = {ut: params.UT_DICT[ut]['FILTERS'] for ut in self.uts}

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
            # set the active filter
            if self.set_filter_flag:
                try:
                    for ut in self.active_uts:
                        new_filter_num = self.filters[ut].index(self.new_filter[ut])
                        self.log.info('Moving filter wheel {} to {} ({})'.format(
                                      ut, self.new_filter[ut], new_filter_num))

                        try:
                            with daemon_proxy(f'filt{ut}') as interface:
                                reply = interface.move_filterwheel(new_filter_num)
                                if reply:
                                    self.log.info(reply)
                            self.last_move_time[ut] = self.loop_time
                        except Exception:
                            self.log.error('No response from interface filt{}'.format(ut))
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
                        self.log.info('Homing filter wheel {}'.format(ut))
                        try:
                            with daemon_proxy(f'filt{ut}') as interface:
                                reply = interface.home_filterwheel()
                                if reply:
                                    self.log.info(reply)
                            self.last_move_time[ut] = self.loop_time
                        except Exception:
                            self.log.error('No response from interface filt{}'.format(ut))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('home_filter command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.home_filter_flag = 0
                self.force_check_flag = True

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

        # Get info from each UT
        temp_info['uts'] = self.uts.copy()
        for ut in self.uts:
            try:
                ut_info = {}
                ut_info['interface_id'] = f'filt{ut}'
                with daemon_proxy(f'filt{ut}') as interface:
                    ut_info['serial_number'] = interface.get_serial_number()
                    ut_info['hw_class'] = interface.get_class()
                    ut_info['remaining'] = interface.get_steps_remaining()
                    ut_info['current_filter_num'] = interface.get_position()
                    ut_info['current_filter'] = self.filters[ut][ut_info['current_filter_num']]
                    ut_info['current_pos'] = interface.get_motor_position()
                    ut_info['homed'] = interface.get_homed()

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
            now_strs = ['{}:{}'.format(ut,
                                       temp_info[ut]['status']
                                       if temp_info[ut] is not None else 'ERROR')
                        for ut in self.uts]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Filter wheels are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(ut,
                                           self.info[ut]['status']
                                           if self.info[ut] is not None else 'ERROR')
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
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')

        if not isinstance(new_filter, dict):
            new_filter = {ut: new_filter for ut in self.uts}
        if any(ut not in self.uts for ut in new_filter):
            raise ValueError(f'Invalid UTs: {[ut for ut in new_filter if ut not in self.uts]}')
        if any(new_filter[ut] not in self.filters[ut] for ut in new_filter):
            bad_filters = {ut: new_filter[ut]
                           for ut in new_filter
                           if new_filter[ut] not in self.filters[ut]}
            raise HardwareError(f'Invalid filters: {bad_filters}')

        self.wait_for_info()
        if any(not self.info[ut]['homed'] for ut in new_filter):
            bad_uts = [ut for ut in new_filter if not self.info[ut]['homed']]
            raise HardwareError(f'Filter Wheels are not homed: {bad_uts}')
        if any(self.info[ut]['remaining'] > 0 for ut in new_filter):
            bad_uts = [ut for ut in new_filter if self.info[ut]['remaining'] > 0]
            raise HardwareError(f'Filter Wheels are already moving: {bad_uts}')

        self.active_uts = sorted([ut for ut in new_filter
                                  if new_filter[ut] == self.info[ut]['current_filter']])
        self.new_filter.update(new_filter)
        self.set_filter_flag = 1

    def home_filters(self, uts=None):
        """Move filter wheel(s) to the home position."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if uts is None:
            uts = self.uts.copy()
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')

        self.wait_for_info()
        if any(self.info[ut]['remaining'] > 0 for ut in uts):
            bad_uts = [ut for ut in uts if self.info[ut]['remaining'] > 0]
            raise HardwareError(f'Filter Wheels are already moving: {bad_uts}')

        self.active_uts = sorted(uts)
        self.home_filter_flag = 1

    def get_info_string(self, verbose=False, force_update=False):
        """Get a string for printing status info."""
        info = self.get_info(force_update)
        if not verbose:
            msg = ''
            for ut in info['uts']:
                host, port = get_daemon_host(info[ut]['interface_id'])
                msg += 'FILTER WHEEL {} ({}:{}) '.format(ut, host, port)
                if info[ut]['status'] != 'Moving':
                    if not info[ut]['homed']:
                        msg += 'Current filter: UNHOMED '
                    else:
                        msg += '  Current filter: {} '.format(info[ut]['current_filter'])
                        msg += '({}) '.format(info[ut]['current_filter_num'])
                    msg += '  [{}]\n'.format(info[ut]['status'])
                else:
                    msg += '  {} ({})\n'.format(info[ut]['status'], info[ut]['remaining'])
            msg = msg.rstrip()
        else:
            msg = '#### FILTER WHEEL INFO ####\n'
            for ut in info['uts']:
                host, port = get_daemon_host(info[ut]['interface_id'])
                msg += 'FILTER WHEEL {} ({}:{})\n'.format(ut, host, port)
                if info[ut]['status'] != 'Moving':
                    msg += 'Status: {}\n'.format(info[ut]['status'])
                    if not info[ut]['homed']:
                        msg += 'Current filter: UNHOMED\n'
                    else:
                        msg += 'Current filter:     {}\n'.format(info[ut]['current_filter'])
                else:
                    msg += 'Status: {} ({})\n'.format(info[ut]['status'], info[ut]['remaining'])
                    msg += 'Current filter:     N/A\n'
                msg += 'Current filter num: {}\n'.format(info[ut]['current_filter_num'])
                msg += 'Filters:            {}\n'.format(','.join(info[ut]['filters']))
                msg += 'Current motor pos:  {}\n'.format(info[ut]['current_pos'])
                msg += 'Serial number:      {}\n'.format(info[ut]['serial_number'])
                msg += 'Hardware class:     {}\n'.format(info[ut]['hw_class'])
                msg += '~~~~~~~\n'
            msg += 'Uptime: {:.1f}s\n'.format(info['uptime'])
            msg += 'Timestamp: {}\n'.format(info['timestamp'])
            msg += '###########################'
        return msg


if __name__ == '__main__':
    daemon = FiltDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
