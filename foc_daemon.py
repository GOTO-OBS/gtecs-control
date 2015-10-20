#!/usr/bin/env python

########################################################################
#                            foc_daemon.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                 G-TeCS daemon to control FLI focuser                 #
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
from fliapi import FakeFocuser
# TeCS modules
import X_params as params
import X_misc as misc
import X_logger as logger

########################################################################
# Focuser Daemon functions
class Filt_Daemon:
    def __init__(self):
        
        ### activate
        self.running=True
        
        ### find current username
        self.username=os.environ["LOGNAME"]

        ### set up logfile
        self.logfile = logger.Logfile('foc',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### initiate flags
        self.get_info_flag=0
        self.remaining_flag=0
        self.set_flag=0
        self.move_flag=0
        self.home_flag=0
        
        ### position variables
        self.steps=0
        self.new_pos=0
        self.remaining=0
        self.limit=1000
        
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
        
        ### connect to focuser
        foc=FakeFocuser('device','serial')
        self.limit = foc.max_extent

        while(self.running):
            self.time_check = time.time()   #used for "ping"
            
            ### control functions
            if(self.get_info_flag): # Request info
                info = {}
                steps_remaining=foc.get_steps_remaining()
                if steps_remaining > 0:
                    info['status']='Moving (%i)' %(steps_remaining)
                else:
                    info['status']='Ready'
                info['current_pos']=foc.stepper_position
                info['int_temp']=foc.read_temperature('internal')
                info['ext_temp']=foc.read_temperature('external')
                self.info = info
                self.get_info_flag=0
            
            if(self.remaining_flag): # Check steps remaining
                self.remaining = foc.get_steps_remaining()
                self.remaining_flag=0
            
            if(self.set_flag): # Change the focuser position
                new_steps=self.new_pos-foc.stepper_position
                try:
                    foc.step_motor(new_steps,blocking=False)
                except:
                    print 'Error moving focuser'
                self.set_flag=0
            
            if(self.move_flag): # Change the focuser position
                self.new_pos=foc.stepper_position+self.steps
                try:
                    foc.step_motor(self.steps,blocking=False)
                except:
                    print 'Error moving focuser'
                self.move_flag=0
            
            if(self.home_flag): # Home the focuser
                try:
                    foc.home_focuser()
                except FLIError:
                    print 'Error moving focuser'
                self.home_flag=0
            
        self.logfile.log('Focuser control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Focuser control functions
    def get_info(self):
        self.get_info_flag=1
    def set_focuser(self,pos):
        self.remaining_flag=1
        time.sleep(0.1)
        if self.remaining>0:
            return 'Motor is still moving'
        elif pos > self.limit:
            return 'End position past limits'
        else:
            self.new_pos=pos
            self.set_flag=1
    def move_focuser(self,steps):
        self.remaining_flag=1
        time.sleep(0.1)
        if self.remaining>0:
            return 'Motor is still moving'
        elif (self.new_pos+steps) > self.limit:
            return 'End position past limits'
        else:
            self.steps=steps
            self.move_flag=1
    def home_focuser(self):
        self.remaining_flag=1
        time.sleep(0.1)
        if self.remaining>0:
            return 'Motor is still moving'
        else:
            self.home_flag=1
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Daemon pinger
    def ping(self):
        #print '  pinged'
        dt_control = abs(time.time()-self.time_check)
        if dt_control > params.DAEMONS['foc']['PINGLIFE']:
            return 'Last focuser daemon control thread time check: %.1f seconds ago' % dt_control
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

pyro_daemon=Pyro4.Daemon(host=params.DAEMONS['foc']['HOST'], port=params.DAEMONS['foc']['PORT'])
filt_daemon=Filt_Daemon()

uri=pyro_daemon.register(filt_daemon,objectId = params.DAEMONS['foc']['PYROID'])

print 'Starting focuser daemon, with Pyro URI:',uri

Pyro4.config.COMMTIMEOUT=5.
pyro_daemon.requestLoop(loopCondition=filt_daemon.status_function)
print 'Exiting focuser daemon'
time.sleep(1.)
