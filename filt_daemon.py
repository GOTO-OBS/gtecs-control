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
from tecs_modules import logger
from tecs_modules import misc
from tecs_modules import params

########################################################################
# Filter wheel daemon functions
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

        for nuc in params.FLI_INTERFACES:
            self.current_pos[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.remaining[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.current_filter_num[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.serial_number[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.homed[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])

        self.active_tel = []
        self.new_filter = ''

        ### start control thread
        t = threading.Thread(target=self.filt_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def filt_control(self):

        while(self.running):
            self.time_check = time.time()

            ### control functions
            # request info
            if(self.get_info_flag):
                # update variables
                for tel in list(self.tel_dict.keys()):
                    nuc, HW = self.tel_dict[tel]
                    fli = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
                    fli._pyroTimeout = params.PROXY_TIMEOUT
                    try:
                        self.current_pos[nuc][HW] = fli.get_filter_position(HW)
                        self.remaining[nuc][HW] = fli.get_filter_steps_remaining(HW)
                        self.current_filter_num[nuc][HW] = fli.get_filter_number(HW)
                        self.serial_number[nuc][HW] = fli.get_filter_serial_number(HW)
                        self.homed[nuc][HW] = fli.get_filter_homed(HW)
                    except:
                        self.logfile.info('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                # save info
                info = {}
                for tel in list(self.tel_dict.keys()):
                    nuc, HW = self.tel_dict[tel]
                    tel = str(params.FLI_INTERFACES[nuc]['TELS'][HW])
                    if self.remaining[nuc][HW] > 0:
                        info['status'+tel] = 'Moving'
                        info['remaining'+tel] = self.remaining[nuc][HW]
                    else:
                        info['status'+tel] = 'Ready'
                    info['current_filter_num'+tel] = self.current_filter_num[nuc][HW]
                    info['current_pos'+tel] = self.current_pos[nuc][HW]
                    info['serial_number'+tel] = self.serial_number[nuc][HW]
                    info['homed'+tel] = self.homed[nuc][HW]
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
                    nuc, HW = self.tel_dict[tel]
                    new_filter_num = self.flist.index(self.new_filter)

                    self.logfile.info('Moving filter wheel %i (%s-%i) to %s (%i)',
                                      tel, nuc, HW, self.new_filter, new_filter_num)

                    fli = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
                    fli._pyroTimeout = params.PROXY_TIMEOUT
                    try:
                        c = fli.set_filter_pos(new_filter_num,HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.info('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                # clear the 'active' units
                self.active_tel = []

                self.set_filter_flag = 0

            # home the filter
            if(self.home_filter_flag):
                # loop through each unit to send orders to it in turn
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]

                    self.logfile.info('Homing filter wheel %i (%s-%i)',
                                      tel, nuc, HW)

                    fli = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
                    fli._pyroTimeout = params.PROXY_TIMEOUT
                    try:
                        c = fli.home_filter(HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.info('No response from fli interface on %s', nuc)
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
            if tel not in list(self.tel_dict.keys()):
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict.keys()))
        if new_filter not in self.flist:
            return 'ERROR: Filter not in list %s' %str(self.flist)
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            nuc, HW = self.tel_dict[tel]
            if self.remaining[nuc][HW] > 0:
                s += '\n  ERROR: Filter wheel %i motor is still moving' %tel
            elif not self.homed[nuc][HW]:
                s += '\n  ERROR: Home filter wheel %i first!' %tel
            else:
                self.active_tel += [tel]
                s += '\n  Moving filter wheel %i' %tel
        self.set_filter_flag = 1
        return s

    def home_filter(self,tel_list):
        """Move filter wheel to home position"""
        for tel in tel_list:
            if tel not in list(self.tel_dict.keys()):
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict.keys()))
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            nuc, HW = self.tel_dict[tel]
            if self.remaining[nuc][HW] > 0:
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
# Create Pyro control server
pyro_daemon = Pyro4.Daemon(host=params.DAEMONS['filt']['HOST'], port=params.DAEMONS['filt']['PORT'])
filt_daemon = FiltDaemon()

uri = pyro_daemon.register(filt_daemon,objectId = params.DAEMONS['filt']['PYROID'])
filt_daemon.logfile.info('Starting filter wheel daemon at %s', uri)

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=filt_daemon.status_function)

filt_daemon.logfile.info('Exiting filter wheel daemon')
time.sleep(1.)
