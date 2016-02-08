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
from math import *
import time
import Pyro4
import threading
# TeCS modules
from tecs_modules import logger
from tecs_modules import misc
from tecs_modules import params

########################################################################
# Focuser daemon functions
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
        self.logfile = logger.Logfile('foc',params.LOGGING)
        self.logfile.log('Daemon started')
        
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
        
        for nuc in params.FLI_INTERFACES:
            self.limit[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.current_pos[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.remaining[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.int_temp[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.ext_temp[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.move_steps[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
        
        self.active_tel = []
        
        ### start control thread
        t = threading.Thread(target=self.foc_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def foc_control(self):
        
        while(self.running):
            self.time_check = time.time()
            
            ### control functions
            # request info
            if(self.get_info_flag):
                # update variables
                for tel in self.tel_dict.keys():
                    nuc, HW = self.tel_dict[tel]
                    fli = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
                    fli._pyroTimeout = params.PROXY_TIMEOUT
                    try:
                        self.limit[nuc][HW] = fli.get_focuser_limit(HW)
                        self.remaining[nuc][HW] = fli.get_focuser_steps_remaining(HW)
                        self.current_pos[nuc][HW] = fli.get_focuser_position(HW)
                        self.int_temp[nuc][HW] = fli.get_focuser_temp('internal',HW)
                        self.ext_temp[nuc][HW] = fli.get_focuser_temp('external',HW)
                    except:
                        print 'ERROR: No response from fli interface on', nuc
                # save info
                info = {}
                for tel in self.tel_dict.keys():
                    nuc, HW = self.tel_dict[tel]
                    tel = str(params.FLI_INTERFACES[nuc]['TELS'][HW])
                    if self.remaining[nuc][HW] > 0:
                        info['status'+tel] = 'Moving'
                        info['remaining'+tel] = self.remaining[nuc][HW]
                    else:
                        info['status'+tel] = 'Ready'
                    info['current_pos'+tel] = self.current_pos[nuc][HW]
                    info['limit'+tel] = self.limit[nuc][HW]
                    info['int_temp'+tel] = self.int_temp[nuc][HW]
                    info['ext_temp'+tel] = self.ext_temp[nuc][HW]
                info['uptime'] = time.time()-self.start_time
                info['ping'] = time.time()-self.time_check
                
                self.info = info
                self.get_info_flag = 0
            
            # move the focuser
            if(self.move_focuser_flag):
                # loop through each unit to send orders to in turn
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]
                    move_steps = self.move_steps[nuc][HW]
                    new_pos = self.current_pos[nuc][HW] - move_steps
                    
                    self.logfile.log('Moving focuser %i (%s-%i) by %i to %i'\
                        %(tel, nuc, HW, move_steps, new_pos))
                    
                    fli = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
                    fli._pyroTimeout = params.PROXY_TIMEOUT
                    try:
                        c = fli.step_focuser_motor(move_steps,HW)
                        if c: print c
                    except:
                        print 'ERROR: No response from fli interface on', nuc
                # cleare the 'active' units
                self.active_tel = []
                
                self.move_focuser_flag = 0
            
            # home the focuser
            if(self.home_focuser_flag):
                # loop through each unit to send orders to in turn
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]
                    
                    self.logfile.log('Homing focuser %i (%s-%i)'\
                        %(tel, nuc, HW) )
                    
                    fli = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
                    fli._pyroTimeout = params.PROXY_TIMEOUT
                    try:
                        c = fli.home_focuser(HW)
                        if c: print c
                    except:
                        print 'ERROR: No response from fli interface on', nuc
                # cleare the 'active' units
                self.active_tel = []
                
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
    
    def set_focuser(self,new_pos,tel_list):
        """Move focuser to given position"""
        for tel in tel_list:
            if tel not in self.tel_dict.keys():
                return 'ERROR: Unit telescope ID not in list %s' %str(self.tel_dict.keys())
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            nuc, HW = self.tel_dict[tel]
            if self.remaining[nuc][HW] > 0:
                s += '\n  ERROR: Focuser %i motor is still moving' %tel
            elif new_pos > self.limit[nuc][HW]:
                s += '\n  ERROR: Position past limit'
            else:
                self.active_tel += [tel]
                self.move_steps[nuc][HW] = new_pos - self.current_pos[nuc][HW]
                s += '\n  Moving focuser %i' %tel
        self.move_focuser_flag = 1
        return s
    
    def move_focuser(self,move_steps,tel_list):
        """Move focuser by given number of steps"""
        for tel in tel_list:
            if tel not in self.tel_dict.keys():
                return 'ERROR: Unit telescope ID not in list %s' %str(self.tel_dict.keys())
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            nuc, HW = self.tel_dict[tel]
            if self.remaining[nuc][HW] > 0:
                s += '\n  ERROR: Focuser %i motor is still moving' %tel
            elif (self.current_pos[nuc][HW] + move_steps) > self.limit[nuc][HW]:
                s += '\n  ERROR: Position past limit'
            else:
                self.active_tel += [tel]
                self.move_steps[nuc][HW] = move_steps
                s += '\n  Moving focuser %i' %tel
        self.move_focuser_flag = 1
        return s
    
    def home_focuser(self,tel_list):
        """Move focuser to the home position"""
        for tel in tel_list:
            if tel not in self.tel_dict.keys():
                return 'ERROR: Unit telescope ID not in list %s' %str(self.tel_dict.keys())
        self.get_info_flag = 1
        time.sleep(0.1)
        s = 'Moving:'
        for tel in tel_list:
            nuc, HW = self.tel_dict[tel]
            if self.remaining[nuc][HW] > 0:
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
# Create Pyro control server
pyro_daemon = Pyro4.Daemon(host=params.DAEMONS['foc']['HOST'], port=params.DAEMONS['foc']['PORT'])
foc_daemon = FocDaemon()

uri = pyro_daemon.register(foc_daemon,objectId = params.DAEMONS['foc']['PYROID'])
print 'Starting focuser daemon at',uri

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=foc_daemon.status_function)

print 'Exiting focuser daemon'
time.sleep(1.)
