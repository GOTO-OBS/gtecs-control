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
import os, sys, commands
from math import *
from string import split,find
import time
import Pyro4
import threading
import numpy
import astropy.io.fits as pyfits
# FLI modules
from fliapi import FakeCamera
# TeCS modules
import X_params as params
import X_misc as misc
import X_logger as logger

########################################################################
# Camera Daemon functions
class Cam_Daemon:
    def __init__(self):
        
        ### activate
        self.running=True
        
        ### find current username
        self.username=os.environ["LOGNAME"]

        ### set up logfile
        self.logfile = logger.Logfile('cam',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### initiate flags
        self.get_info_flag=0
        self.start_exposure_flag=0
        self.get_timeleft_flag=0
        self.abort_exposure_flag=0
        self.get_image_flag=0
        self.set_temp_flag=0
        self.set_flushes_flag=0
        self.set_binning_flag=0
        self.set_area_flag=0

        ### camera
        self.exptime = 1
        self.frametype = 'normal'
        self.image = 'None yet'
        self.target_temp = 10 #no idea
        self.target_flushes = 1 #don't know if we're using this
        self.hbin = 1
        self.vbin = 1
        self.ul_x = 0
        self.ul_y = 0
        self.lr_x = 0
        self.lr_y = 0
        
        ### exposure
        self.exposing = 0
        self.timeleft = 0
        self.abort = 0
        
        ### status
        self.info='None yet'
        
        ### timing
        self.start_time=time.time()   #used for uptime
        
        ### start control thread
        t=threading.Thread(target=self.cam_control)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control function
    def cam_control(self):
        
        ### connect to camera
        cam=FakeCamera('device','serial')
        
        while(self.running):
            self.time_check = time.time()   #used for "ping"

            ### control functions
            if(self.get_info_flag): # Request info
                info=cam.get_info()
                self.info = info
                if(self.exposing):
                    self.info['status']='Exposing'
                    self.info['timeleft']=cam.get_exposure_timeleft()
                else:
                    self.info['status']='Ready'
                
                self.info['frametype']=self.frametype
                self.info['exptime']=self.exptime
                self.info['bins']=(self.hbin,self.vbin)
                self.get_info_flag=0
            
            if(self.start_exposure_flag):
                exptime=self.exptime
                frametype=self.frametype
                cam.set_exposure(exptime,frametype)
                cam.start_exposure()
                self.start_exposure_flag=0
                self.exposing=1
            
            if(self.get_timeleft_flag):
                self.timeleft = cam.get_exposure_timeleft()
                self.get_timeleft_flag=0
            
            if(self.get_image_flag):
                self.image = cam.fetch_image()
                self.get_image_flag=0
            
            if(self.set_temp_flag):
                target_temp = self.target_temp
                cam.set_temperature(target_temp)
                self.set_temp_flag=0
            
            if(self.set_flushes_flag):
                target_flushes = self.target_flushes
                cam.set_flushes(target_flushes)
                self.set_flushes_flag=0
            
            if(self.set_binning_flag):
                hbin = self.hbin
                vbin = self.vbin
                cam.set_image_binning(hbin,vbin)
                self.set_binning_flag=0
            
            if(self.set_area_flag):
                ul_x = self.ul_x
                ul_y = self.ul_y
                lr_x = self.lr_x
                lr_y = self.lr_y
                cam.set_image_size(ul_x, ul_y, lr_x, lr_y)
                self.set_area_flag=0
            
            if(self.exposing):
                if(self.abort):
                    cam.cancel_exposure()
                    print 'Exposure aborted!'
                    self.exposing=0
                    self.abort=0
                self.timeleft=cam.get_exposure_timeleft()
                if self.timeleft == 0:
                    self.image = cam.fetch_image()
                    print 'Exposure finished!'
                    self.exposing=0
                    self.write_fits(self.image,'image.fits')
            
        self.logfile.log('Camera control thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    def get_info(self):
        self.get_info_flag=1
        time.sleep(0.1)
        return self.info
    
    def get_timeleft(self):
        self.get_timeleft_flag=1
        time.sleep(0.1)
        return self.timeleft
    
    def get_image(self):
        self.get_image_flag=1
    
    def set_temp(self,target_temp):
        self.target_temp=target_temp
        self.set_temp_flag=1
    
    def set_flushes(self,target_flushes):
        self.target_flushes=target_flushes
        self.set_flushes_flag=1
    
    def set_binning(self,hbin,vbin):
        self.hbin=hbin
        self.vbin=vbin
        self.set_binning_flag=1
    
    def set_area(self,ul_x, ul_y, lr_x, lr_y):
        self.ul_x = ul_x
        self.ul_y = ul_y
        self.lr_x = lr_x
        self.lr_y = lr_y
        self.set_area_flag=1
    
    def take_image(self,exptime,frametype):
        self.exptime=exptime
        self.frametype=frametype
        self.start_exposure_flag=1
    
    def abort_exposure(self):
        if(self.exposing):
            self.abort=1
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Image data functions
    def write_fits(self,image,filename):
        hdu = pyfits.PrimaryHDU(image)
        hdulist = pyfits.HDUList([hdu])
        if os.path.exists(filename): os.remove(filename)
        hdulist.writeto(filename)
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Daemon pinger
    def ping(self):
        #print '  pinged'
        dt_control = abs(time.time()-self.time_check)
        if dt_control > params.DAEMONS['cam']['PINGLIFE']:
            return 'Last camera daemon control thread time check: %.1f seconds ago' % dt_control
        else:
            return 'ping'
    
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

pyro_daemon=Pyro4.Daemon(host=params.DAEMONS['cam']['HOST'], port=params.DAEMONS['cam']['PORT'])
cam_daemon=Cam_Daemon()

uri=pyro_daemon.register(cam_daemon,objectId = params.DAEMONS['cam']['PYROID'])

print 'Starting camera daemon, with Pyro URI:',uri

Pyro4.config.COMMTIMEOUT=5.
pyro_daemon.requestLoop(loopCondition=cam_daemon.status_function)
print 'Exiting camera daemon'
time.sleep(1.)
