#!/usr/bin/env python
"""Daemon to control FLI filter wheels via fli_interface."""

import datetime
import threading
import time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import HardwareDaemon, daemon_proxy


class FiltDaemon(HardwareDaemon):
    """Filter wheel hardware daemon class."""

    def __init__(self):
        HardwareDaemon.__init__(self, daemon_id='filt')

        # command flags
        self.get_info_flag = 1
        self.set_filter_flag = 0
        self.home_filter_flag = 0

        # filter wheel variables
        self.current_pos = {}
        self.current_filter_num = {}
        self.remaining = {}
        self.serial_number = {}
        self.homed = {}

        for intf in params.FLI_INTERFACES:
            nhw = len(params.FLI_INTERFACES[intf]['TELS'])
            self.current_pos[intf] = [0] * nhw
            self.remaining[intf] = [0] * nhw
            self.current_filter_num[intf] = [0] * nhw
            self.serial_number[intf] = [0] * nhw
            self.homed[intf] = [0] * nhw

        self.active_tel = []
        self.new_filter = ''

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        self.log.info('Daemon control thread started')

        while(self.running):
            self.time_check = time.time()

            # check dependencies
            if (self.time_check - self.dependency_check_time) > 2:
                if not self.dependencies_are_alive:
                    if not self.dependency_error:
                        self.log.error('Dependencies are not responding')
                        self.dependency_error = True
                else:
                    if self.dependency_error:
                        self.log.info('Dependencies responding again')
                        self.dependency_error = False
                self.dependency_check_time = time.time()

            if self.dependency_error:
                time.sleep(5)
                continue

            # control functions
            # request info
            if self.get_info_flag:
                try:
                    # update variables
                    for tel in params.TEL_DICT:
                        intf, hw = params.TEL_DICT[tel]
                        try:
                            with daemon_proxy(intf) as fli:
                                self.current_pos[intf][hw] = fli.get_filter_position(hw)
                                self.remaining[intf][hw] = fli.get_filter_steps_remaining(hw)
                                self.current_filter_num[intf][hw] = fli.get_filter_number(hw)
                                self.serial_number[intf][hw] = fli.get_filter_serial_number(hw)
                                self.homed[intf][hw] = fli.get_filter_homed(hw)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)
                    # save info
                    info = {}
                    for tel in params.TEL_DICT:
                        intf, hw = params.TEL_DICT[tel]
                        tel = str(params.FLI_INTERFACES[intf]['TELS'][hw])
                        if self.remaining[intf][hw] > 0:
                            info['status' + tel] = 'Moving'
                            info['remaining' + tel] = self.remaining[intf][hw]
                        else:
                            info['status' + tel] = 'Ready'
                        info['current_filter_num' + tel] = self.current_filter_num[intf][hw]
                        info['current_pos' + tel] = self.current_pos[intf][hw]
                        info['serial_number' + tel] = self.serial_number[intf][hw]
                        info['homed' + tel] = self.homed[intf][hw]

                    info['uptime'] = time.time() - self.start_time
                    info['ping'] = time.time() - self.time_check
                    now = datetime.datetime.utcnow()
                    info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

                    self.info = info
                except Exception:
                    self.log.error('get_info command failed')
                    self.log.debug('', exc_info=True)
                self.get_info_flag = 0

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

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Filter wheel control functions
    def get_info(self):
        """Return filter wheel status info."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonDependencyError('Dependencies are not running')

        # Set flag
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.1)
        return self.info

    def get_info_simple(self):
        """Return plain status dict, or None."""
        try:
            info = self.get_info()
        except Exception:
            return None
        return info

    def set_filter(self, new_filter, tel_list):
        """Move filter wheel to given filter."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonDependencyError('Dependencies are not running')

        # Check input
        if new_filter.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list %s' % str(params.FILTER_LIST))
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            if self.remaining[intf][hw] == 0 and self.homed[intf][hw]:
                self.active_tel += [tel]
        self.new_filter = new_filter

        # Set flag
        self.set_filter_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            s += '\n  '
            if self.remaining[intf][hw] > 0:
                s += misc.errortxt('"HardwareStatusError: Filter wheel %i is still moving"' % tel)
            elif not self.homed[intf][hw]:
                s += misc.errortxt('"HardwareStatusError: Filter wheel %i not homed"' % tel)
            else:
                s += 'Moving filter wheel %i' % tel
        return s

    def home_filter(self, tel_list):
        """Move filter wheel to home position."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonDependencyError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            if self.remaining[intf][hw] == 0:
                self.active_tel += [tel]

        # Set flag
        self.home_filter_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            s += '\n  '
            if self.remaining[intf][hw] > 0:
                s += misc.errortxt('"HardwareStatusError: Filter wheel %i is still moving"' % tel)
            else:
                s += 'Homing filter wheel %i' % tel
        return s


if __name__ == "__main__":
    daemon_id = 'filt'
    with misc.make_pid_file(daemon_id):
        FiltDaemon()._run()
