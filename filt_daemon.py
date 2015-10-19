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
import os, sys, commands
from math import *
from string import split,find
import time
import Pyro4
import threading
# FLI modules
from fliapi import FakeFilterWheel
# TeCS modules
import X_params as params
import X_misc as misc
import X_logger as logger

########################################################################
# Filter wheel Daemon functions
class Filt_Daemon:
    def __init__(self):
        
        ### activate
        self.running=True
        
        ### find current username
        self.username=os.environ["LOGNAME"]

        ### set up logfile
        self.logfile = logger.Logfile('filt',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### initiate flags
        self.get_info_flag=0
        self.set_filter_flag=0
        self.get_filter_flag=0
        
        ### filters
        self.flist=params.FILTER_LIST
        self.current_filter=0
        self.new_filter=0
        
        ### status
        self.info='None yet'
        
        ### timing
        self.start_time=time.time()   #used for uptime
        
        ### start control thread
        t=threading.Thread(target=self.filt_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control function
    def filt_control(self):
        while(self.running):
            self.time_check = time.time()   #used for "ping"
            
            ### connect to filter
            filt=FakeFilterWheel('device','serial')
            
            ### control functions
            if(self.get_info_flag): # Request info
                try:
                    new_info = SiTech.get_info()
                    self.info = new_info
                except:
                    print 'No response from SiTech daemon'
                self.get_info_flag=0
            
            if(self.set_filter_flag): # Choose the active filter
                if filt.get_steps_remaining() > 0:
                    return 'Motor is still moving'
                try:
                    e = filt.set_filter_pos(self.new_filter)
                except:
                    print e
                self.set_filter_flag=0
            
            if(self.get_filter_flag): # Report the current filter
                self.current_filter  = filt.get_filter_pos()
                self.get_filter_flag=0
            
        self.logfile.log('Filter wheel control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount control functions
    def get_info(self):
        self.get_info_flag=1
    def set_filter(self,new_filt):
        self.new_filt=new_filt
        self.set_filter_flag=1
    def get_filter(self):
        self.get_filter_flag=1
        
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Daemon pinger
    def ping(self):
        #print '  pinged'
        dt_control = abs(time.time()-self.time_check)
        if dt_control > params.DAEMONS['mnt']['PINGLIFE']:
            return 'Last mount daemon control thread time check: %.1f seconds ago' % dt_control
        else:
            return 'ping'
    
    def report_to_UI(self,data):
        if data == 'info':
            return self.info
        else:
            return 'Invalid data request'
    
    def prod(self):
        return
        
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Status and shutdown
    def status_function(self):
        #print 'status query:', self.running
        return self.running
    
    def shutdown(self):
        self.running=False
        #print '  set status to', self.running

########################################################################
# Create Pyro control server 

pyro_daemon=Pyro4.Daemon(host=params.DAEMONS['filt']['HOST'], port=params.DAEMONS['filt']['PORT'])
filt_daemon=Filt_Daemon()

uri=pyro_daemon.register(filt_daemon,objectId = params.DAEMONS['filt']['PYROID'])

print 'Starting filter wheel daemon, with Pyro URI:',uri

Pyro4.config.COMMTIMEOUT=5.
pyro_daemon.requestLoop(loopCondition=filt_daemon.status_function)
print 'Exiting filter wheel daemon'
time.sleep(1.)
