#!/usr/bin/env python

########################################################################
#                            foc_daemon.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#     G-TeCS meta-daemon to control FLI focusers via fli_interface     #
#                    Martin Dyer, Sheffield, 2015-16                   #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
from math import *
import time, datetime
import Pyro4
import threading
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params

########################################################################
# Focuser daemon class

class FocDaemon:
    """
    Focuser daemon class

    Contains X functions:
    - get_info()
    - set_focuser(pos, telescopeIDs)
    - move_focuser(steps, telescopeIDs)
    - home_focuser(telescopeIDs)
    """

    def __init__(self):
        self.running = True
        self.start_time = time.time()

        ### set up logfile
        self.logfile = logger.getLogger('foc',
                                        file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
        self.logfile.info('Daemon started')

        ### command flags
        self.get_info_flag = 1
        self.set_focuser_flag = 0
        self.move_focuser_flag = 0
        self.home_focuser_flag = 0

        ### focuser variables
        self.info = {}
        self.tel_dict = params.TEL_DICT

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

        ### start control thread
        t = threading.Thread(target=self.foc_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def foc_control(self):

        # make proxies once, outside the loop
        fli_proxies = dict()
        for intf in params.FLI_INTERFACES:
            fli_proxies[intf] = Pyro4.Proxy(params.FLI_INTERFACES[intf]['ADDRESS'])
            fli_proxies[intf]._pyroTimeout = params.PROXY_TIMEOUT

        while(self.running):
            self.time_check = time.time()

            ### control functions
            # request info
            if(self.get_info_flag):
                # update variables
                for tel in self.tel_dict:
                    intf, HW = self.tel_dict[tel]
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
                for tel in self.tel_dict:
                    intf, HW = self.tel_dict[tel]
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
                self.get_info_flag = 0

            # move the focuser
            if(self.move_focuser_flag):
                # loop through each unit to send orders to in turn
                for tel in self.active_tel:
                    intf, HW = self.tel_dict[tel]
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

                # cleare the 'active' units
                self.active_tel = []

                self.move_focuser_flag = 0

            # home the focuser
            if(self.home_focuser_flag):
                # loop through each unit to send orders to in turn
                for tel in self.active_tel:
                    intf, HW = self.tel_dict[tel]

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
                # cleare the 'active' units
                self.active_tel = []

                self.home_focuser_flag = 0

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Focuser control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Focuser control functions
    def get_info(self):
        """Return focuser status info"""
        self.get_info_flag = 1
        time.sleep(0.1)
        return self.info

    def set_focuser(self,new_pos,tel_list):
        """Move focuser to given position"""
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = self.tel_dict[tel]
            if self.remaining[intf][HW] > 0:
                s += '\n  ERROR: Focuser %i motor is still moving' %tel
            elif new_pos > self.limit[intf][HW]:
                s += '\n  ERROR: Position past limit'
            else:
                self.active_tel += [tel]
                self.move_steps[intf][HW] = new_pos - self.current_pos[intf][HW]
                s += '\n  Moving focuser %i' %tel
        self.move_focuser_flag = 1
        return s

    def move_focuser(self,move_steps,tel_list):
        """Move focuser by given number of steps"""
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = self.tel_dict[tel]
            if self.remaining[intf][HW] > 0:
                s += '\n  ERROR: Focuser %i motor is still moving' %tel
            elif (self.current_pos[intf][HW] + move_steps) > self.limit[intf][HW]:
                s += '\n  ERROR: Position past limit'
            else:
                self.active_tel += [tel]
                self.move_steps[intf][HW] = move_steps
                s += '\n  Moving focuser %i' %tel
        self.move_focuser_flag = 1
        return s

    def home_focuser(self,tel_list):
        """Move focuser to the home position"""
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = self.tel_dict[tel]
            if self.remaining[intf][HW] > 0:
                s += '\n  ERROR: Focuser %i motor is still moving' %tel
            else:
                self.active_tel += [tel]
                s += '\n  Homing focuser %i' %tel
        self.home_focuser_flag = 1
        return s

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS['foc']['PINGLIFE']:
            return 'ERROR: Last control thread time check was %.1f seconds ago' %dt_control
        else:
            return 'ping'

    def prod(self):
        return

    def status_function(self):
        return self.running

    def shutdown(self):
        self.running = False

########################################################################

def start():
    '''
    Create Pyro server, register the daemon and enter control loop
    '''
    pyro_host = params.DAEMONS['foc']['HOST']
    pyro_port = params.DAEMONS['foc']['PORT']
    pyro_ID = params.DAEMONS['foc']['PYROID']

    pyro_daemon = Pyro4.Daemon(host=pyro_host, port=pyro_port)
    foc_daemon = FocDaemon()

    uri = pyro_daemon.register(foc_daemon, objectId=pyro_ID)
    foc_daemon.logfile.info('Starting focuser daemon at %s', uri)

    Pyro4.config.COMMTIMEOUT = 5.
    pyro_daemon.requestLoop(loopCondition=foc_daemon.status_function)

    foc_daemon.logfile.info('Exiting focuser daemon')
    time.sleep(1.)

if __name__ == "__main__":
    start()
