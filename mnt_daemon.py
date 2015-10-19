#!/usr/bin/env python

########################################################################
#                            mnt_daemon.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#        G-TeCS daemon to access SiTech mount control via ASCOM        #
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
# TeCS modules
import X_params as params
import X_misc as misc
import X_logger as logger

########################################################################
# Mount Daemon functions
class Mnt_Daemon:
    def __init__(self):
        
        ### activate
        self.running=True
        
        ### find current username
        self.username=os.environ["LOGNAME"]

        ### set up logfile
        self.logfile = logger.Logfile('mnt',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### initiate flags
        self.get_info_flag=0
        self.ping_flag=0
        self.start_tracking_flag=0
        self.full_stop_flag=0
        self.park_flag=0
        self.unpark_flag=0
        self.ra_flag=0
        self.dec_flag=0
        self.slew_flag=0
        
        ### target data
        self.target_ra=0
        self.target_dec=0
        
        ### status
        self.info='None yet'
        self.step = params.DEFAULT_OFFSET_STEP
        
        ### timing
        self.start_time=time.time()   #used for uptime
        
        ### start control thread
        t=threading.Thread(target=self.mnt_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control function
    def mnt_control(self):
        while(self.running):
            self.time_check = time.time()   #used for "ping"
                    
            ### Connect to SiTech daemon
            SITECH_ADDRESS = 'PYRO:sitech_daemon@137.205.160.30:7766'
            SiTech = Pyro4.Proxy(SITECH_ADDRESS)
            
            ### Time stuff
            teltime_str = SiTech.get_info()['teltime']
            teltime = time.strptime(teltime_str, '%Y-%m-%d %H:%M:%S')
            telsec = time.mktime(teltime)
            self.ut = time.gmtime(telsec)
            #self.lst = misc.find_lst(params.SITE_LONGITUDE,telsec)
            self.lst = SiTech.get_info()['lst']
            
            ### control functions
            if(self.get_info_flag): # Request info
                try:
                    new_info = SiTech.get_info()
                    self.info = new_info
                except:
                    print 'No response from SiTech daemon'
                self.get_info_flag=0
            
            if(self.ping_flag): # Ping the SiTech daemon
                try:
                    ping = SiTech.ping()
                except:
                    print 'No response from SiTech daemon'
                self.ping_flag=0
                
            if(self.start_tracking_flag): # Start tracking
                try:
                    SiTech.start_tracking()
                except:
                    print 'No responce from SiTech daemon'
                self.start_tracking_flag=0
            
            if(self.full_stop_flag): # Stop tracking/slewing
                try:
                    SiTech.full_stop()
                except:
                    print 'No responce from SiTech daemon'
                self.full_stop_flag=0
            
            if(self.park_flag): # Park the mount
                try:
                    SiTech.park()
                except:
                    print 'No responce from SiTech daemon'
                self.park_flag=0
            
            if(self.unpark_flag): # Unpark the mount
                try:
                    SiTech.unpark()
                except:
                    print 'No responce from SiTech daemon'
                self.unpark_flag=0
            
            if(self.ra_flag): # Set target RA
                try:
                    SiTech.set_ra(self.target_ra)
                except:
                    print 'No responce from SiTech daemon'
                self.ra_flag=0
            
            if(self.dec_flag): # Set target dec
                try:
                    SiTech.set_dec(self.target_dec)
                except:
                    print 'No responce from SiTech daemon'
                self.dec_flag=0
            
            if(self.slew_flag): # Slew to target RA & Dec
                try:
                    SiTech.slew()
                except:
                    print 'No responce from SiTech daemon'
                self.slew_flag=0
            
        self.logfile.log('Mount control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount control functions
    def get_info(self):
        self.get_info_flag=1
    def pingS(self):
        self.ping_flag=1
    def start_tracking(self):
        self.start_tracking_flag=1
    def full_stop(self):
        self.full_stop_flag=1
    def park(self):
        self.park_flag=1
    def unpark(self):
        self.unpark_flag=1
    def n(self):
        self.unpark_flag=1
    def s(self):
        self.unpark_flag=1
    def w(self):
        self.unpark_flag=1
    def e(self):
        self.unpark_flag=1
    def ra(self,h,m,s):
        self.target_ra=h+m/60.+s/3600.
        self.ra_flag=1
    def dec(self,sign,d,m,s):
        if sign=='+':
            self.target_dec=d+m/60.+s/3600.
        else:
            self.target_dec=-d-m/60.-s/3600.
        self.dec_flag=1
    def slew(self,ra=None,dec=None):
        if ra is not None:
            self.target_ra=ra
            self.ra_flag=1
            time.sleep(0.1) # time to set target
        if dec is not None:
            self.target_dec=dec
            self.dec_flag=1
            time.sleep(0.1) # time to set target
        
        if misc.check_alt_limit(self.target_ra,self.target_dec,self.lst):
            print 'Asked to slew to target below horizon'
            return 'Target too low, cannot slew'
        else:        
            self.slew_flag=1
    
    def offset_dec(self,direction):
        step_deg = self.step/3600.
        if direction=='north':
            self.target_dec+=step_deg
        elif direction=='south':
            self.target_dec+=-step_deg
        self.dec_flag=1
        time.sleep(0.1)
        if misc.check_alt_limit(self.target_ra,self.target_dec,self.lst):
            print 'Asked to slew to target below horizon'
            return 'Target too low, cannot slew'
        else:        
            self.slew_flag=1
    
    def offset_ra(self,direction):
        step_deg = (self.step/3600.)/cos(self.target_dec)
        step_hrs = step_deg*24./360.
        if direction=='east':
            self.target_ra+=step_hrs
        elif direction=='west':
            self.target_ra+=-step_hrs
        self.ra_flag=1
        time.sleep(0.1)
        if misc.check_alt_limit(self.target_ra,self.target_dec,self.lst):
            print 'Asked to slew to target below horizon'
            return 'Target too low, cannot slew'
        else:        
            self.slew_flag=1
    
    def set_step(self,offset):
        self.step=offset
        print 'New offset:', self.step
        
        
        
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
        if data == 'status':
            return self.status
        elif data == 'info':
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

pyro_daemon=Pyro4.Daemon(host=params.DAEMONS['mnt']['HOST'], port=params.DAEMONS['mnt']['PORT'])
mnt_daemon=Mnt_Daemon()

uri=pyro_daemon.register(mnt_daemon,objectId = params.DAEMONS['mnt']['PYROID'])

print 'Starting mount daemon, with Pyro URI:',uri

Pyro4.config.COMMTIMEOUT=5.
pyro_daemon.requestLoop(loopCondition=mnt_daemon.status_function)
print 'Exiting mount daemon'
time.sleep(1.)
