#!/usr/bin/env python
"""
Daemon to control FLI filter wheels via fli_interface
"""

import sys
import pid
import time
import datetime
from math import *
import Pyro4
import threading

from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs.daemons import HardwareDaemon, daemon_proxy, dependencies_are_alive


class FiltDaemon(HardwareDaemon):
    """Filter wheel hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, daemon_ID='filt')

        ### command flags
        self.get_info_flag = 1
        self.set_filter_flag = 0
        self.home_filter_flag = 0

        ### filter wheel variables
        self.info = None

        self.current_pos = {}
        self.current_filter_num = {}
        self.remaining = {}
        self.serial_number = {}
        self.homed = {}

        for intf in params.FLI_INTERFACES:
            nHW = len(params.FLI_INTERFACES[intf]['TELS'])
            self.current_pos[intf] = [0]*nHW
            self.remaining[intf] = [0]*nHW
            self.current_filter_num[intf] = [0]*nHW
            self.serial_number[intf] = [0]*nHW
            self.homed[intf] = [0]*nHW

        self.active_tel = []
        self.new_filter = ''

        self.dependency_error = 0
        self.dependency_check_time = 0

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()


    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

        while(self.running):
            self.time_check = time.time()

            ### check dependencies
            if (self.time_check - self.dependency_check_time) > 2:
                if not dependencies_are_alive(self.daemon_ID):
                    if not self.dependency_error:
                        self.logfile.error('Dependencies are not responding')
                        self.dependency_error = 1
                else:
                    if self.dependency_error:
                        self.logfile.info('Dependencies responding again')
                        self.dependency_error = 0
                self.dependency_check_time = time.time()

            if self.dependency_error:
                time.sleep(5)
                continue

            ### control functions
            # request info
            if self.get_info_flag:
                try:
                    # update variables
                    for tel in params.TEL_DICT:
                        intf, HW = params.TEL_DICT[tel]
                        try:
                            with daemon_proxy(intf) as fli:
                                self.current_pos[intf][HW] = fli.get_filter_position(HW)
                                self.remaining[intf][HW] = fli.get_filter_steps_remaining(HW)
                                self.current_filter_num[intf][HW] = fli.get_filter_number(HW)
                                self.serial_number[intf][HW] = fli.get_filter_serial_number(HW)
                                self.homed[intf][HW] = fli.get_filter_homed(HW)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                    # save info
                    info = {}
                    for tel in params.TEL_DICT:
                        intf, HW = params.TEL_DICT[tel]
                        tel = str(params.FLI_INTERFACES[intf]['TELS'][HW])
                        if self.remaining[intf][HW] > 0:
                            info['status'+tel] = 'Moving'
                            info['remaining'+tel] = self.remaining[intf][HW]
                        else:
                            info['status'+tel] = 'Ready'
                        info['current_filter_num'+tel] = self.current_filter_num[intf][HW]
                        info['current_pos'+tel] = self.current_pos[intf][HW]
                        info['serial_number'+tel] = self.serial_number[intf][HW]
                        info['homed'+tel] = self.homed[intf][HW]

                    info['uptime'] = time.time()-self.start_time
                    info['ping'] = time.time()-self.time_check
                    now = datetime.datetime.utcnow()
                    info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

                    self.info = info
                except:
                    self.logfile.error('get_info command failed')
                    self.logfile.debug('', exc_info=True)
                self.get_info_flag = 0

            # set the active filter
            if self.set_filter_flag:
                try:
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        new_filter_num = params.FILTER_LIST.index(self.new_filter)

                        self.logfile.info('Moving filter wheel %i (%s-%i) to %s (%i)',
                                          tel, intf, HW, self.new_filter, new_filter_num)

                        try:
                            with daemon_proxy(intf) as fli:
                                c = fli.set_filter_pos(new_filter_num,HW)
                                if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                except:
                    self.logfile.error('set_filter command failed')
                    self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.set_filter_flag = 0

            # home the filter
            if self.home_filter_flag:
                try:
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]

                        self.logfile.info('Homing filter wheel %i (%s-%i)',
                                          tel, intf, HW)

                        try:
                            with daemon_proxy(intf) as fli:
                                c = fli.home_filter(HW)
                                if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                except:
                    self.logfile.error('home_filter command failed')
                    self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.home_filter_flag = 0

            time.sleep(params.DAEMON_SLEEP_TIME) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return


    # Filter wheel control functions
    def get_info(self):
        """Return filter wheel status info"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set flag
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.1)
        return self.info


    def get_info_simple(self):
        """Return plain status dict, or None"""
        try:
            info = self.get_info()
        except:
            return None
        return info


    def set_filter(self, new_filter, tel_list):
        """Move filter wheel to given filter"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if new_filter.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list %s' %str(params.FILTER_LIST))
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            if self.remaining[intf][HW] == 0 and self.homed[intf][HW]:
                self.active_tel += [tel]
        self.new_filter = new_filter

        # Set flag
        self.set_filter_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            s += '\n  '
            if self.remaining[intf][HW] > 0:
                s += misc.ERROR('"HardwareStatusError: Filter wheel %i motor is still moving"' %tel)
            elif not self.homed[intf][HW]:
                s += misc.ERROR('"HardwareStatusError: Filter wheel %i not homed"' %tel)
            else:
                s += 'Moving filter wheel %i' %tel
        return s


    def home_filter(self, tel_list):
        """Move filter wheel to home position"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            if self.remaining[intf][HW] == 0:
                self.active_tel += [tel]

        # Set flag
        self.home_filter_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            s += '\n  '
            if self.remaining[intf][HW] > 0:
                s += misc.ERROR('"HardwareStatusError: Filter wheel %i motor is still moving"' %tel)
            else:
                s += 'Homing filter wheel %i' %tel
        return s


if __name__ == "__main__":
    try:
        with pid.PidFile('filt', piddir=params.CONFIG_PATH):
            FiltDaemon()._run()
    except pid.PidFileError:
        raise misc.MultipleDaemonError('Daemon already running')
