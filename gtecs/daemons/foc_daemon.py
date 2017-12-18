#!/usr/bin/env python
"""
Daemon to control FLI focusers via fli_interface
"""
import sys
import time
import datetime
from math import *
import Pyro4
import threading

from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs.daemons import HardwareDaemon


class FocDaemon(HardwareDaemon):
    """Focuser hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'foc')

        ### command flags
        self.get_info_flag = 1
        self.set_focuser_flag = 0
        self.move_focuser_flag = 0
        self.home_focuser_flag = 0

        ### focuser variables
        self.info = None

        self.limit = {}
        self.current_pos = {}
        self.remaining = {}
        self.int_temp = {}
        self.ext_temp = {}
        self.move_steps = {}
        self.serial_number = {}

        for intf in params.FLI_INTERFACES:
            nHW = len(params.FLI_INTERFACES[intf]['TELS'])
            self.limit[intf] = [0]*nHW
            self.current_pos[intf] = [0]*nHW
            self.remaining[intf] = [0]*nHW
            self.int_temp[intf] = [0]*nHW
            self.ext_temp[intf] = [0]*nHW
            self.move_steps[intf] = [0]*nHW
            self.serial_number[intf] = [0]*nHW

        self.active_tel = []

        self.dependency_error = 0
        self.dependency_check_time = 0

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()


    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

        # make proxies once, outside the loop
        fli_proxies = dict()
        for intf in params.FLI_INTERFACES:
            fli_proxies[intf] = Pyro4.Proxy(params.DAEMONS[intf]['ADDRESS'])
            fli_proxies[intf]._pyroTimeout = params.PROXY_TIMEOUT

        while(self.running):
            self.time_check = time.time()

            ### check dependencies
            if (self.time_check - self.dependency_check_time) > 2:
                if not misc.dependencies_are_alive('foc'):
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
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            self.limit[intf][HW] = fli.get_focuser_limit(HW)
                            self.remaining[intf][HW] = fli.get_focuser_steps_remaining(HW)
                            self.current_pos[intf][HW] = fli.get_focuser_position(HW)
                            self.int_temp[intf][HW] = fli.get_focuser_temp('internal',HW)
                            self.ext_temp[intf][HW] = fli.get_focuser_temp('external',HW)
                            self.serial_number[intf][HW] = fli.get_focuser_serial_number(HW)
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
                            if self.move_steps[intf][HW] == 0: # Homing, needed due to bug in remaining
                                info['remaining'+tel] = self.current_pos[intf][HW]
                            else:
                                info['remaining'+tel] = self.remaining[intf][HW]
                        else:
                            info['status'+tel] = 'Ready'
                        info['current_pos'+tel] = self.current_pos[intf][HW]
                        info['limit'+tel] = self.limit[intf][HW]
                        info['int_temp'+tel] = self.int_temp[intf][HW]
                        info['ext_temp'+tel] = self.ext_temp[intf][HW]
                        info['serial_number'+tel] = self.serial_number[intf][HW]

                    info['uptime'] = time.time()-self.start_time
                    info['ping'] = time.time()-self.time_check
                    now = datetime.datetime.utcnow()
                    info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

                    self.info = info
                except:
                    self.logfile.error('get_info command failed')
                    self.logfile.debug('', exc_info=True)
                self.get_info_flag = 0

            # move the focuser
            if self.move_focuser_flag:
                try:
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        move_steps = self.move_steps[intf][HW]
                        new_pos = self.current_pos[intf][HW] + move_steps

                        self.logfile.info('Moving focuser %i (%s-%i) by %i to %i',
                                          tel, intf, HW, move_steps, new_pos)

                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            c = fli.step_focuser_motor(move_steps,HW)
                            if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                except:
                    self.logfile.error('move_focuser command failed')
                    self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.move_focuser_flag = 0

            # home the focuser
            if self.home_focuser_flag:
                try:
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]

                        self.logfile.info('Homing focuser %i (%s-%i)',
                                          tel, intf, HW)

                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            c = fli.home_focuser(HW)
                            if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                        fli._pyroRelease()
                        self.move_steps[intf][HW] = 0 # to mark that it's homing
                except:
                    self.logfile.error('home_focuser command failed')
                    self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.home_focuser_flag = 0

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return


    # Focuser control functions
    def get_info(self):
        """Return focuser status info"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set flag
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.1)
        return self.info


    def get_simple_info(self):
        """Return simple exposure queue status dict"""
        try:
            info = self.get_info()
        except:
            return None
        return info


    def set_focuser(self, new_pos, tel_list):
        """Move focuser to given position"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if int(new_pos) < 0 or (int(new_pos) - new_pos) != 0:
            raise ValueError('Position must be a positive integer')
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(list(params.TEL_DICT)))

        # Set values
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            if self.remaining[intf][HW] == 0 and new_pos <= self.limit[intf][HW]:
                self.active_tel += [tel]
                self.move_steps[intf][HW] = new_pos - self.current_pos[intf][HW]

        # Set flag
        self.move_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            s += '\n  '
            if self.remaining[intf][HW] > 0:
                s += misc.ERROR('"HardwareStatusError: Focuser %i motor is still moving"' %tel)
            elif new_pos > self.limit[intf][HW]:
                s += misc.ERROR('"ValueError: Focuser %i position past limit"' %tel)
            else:
                s += 'Moving focuser %i' %tel
        return s


    def move_focuser(self, move_steps, tel_list):
        """Move focuser by given number of steps"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if (int(new_pos) - new_pos) != 0:
            raise ValueError('Steps must be an integer')
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(list(params.TEL_DICT)))

        # Set values
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            new_pos = self.current_pos[intf][HW] + move_steps
            if self.remaining[intf][HW] == 0 and new_pos <= self.limit[intf][HW]:
                self.active_tel += [tel]
                self.move_steps[intf][HW] = move_steps

        # Set flag
        self.move_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            new_pos = self.current_pos[intf][HW] + move_steps
            s += '\n  '
            if self.remaining[intf][HW] > 0:
                s += misc.ERROR('"HardwareStatusError: Focuser %i motor is still moving"' %tel)
            elif new_pos > self.limit[intf][HW]:
                s += misc.ERROR('"ValueError: Position past limit"')
            else:
                s += 'Moving focuser %i' %tel
        return s


    def home_focuser(self, tel_list):
        """Move focuser to the home position"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(list(params.TEL_DICT)))

        # Set values
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            if self.remaining[intf][HW] == 0:
                self.active_tel += [tel]

        # Set flag
        self.home_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            s += '\n  '
            if self.remaining[intf][HW] > 0:
                s += misc.ERROR('"HardwareStatusError: Focuser %i motor is still moving"' %tel)
            else:
                s += 'Homing focuser %i' %tel
        return s


def start():
    """Create Pyro server, register the daemon and enter request loop"""

    host = params.DAEMONS['foc']['HOST']
    port = params.DAEMONS['foc']['PORT']

    # Check the daemon isn't already running
    if not misc.there_can_only_be_one('foc'):
        sys.exit()

    # Start the daemon
    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        foc_daemon = FocDaemon()
        uri = pyro_daemon.register(foc_daemon, objectId='foc')
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        foc_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=foc_daemon.status_function)

    # Loop has closed
    foc_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)


if __name__ == "__main__":
    start()
