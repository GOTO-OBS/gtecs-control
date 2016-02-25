#!/usr/bin/env python

########################################################################
#                            dome_daemon.py                            #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#             G-TeCS daemon to control an AstroHaven dome              #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
import os, sys, commands
from math import *
import time, datetime
import Pyro4
import threading
# TeCS modules
from tecs_modules import flags
from tecs_modules import logger
from tecs_modules import misc
from tecs_modules import params

########################################################################
# Dome daemon functions
class DomeDaemon:
    """
    Dome daemon class
    
    Contains x functions:
    - get_info()

    """
    def __init__(self):
        self.running = True
        self.start_time = time.time()
        
        ### set up logfile
        self.logfile = logger.Logfile('dome',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### command flags
        self.get_info_flag = 1
        self.open_flag = 0
        self.close_flag = 0
        self.halt_flag = 0
        
        ### dome variables
        self.info = {}
        self.status_flag = 1
        self.dome_status = {'dome':'unknown', 'hatch':'unknown', 'estop':'unknown', 'monitorlink':'unknown'}
        self.weather_check = 0
        self.count = 0
        self.last_hatch_status = None
        self.last_estop_status = None
        self.power_status = None
        self.move_side = 'both'
        self.move_steps = None
        
        ### start control thread
        t = threading.Thread(target=self.dome_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def dome_control(self):
        
        ### connect to dome object
        dome = params.DOME
        
        while(self.running):
            self.time_check = time.time()
            
            # check dome status every 5 seconds
            delta = self.time_check - self.start_time
            if (delta % 30 < 1 and self.status_flag == 1) or self.status_flag == -1:
                
                # get current dome status
                self.dome_status = dome.status()
                if self.dome_status == None:
                    self.logfile.log('Failed to get dome status')
                    continue
                
                # ping the power sources
                #pinglist = ['power1', 'power2', 'power3', 'scope', 'video', 'reg']
                #self.power_status = misc.check_hosts(pinglist)
                
                # check any external flags
                condition_flags = flags.Conditions()
                override_flags = flags.Overrides()
                
                # test for an emergency
                #if misc.loopback_test(params.BIG_RED_BUTTON_PORT,'bob',chances=3):
                #    self.logfile.log('Emergency shutdown button pressed',1)
                #    os.system('touch %s' % params.EMERGENCY_FILE)
                if self.power_status:
                    self.logfile.log('No external power')
                    os.system('touch ' + str(params.EMERGENCY_FILE))
                
                # in case of emergency
                if os.path.isfile(params.EMERGENCY_FILE) and self.dome_status['dome'] != 'closed':
                    self.logfile.log('Closing dome (emergency!)')
                    self.close_flag = 0
                elif self.weather_check > 0 and override_flags.dome_auto < 1:
                    if condition_flags.summary > 0:
                        self.logfile.log('Conditions bad, auto-closing dome')
                        elf.close_flag = 0

                self.status_flag = 0
            if delta % 30 > 1:
                self.status_flag = 1
            
            ### control functions
            # request info
            if(self.get_info_flag):
                info = {}
                for key in ['dome','hatch']:
                    info[key] = self.dome_status[key]
                info['uptime'] = time.time() - self.start_time
                info['ping'] = time.time() - self.time_check
                now = datetime.datetime.utcnow()
                info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")
                if os.path.isfile(params.EMERGENCY_FILE):
                    info['emergency'] = 1
                else:
                    info['emergency'] = 0
                self.info = info
                self.get_info_flag = 0
            
            # open dome
            if(self.open_flag):
                # only open if allowed
                if override_flags.dome_auto <1 and condition_flags.summary > 0:
                    self.logfile.log('ERROR: Conditions bad, dome will not open')
                elif self.power_status:
                    self.logfile.log('ERROR: No external power, dome will not open')
                elif os.path.isfile(params.EMERGENCY_FILE):
                    self.logfile.log('ERROR: In emergency locked state, dome will not open')
                # open both sides
                elif self.move_side == 'both':
                    try:
                        self.logfile.log('Opening dome')
                        c = dome.open_full()
                        if c: print c
                    except:
                        self.logfile.log('ERROR: Failed to open dome')
                # open only one side
                elif self.move_side in ['east','west']:
                    try:
                        self.logfile.log('Opening %s side of dome' %self.move_side)
                        c = dome.open_side(self.move_side, self.move_steps)
                        if c: print c
                    except:
                        self.logfile.log('ERROR: Failed to open dome')
                self.weather_check = 1
                self.move_side = 'both'
                self.move_steps = 0
                self.open_flag = 0
                self.status_flag = -1
            
            # close dome
            if(self.close_flag):
                # open both sides
                if self.move_side == 'both':
                    try:
                        self.logfile.log('Closing dome')
                        c = dome.close_full()
                        if c: print c
                    except:
                        self.logfile.log('ERROR: Failed to close dome')
                # open only one side
                elif self.move_side in ['east','west']:
                    try:
                        self.logfile.log('Closing %s side of dome' %self.move_side)
                        c = dome.close_side(self.move_side, self.move_steps)
                        if c: print c
                    except:
                        self.logfile.log('ERROR: Failed to open dome')
                self.weather_check = 1
                self.move_side = 'both'
                self.move_steps = 0
                self.close_flag = 0
                self.status_flag = -1
            
            # halt dome motion
            if(self.halt_flag):
                try:
                    self.logfile.log('Halting dome')
                    c = dome.halt()
                    if c: print c
                except:
                    self.logfile.log('ERROR: Failed to halt dome')
                self.halt_flag = 0
                self.status_flag = -1
            
            time.sleep(0.0001) # To save 100% CPU usage
        
        self.logfile.log('Dome control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Dome control functions
    def get_info(self):
        """Return dome status info"""
        self.get_info_flag = 1
        time.sleep(0.1)
        return self.info
    
    def open_dome(self,side,steps):
        """Open the dome"""
        if flags.Overrides().dome_auto < 1 and flags.Conditions().summary > 0:
            return 'ERROR: Conditions bad, dome will not open'
        elif self.power_status:
            return 'ERROR: No external power, dome will not open'
        elif os.path.isfile(params.EMERGENCY_FILE):
            return 'ERROR: In emergency locked state, dome will not open'
        else:
            self.open_flag = 1
            self.move_side = side
            self.move_steps = steps
            return 'Opening dome'
    
    def close_dome(self,side,steps):
        """Close the dome"""
        self.close_flag = 1
        self.move_side = side
        self.move_steps = steps
        return 'Closing dome'
    
    def halt_dome(self):
        """Stope the dome moving"""
        self.halt_flag = 1
        return 'Halting dome'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS['dome']['PINGLIFE']:
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
pyro_daemon = Pyro4.Daemon(host=params.DAEMONS['dome']['HOST'], port=params.DAEMONS['dome']['PORT'])
dome_daemon = DomeDaemon()

uri = pyro_daemon.register(dome_daemon,objectId = params.DAEMONS['dome']['PYROID'])
print 'Starting dome daemon at',uri

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=dome_daemon.status_function)

print 'Exiting dome daemon'
time.sleep(1.)
