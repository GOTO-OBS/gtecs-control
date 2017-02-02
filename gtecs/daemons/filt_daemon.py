#!/usr/bin/env python

########################################################################
#                            filt_daemon.py                            #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#   G-TeCS meta-daemon to control FLI filter wheels via fli_interface  #
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
# Filter wheel daemon class

class FiltDaemon:
    """
    Filter wheel daemon class

    Contains 2 functions:
    - get_info()
    - set_filter(filt, telescopeIDs)
    - home_filter(telescopeIDs)
    """

    def __init__(self):
        self.running = True
        self.start_time = time.time()

        ### set up logfile
        self.logfile = logger.getLogger('filt',
                                        file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
        self.logfile.info('Daemon started')

        ### command flags
        self.get_info_flag = 1
        self.set_filter_flag = 0
        self.home_filter_flag = 0

        ### filter wheel variables
        self.info = {}
        self.flist = params.FILTER_LIST
        self.tel_dict = params.TEL_DICT

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

        ### start control thread
        t = threading.Thread(target=self.filt_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def filt_control(self):

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
                for tel in self.tel_dict:
                    intf, HW = self.tel_dict[tel]
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
                self.get_info_flag = 0

            # set the active filter
            if(self.set_filter_flag):
                # loop through each unit to send orders to in turn
                for tel in self.active_tel:
                    intf, HW = self.tel_dict[tel]
                    new_filter_num = self.flist.index(self.new_filter)

                    self.logfile.info('Moving filter wheel %i (%s-%i) to %s (%i)',
                                      tel, intf, HW, self.new_filter, new_filter_num)

                    fli = fli_proxies[intf]
                    try:
                        fli._pyroReconnect()
                        c = fli.set_filter_pos(new_filter_num,HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', intf)
                        self.logfile.debug('', exc_info=True)

                # clear the 'active' units
                self.active_tel = []

                self.set_filter_flag = 0

            # home the filter
            if(self.home_filter_flag):
                # loop through each unit to send orders to it in turn
                for tel in self.active_tel:
                    intf, HW = self.tel_dict[tel]

                    self.logfile.info('Homing filter wheel %i (%s-%i)',
                                      tel, intf, HW)

                    fli = fli_proxies[intf]
                    try:
                        fli._pyroReconnect()
                        c = fli.home_filter(HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', intf)
                        self.logfile.debug('', exc_info=True)
                # clear the active units
                self.active_tel = []

                self.home_filter_flag = 0

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Filter wheel control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Filter wheel control functions
    def get_info(self):
        """Return filter wheel status info"""
        self.get_info_flag = 1
        time.sleep(0.1)
        return self.info

    def set_filter(self,new_filter,tel_list):
        """Move filter wheel to given filter"""
        self.new_filter = new_filter
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        if new_filter not in self.flist:
            return 'ERROR: Filter not in list %s' %str(self.flist)
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = self.tel_dict[tel]
            if self.remaining[intf][HW] > 0:
                s += '\n  ERROR: Filter wheel %i motor is still moving' %tel
            elif not self.homed[intf][HW]:
                s += '\n  ERROR: Home filter wheel %i first!' %tel
            else:
                self.active_tel += [tel]
                s += '\n  Moving filter wheel %i' %tel
        self.set_filter_flag = 1
        return s

    def home_filter(self,tel_list):
        """Move filter wheel to home position"""
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            intf, HW = self.tel_dict[tel]
            if self.remaining[intf][HW] > 0:
                s += '\n  ERROR: Filter wheel %i motor is still moving' %tel
            else:
                self.active_tel += [tel]
                s += '\n  Homing filter wheel %i' %tel
        self.home_filter_flag = 1
        return s

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS['filt']['PINGLIFE']:
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
    Create Pyro server, register the daemon and enter request loop
    '''
    host = params.DAEMONS['filt']['HOST']
    port = params.DAEMONS['filt']['PORT']
    pyroID = params.DAEMONS['filt']['PYROID']

    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        filt_daemon = FiltDaemon()
        uri = pyro_daemon.register(filt_daemon, objectId=pyroID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        filt_daemon.logfile.info('Starting filter wheel daemon at %s', uri)
        pyro_daemon.requestLoop(loopCondition=filt_daemon.status_function)

    # Loop has closed
    filt_daemon.logfile.info('Exiting filter wheel daemon')
    time.sleep(1.)

if __name__ == "__main__":
    start()
