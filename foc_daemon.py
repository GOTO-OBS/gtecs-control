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
from math import *
import time
import Pyro4
import threading
# FLI modules
from fliapi import FakeFocuser
# TeCS modules
from tecs_modules import logger
from tecs_modules import misc
from tecs_modules import params

########################################################################
# Focuser daemon functions
class FocDaemon:
    """
    Focuser daemon class
    
    Contains 4 functions:
    - get_info()
    - set_focuser(pos)
    - move_focuser(steps)
    - home_focuser()
    """
    def __init__(self):
        self.running = True
        self.start_time = time.time()
        
        ### set up logfile
        self.logfile = logger.Logfile('foc',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### function flags
        self.get_info_flag = 1
        self.set_focuser_flag = 0
        self.move_focuser_flag = 0
        self.home_focuser_flag = 0
        
        ### focuser variables
        self.info = {}
        self.limit = 2000
        self.current_pos = 0
        self.new_pos = 0
        self.move_steps = 0
        self.remaining = 0
        
        ### start control thread
        t = threading.Thread(target=self.foc_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def foc_control(self):
        
        ### connect to (fake) focuser
        foc = FakeFocuser('device','serial')
        
        while(self.running):
            self.time_check = time.time()
            
            ### control functions
            # request info
            if(self.get_info_flag):
                # update variables
                self.limit = foc.max_extent
                self.remaining = foc.get_steps_remaining()
                self.current_pos = foc.stepper_position
                self.int_temp = foc.read_temperature('internal')
                self.ext_temp = foc.read_temperature('external')
                # save info
                info = {}
                if self.remaining > 0:
                    info['status'] = 'Moving'
                    info['remaining'] = self.remaining
                else:
                    info['status'] = 'Ready'
                info['current_pos'] = self.current_pos
                info['limit'] = self.limit
                info['int_temp'] = self.int_temp
                info['ext_temp'] = self.ext_temp
                info['uptime'] = time.time()-self.start_time
                info['ping'] = time.time()-self.time_check
                self.info = info
                self.get_info_flag = 0
            
            # move the focuser to position
            if(self.set_focuser_flag):
                self.current_pos = foc.stepper_position
                self.move_steps = self.new_pos - self.current_pos
                self.logfile.log('Moving focuser by %i to %i'\
                    %(self.move_steps,self.new_pos))
                c = foc.step_motor(self.move_steps, blocking=False)
                if c: print c
                self.set_focuser_flag = 0
            
            # move the focuser by steps
            if(self.move_focuser_flag):
                self.current_pos = foc.stepper_position
                self.new_pos = self.current_pos + self.move_steps
                self.logfile.log('Moving focuser by %i to %i'\
                    %(self.move_steps,self.new_pos))
                c = foc.step_motor(self.move_steps, blocking=False)
                if c: print c
                self.move_focuser_flag = 0
            
            # home the focuser
            if(self.home_focuser_flag):
                self.logfile.log('Homing focuser')
                c = foc.home_focuser()
                if c: print c
                self.home_focuser_flag = 0
            
            time.sleep(0.0001) # To save 100% CPU usage
        
        self.logfile.log('Focuser control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Focuser control functions
    def get_info(self):
        """Return focuser status info"""
        self.get_info_flag = 1
        time.sleep(0.1)
        return self.info
    
    def set_focuser(self,new_pos):
        """Move focuser motor to given position"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.remaining > 0:
            return 'ERROR: Motor is still moving'
        elif new_pos > self.limit:
            return 'ERROR: End position past limits'
        else:
            self.new_pos = new_pos
            self.set_focuser_flag = 1
            return 'Moving focuser'
    
    def move_focuser(self,move_steps):
        """Move focuser motor by given number of steps"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.remaining > 0:
            return 'ERROR: Motor is still moving'
        elif (self.current_pos + move_steps) > self.limit:
            return 'ERROR: End position past limits'
        else:
            self.move_steps = move_steps
            self.move_focuser_flag = 1
            return 'Moving focuser'
    
    def home_focuser(self):
        """Move the focuser to the home position"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.remaining > 0:
            return 'ERROR: Motor is still moving'
        else:
            self.home_focuser_flag = 1
            return 'Homing focuser'
    
    
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
# Create Pyro control server 
pyro_daemon = Pyro4.Daemon(host=params.DAEMONS['foc']['HOST'], port=params.DAEMONS['foc']['PORT'])
foc_daemon = FocDaemon()

uri = pyro_daemon.register(foc_daemon,objectId=params.DAEMONS['foc']['PYROID'])
print 'Starting focuser daemon at',uri

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=foc_daemon.status_function)

print 'Exiting focuser daemon'
time.sleep(1.)
