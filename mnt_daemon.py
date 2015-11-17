#!/usr/bin/env python2

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
from math import *
import time
import Pyro4
import threading
# TeCS modules
import X_params as params
import X_misc as misc
import X_logger as logger

########################################################################
# Mount daemon functions
class MntDaemon:
    """
    Mount daemon class
    
    Contains x functions:
    - get_info()
    - slew_to_radec(ra,dec)
    - slew_to_target()
    - start_tracking()
    - full_stop()
    - park()
    - unpark()
    - set_target_ra(ra)
    - set_target_dec(dec)
    - set_target(dec)
    - offset(direction)
    - set_step()
    """
    def __init__(self):
        self.running = True
        self.start_time = time.time()
        
        ### set up logfile
        self.logfile = logger.Logfile('mnt',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### command flags
        self.get_info_flag = 1
        self.slew_radec_flag = 0
        self.slew_target_flag = 0
        self.start_tracking_flag = 0
        self.full_stop_flag = 0
        self.park_flag = 0
        self.unpark_flag = 0
        self.set_target_ra_flag = 0
        self.set_target_dec_flag = 0
        self.set_target_flag = 0
        
        ### mount variables
        self.info = {}
        self.step = params.DEFAULT_OFFSET_STEP
        self.mount_status = 'Unknown'
        self.mount_alt = 0
        self.mount_az = 0
        self.mount_ra = 0
        self.mount_dec = 0
        self.target_ra = None
        self.target_dec = None
        self.target_distance = None
        self.lst = 0
        self.utc = time.gmtime(time.time())
        self.utc_str = time.strftime('%Y-%m-%d %H:%M:%S', self.utc)
        self.temp_ra = None
        self.temp_dec = None
        
        ### start control thread
        t = threading.Thread(target=self.mnt_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def mnt_control(self):
        
        
        while(self.running):
            self.time_check = time.time()

            ### connect to sitech daemon
            sitech = Pyro4.Proxy(params.SITECH_ADDRESS)
            
            ### control functions
            # request info
            if(self.get_info_flag):
                # update variables
                try:
                    self.mount_status = sitech.get_mount_status()
                    self.mount_alt, self.mount_az = sitech.get_mount_altaz()
                    self.mount_ra, self.mount_dec = sitech.get_mount_radec()
                    self.target_ra, self.target_dec = sitech.get_target_radec()
                    self.target_distance = sitech.get_target_distance()
                    self.lst = sitech.get_lst()
                    self.utc = time.gmtime(time.time())
                    self.utc_str = time.strftime('%Y-%m-%d %H:%M:%S', self.utc)
                except:
                    print 'ERROR: No response from sitech daemon'
                # save info
                info = {}
                info['status'] = self.mount_status
                info['mount_alt'] = self.mount_alt
                info['mount_az'] = self.mount_az
                info['mount_ra'] = self.mount_ra
                info['mount_dec'] = self.mount_dec
                info['target_ra'] = self.target_ra
                info['target_dec'] = self.target_dec
                info['target_dist'] = self.target_distance
                info['lst'] = self.lst
                info['ha'] = misc.find_ha(self.mount_ra,self.lst)
                info['utc'] = self.utc_str
                info['step'] = self.step
                info['uptime'] = time.time()-self.start_time
                info['ping'] = time.time()-self.time_check
                self.info = info
                self.get_info_flag = 0
            
            # slew to given coordinates 
            if(self.slew_radec_flag):
                self.logfile.log('Slewing to %.2f,%.2f'\
                    %(self.temp_ra,self.temp_dec))
                try:
                    c = sitech.slew_to_radec(self.temp_ra,self.temp_dec)
                    if c: print c
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.slew_radec_flag = 0
                self.temp_ra = None
                self.temp_dec = None
            
            # slew to target
            if(self.slew_target_flag):
                self.logfile.log('Slewing to target')
                try:
                    c = sitech.slew_to_target()
                    if c: print c
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.slew_target_flag = 0
            
            # start tracking
            if(self.start_tracking_flag): 
                try:
                    c = sitech.start_tracking()
                    if c: print c
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.start_tracking_flag = 0
            
            # stop all motion (tracking or slewing)
            if(self.full_stop_flag):
                try:
                    c = sitech.full_stop()
                    if c: print c
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.full_stop_flag = 0
            
            # park the mount
            if(self.park_flag):
                try:
                    c = sitech.park()
                    if c: print c
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.park_flag = 0
            
            # unpark the mount
            if(self.unpark_flag):
                try:
                    c = sitech.unpark()
                    if c: print c
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.unpark_flag = 0
            
            # Set target RA
            if(self.set_target_ra_flag):
                try:
                    print 'set ra to',self.temp_ra
                    c = sitech.set_target_ra(self.temp_ra)
                    if c: print c
                    print 'again, set ra to',self.temp_ra
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.set_target_ra_flag = 0
                self.temp_ra = None
            
            # Set target Dec
            if(self.set_target_dec_flag):
                try:
                    c = sitech.set_target_dec(self.temp_dec)
                    if c: print c
                    print 'set dec to',self.temp_dec
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.set_target_dec_flag = 0
                self.temp_dec = None
            
            # Set target
            if(self.set_target_flag):
                try:
                    c = sitech.set_target(self.temp_ra,self.temp_dec)
                    if c: print c
                except:
                    print 'ERROR: No responce from sitech daemon'
                self.set_target_flag = 0
                self.temp_ra = None
                self.temp_dec = None
            
        self.logfile.log('Mount control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount control functions
    def get_info(self):
        """Return mount status info"""
        self.get_info_flag = 1
        time.sleep(0.1)
        return self.info
    
    def slew_to_radec(self,ra,dec):
        """Slew to specified coordinates"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            return 'ERROR: Already slewing'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked, need to unpark before slewing'
        elif misc.check_alt_limit(ra,dec,self.lst):
            return 'ERROR: Target too low, cannot slew'
        else:
            self.temp_ra = ra
            self.temp_dec = dec
            self.slew_radec_flag = 1
            return 'Slewing to coordinates'
    
    def slew_to_target(self):
        """Slew to current set target"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            return 'ERROR: Already slewing'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked, need to unpark before slewing'
        elif misc.check_alt_limit(self.target_ra,self.target_dec,self.lst):
            return 'ERROR: Target too low, cannot slew'
        else:
            self.slew_target_flag = 1
            return 'Slewing to target'
    
    def start_tracking(self):
        """Starts mount tracking"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Tracking':
            return 'ERROR: Already tracking'
        elif self.mount_status == 'Slewing':
            return 'ERROR: Currently slewing, will track when reached target'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked'
        else:
            self.start_tracking_flag = 1
            return 'Started tracking'
    
    def full_stop(self):
        """Stops mount moving (slewing or tracking)"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Stopped':
            return 'ERROR: Already stopped'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked'
        else:
            self.full_stop_flag = 1
            return 'Stopping mount'
    
    def park(self):
        """Moves the mount to the park position"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Parked':
            return 'ERROR: Already parked'
        else:
            self.park_flag = 1
            return 'Parking mount'
    
    def unpark(self):
        """Unpark the mount"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status != 'Parked':
            return 'ERROR: Mount is not parked'
        else:
            self.unpark_flag = 1
            return 'Unparking mount'
    
    def set_target_ra(self,ra):
        """Set the target RA"""
        self.temp_ra = ra
        self.set_target_ra_flag = 1
        return """Setting target RA"""
        
    def set_target_dec(self,dec):
        """Set the target Dec"""
        self.temp_dec = dec
        self.set_target_dec_flag = 1
        return """Setting target Dec"""
        
    def set_target(self,ra,dec):
        """Set the target location"""
        self.temp_ra = ra
        self.temp_dec = dec
        self.set_target_flag = 1
        return """Setting target"""
    
    def offset(self,direction):
        """Offset in a specified (cardinal) direction"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            return 'ERROR: Already slewing'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked'
        elif direction not in ['north','south','east','west']:
            return 'ERROR: Invalid direction'
        else:
            step_deg = self.step/3600.
            step_ra = (step_deg*24./360.)/cos(self.mount_dec)
            step_dec = step_deg
            if direction == 'north':
                ra = self.mount_ra
                dec = self.mount_dec + step_dec
            elif direction == 'south':
                ra = self.mount_ra
                dec = self.mount_dec - step_dec
            elif direction == 'east':
                ra = self.mount_ra + step_ra
                dec = self.mount_dec
            elif direction == 'west':
                ra = self.mount_ra - step_ra
                dec = self.mount_dec
            if misc.check_alt_limit(ra,dec,self.lst):
                return 'ERROR: Target too low, cannot slew'
            else:
                self.temp_ra = ra
                self.temp_dec = dec
                self.slew_radec_flag = 1
                return 'Slewing to offset coordinates'
    
    def set_step(self,offset):
        self.step = offset
        return 'New offset step set'
   
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS['mnt']['PINGLIFE']:
            return 'ERROR: Last control thread time check: %.1f seconds ago' % dt_control
        else:
            return 'ping'
    
    def prod(self):
        return
    
    def status_function(self):
        return self.running
    
    def shutdown(self):
        self.running=False

########################################################################
# Create Pyro control server 
pyro_daemon = Pyro4.Daemon(host=params.DAEMONS['mnt']['HOST'], port=params.DAEMONS['mnt']['PORT'])
mnt_daemon = MntDaemon()

uri=pyro_daemon.register(mnt_daemon,objectId = params.DAEMONS['mnt']['PYROID'])
print 'Starting mount daemon at',uri

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=mnt_daemon.status_function)

print 'Exiting mount daemon'
time.sleep(1.)
