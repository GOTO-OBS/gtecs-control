#!/usr/bin/env python

########################################################################
#                            cam_daemon.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                  G-TeCS daemon to control FLI camera                 #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from math import *
import time, datetime
import Pyro4
import threading
import os
import astropy.io.fits as pyfits
# FLI modules
from fliapi import FakeCamera
# TeCS modules
from tecs_modules import logger
from tecs_modules import misc
from tecs_modules import params

########################################################################
# Camera daemon functions
class CamDaemon:
    """
    Camera daemon class
    
    Contains 7 functions:
    - get_info()
    - take_image(exptime,frametype)
    - abort_exposure()
    - set_temp(target_temp)
    - set_flushes(target_flushes)
    - set_binning(hbin,vbin)
    - set_area(ul_x, ul_y, lr_x, lr_y)
    """
    def __init__(self):
        self.running = True
        self.start_time = time.time()
        
        ### set up logfile
        self.logfile = logger.Logfile('cam',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### command flags
        self.get_info_flag = 1
        self.take_exposure_flag = 0
        self.save_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.set_temp_flag = 0
        self.set_flushes_flag = 0
        self.set_binning_flag = 0
        self.set_area_flag = 0
        
        ### camera variables
        self.info = {}
        self.ftlist = params.FRAMETYPE_LIST
        self.exptime = 1
        self.frametype = 'normal'
        self.image = 'None yet'
        self.target_temp = 10
        self.target_flushes = 1
        self.hbin = 1
        self.vbin = 1
        self.ul_x = 0
        self.ul_y = 0
        self.lr_x = 0
        self.lr_y = 0
        self.timeleft = 0
        
        ### start control thread
        t = threading.Thread(target=self.cam_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def cam_control(self):
        
        ### connect to (fake) camera
        cam = FakeCamera('device','serial')
        
        while(self.running):
            self.time_check = time.time()
            
            ### control functions
            # request info
            if(self.get_info_flag):
                # update variables
                self.timeleft = cam.get_exposure_timeleft()
                # save info
                info = cam.get_info()
                if self.timeleft > 0:
                    info['status'] = 'Exposing'
                    info['timeleft'] = self.timeleft/1000.
                else:
                    info['status'] = 'Ready'
                info['frametype'] = self.frametype
                info['exptime'] = self.exptime
                info['bins'] = (self.hbin,self.vbin)
                info['uptime'] = time.time()-self.start_time
                info['ping'] = time.time()-self.time_check
                self.info = info
                self.get_info_flag = 0
                
            # take exposure
            if(self.take_exposure_flag):
                exptime = self.exptime
                frametype = self.frametype
                self.logfile.log('Taking exposure: %is, %s'%(exptime,frametype))
                exptime_ms = exptime*1000.
                c = cam.set_exposure(exptime_ms,frametype)
                if c: print c
                c = cam.start_exposure()
                if c: print c
                self.take_exposure_flag = 0
                self.save_exposure_flag = 1
            
            # save image
            if(self.save_exposure_flag):
                self.timeleft = cam.get_exposure_timeleft()
                if self.timeleft == 0:
                    self.logfile.log('Fetching exposure')
                    self.image = cam.fetch_image()
                    filename = self.image_location()
                    self.logfile.log('Saving exposure to %s'%filename)
                    self.write_fits(self.image,filename)
                    self.save_exposure_flag = 0
            
            # abort exposure
            if(self.abort_exposure_flag):
                self.logfile.log('Aborting exposure')
                c = cam.cancel_exposure()
                if c: print c
                self.abort_exposure_flag = 0
            
            # set camera temperature
            if(self.set_temp_flag):
                target_temp = self.target_temp
                self.logfile.log('Setting temperature to %i'%target_temp)
                c = cam.set_temperature(target_temp)
                if c: print c
                self.set_temp_flag = 0
            
            # set number of flushes
            if(self.set_flushes_flag):
                target_flushes = self.target_flushes
                self.logfile.log('Setting number of flushes to %i'%target_flushes)
                c = cam.set_flushes(target_flushes)
                if c: print c
                self.set_flushes_flag = 0
            
            # set binning
            if(self.set_binning_flag):
                hbin = self.hbin
                vbin = self.vbin
                self.logfile.log('Setting bins to (%i,%i)'%(hbin,vbin))
                c = cam.set_image_binning(hbin,vbin)
                if c: print c
                self.set_binning_flag = 0
            
            # set active area
            if(self.set_area_flag):
                ul_x = self.ul_x
                ul_y = self.ul_y
                lr_x = self.lr_x
                lr_y = self.lr_y
                self.logfile.log('Setting area to (%i,%i,%i,%i)'%(ul_x, ul_y, lr_x, lr_y))
                c = cam.set_image_size(ul_x, ul_y, lr_x, lr_y)
                if c: print c
                self.set_area_flag = 0
            
            time.sleep(0.0001) # To save 100% CPU usage
        
        self.logfile.log('Camera control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    def get_info(self):
        """Return camera status info"""
        self.get_info_flag=1
        time.sleep(0.1)
        return self.info
    
    def take_image(self,exptime,frametype='normal'):
        """Take image with camera"""
        self.exptime = exptime
        self.frametype = frametype
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.timeleft != 0:
            return 'ERROR: Already exposing'
        if self.timeleft != 0:
            return 'ERROR: Already exposing'
        if frametype not in self.ftlist:
            return 'ERROR: Frame type must be in %s' %str(self.ftlist)
        else:
            self.take_exposure_flag = 1
            return "Taking image"
    
    def abort_exposure(self):
        """Abort current exposure"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.timeleft == 0:
            return 'ERROR: Not currently exposing'
        else:
            self.abort_exposure_flag = 1
            return "Aborting exposure"
    
    def set_temp(self,target_temp):
        """Set the camera's temperature"""
        self.target_temp = target_temp
        if not (-55 <= target_temp <= 45):
            return 'ERROR: Temperature must be between -55 and 45'
        else:
            self.set_temp_flag = 1
            return 'Setting temperature'
    
    def set_flushes(self,target_flushes):
        """Set the number of times to flush the CCD before an exposure"""
        self.target_flushes = target_flushes
        if not (0 <= target_flushes <= 16):
            return 'ERROR: Number of flushes must be between 0 and 16'
        else:
            self.set_flushes_flag = 1
            return 'Setting flushes'
    
    def set_binning(self,hbin,vbin=None):
        """Set the image binning"""
        self.hbin = hbin
        if vbin:
            self.vbin = vbin
            self.set_binning_flag = 1
            return 'Setting horizontal and vertical bins'
        else:
            self.vbin = hbin
            self.set_binning_flag = 1
            return 'Setting symmetric bins'
    
    def set_area(self,ul_x, ul_y, lr_x, lr_y):
        """Set the active image area"""
        self.ul_x = ul_x
        self.ul_y = ul_y
        self.lr_x = lr_x
        self.lr_y = lr_y
        self.set_area_flag = 1
        return 'Setting active area'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Image data functions
    def write_fits(self,image,filename):
        hdu = pyfits.PrimaryHDU(image)
        hdulist = pyfits.HDUList([hdu])
        if os.path.exists(filename): os.remove(filename)
        hdulist.writeto(filename)

    def image_location(self):
        # Find the date the observing night began, for the directory
        now = datetime.datetime.utcnow()
        if now.hour < 12: now = now - datetime.timedelta(days=1)
        night = now.strftime("%Y-%m-%d")
        direc = params.IMAGE_PATH + night
        if not os.path.exists(direc): os.mkdir(direc)
        # Find the run number, for the file name
        n = 0
        while os.path.exists(direc + '/%05i'%n + '.fits'):
            n += 1
        return direc + '/%05i'%n + '.fits'

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        dt_control = abs(time.time()-self.time_check)
        if dt_control > params.DAEMONS['cam']['PINGLIFE']:
            return 'Last control thread time check: %.1f seconds ago' %dt_control
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
pyro_daemon = Pyro4.Daemon(host=params.DAEMONS['cam']['HOST'], port=params.DAEMONS['cam']['PORT'])
cam_daemon = CamDaemon()

uri = pyro_daemon.register(cam_daemon,objectId = params.DAEMONS['cam']['PYROID'])
print 'Starting camera daemon at',uri

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=cam_daemon.status_function)

print 'Exiting camera daemon'
time.sleep(1.)
