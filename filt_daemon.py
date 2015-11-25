#!/usr/bin/env python

########################################################################
#                            filt_daemon.py                            #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#               G-TeCS daemon to control FLI filter wheel              #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from math import *
import time
import Pyro4
import threading
# FLI modules
from fliapi import FakeFilterWheel
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
    - set_filter(filt)
    """
    def __init__(self):
        self.running = True
        self.start_time = time.time()

        ### set up logfile
        self.logfile = logger.Logfile('filt',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### command flags
        self.get_info_flag = 1
        self.set_filter_flag = 0
        
        ### filter wheel variables
        self.info = {}
        self.flist = params.FILTER_LIST
        self.current_pos = 0
        self.current_filter_num = 0
        self.current_filter = 'X'
        self.new_filter_num = 0
        self.new_filter = 'X'
        self.remaining = 0
        
        ### start control thread
        t = threading.Thread(target=self.filt_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def filt_control(self):
        
        ### connect to (fake) filter
        filt = FakeFilterWheel('device','serial')
        
        while(self.running):
            self.time_check = time.time()
            
            ### control functions
            # request info
            if(self.get_info_flag):
                # update variables
                self.remaining = filt.get_steps_remaining()
                self.current_filter_num = filt.get_filter_pos()
                self.current_filter = self.flist[self.current_filter_num]
                self.current_pos = filt.stepper_position
                # save info
                info = {}
                if self.remaining > 0:
                    info['status'] = 'Moving'
                    info['remaining'] = self.remaining
                    info['current_filter'] = 'N/A'
                else:
                    info['status'] = 'Ready'
                    info['current_filter'] = self.current_filter
                info['current_filter_num'] = self.current_filter_num
                info['current_pos'] = self.current_pos
                info['uptime'] = time.time()-self.start_time
                info['ping'] = time.time()-self.time_check
                self.info = info
                self.get_info_flag = 0
            
            # choose the active filter
            if(self.set_filter_flag):
                self.logfile.log('Moving filter wheel to %s (%i)'\
                    %(self.new_filter,self.new_filter_num))
                c = filt.set_filter_pos(self.new_filter_num)
                if c: print c
                self.set_filter_flag = 0
            
            time.sleep(0.0001) # To save 100% CPU usage
        
        self.logfile.log('Filter wheel control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Filter wheel control functions
    def get_info(self):
        """Return filter wheel status info"""
        self.get_info_flag = 1
        time.sleep(0.1)
        return self.info
    
    def set_filter(self,new_filter):
        """Move filter wheel to given filter"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.remaining > 0:
            return 'ERROR: Motor is still moving'
        if new_filter not in self.flist:
            return 'ERROR: Filter not in list %s' %str(self.flist)
        else:
            self.new_filter = new_filter
            self.new_filter_num = self.flist.index(new_filter)
            self.set_filter_flag = 1
            return 'Moving filter'
    
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
print 'Starting filter wheel daemon at',uri

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=filt_daemon.status_function)

print 'Exiting filter wheel daemon'
time.sleep(1.)
