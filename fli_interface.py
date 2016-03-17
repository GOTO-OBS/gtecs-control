#!/usr/bin/env python

########################################################################
#                           fli_interface.py                           #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                G-TeCS interface to control FLI hardware              #
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
import numpy
import socket
# FLI modules
from fliapi import USBCamera, USBFocuser, USBFilterWheel
from fliapi import FakeCamera, FakeFocuser, FakeFilterWheel
# TeCS modules
from tecs_modules import logger
from tecs_modules import misc
from tecs_modules import params

########################################################################
# FLI control functions
class FLI:
    """
    FLI interface class
    
    Contains 22 functions:
    ::focuser::
    - step_focuser_motor(steps, HW)
    - home_focuser(HW):
    - get_focuser_limit(HW)
    - get_focuser_position(HW)
    - get_focuser_steps_remaining(HW)
    - get_focuser_temp(temp_type, HW):
       
    ::filter::
    - set_filter_pos(new_filter, HW)
    - home_filter(HW):
    - get_filter_number(HW)
    - get_filter_position(HW)
    - get_filter_steps_remaining(HW)
    
    ::camera::
    - set_exposure(exptime_ms, frametype, HW)
    - start_exposure(HW)
    - save_exposure(filename, HW)
    - abort_exposure(HW)
    - set_camera_temp(target_temp, HW)
    - set_camera_flushes(target_flushes, HW)
    - set_camera_bins(hbin, vbin, HW)
    - set_camera_area(ul_x, ul_y, lr_x, lr_y, HW)
    - get_camera_info(HW)
    - get_camera_time_remaining(HW)
    - get_camera_temp(temp_type, HW)
    - get_camera_cooler_power(HW)
    """
    def __init__(self):
        self.running = True
        
        ### find interface params
        self.hostname = socket.gethostname()
        for nuc in params.FLI_INTERFACES.keys():
            if params.FLI_INTERFACES[nuc]['HOST'] == self.hostname:
                self.nuc = nuc
        
        ### fli objects
        self.cams = []
        self.focs = []
        self.filts = []
        for HW in range(len(params.FLI_INTERFACES[self.nuc]['TELS'])):
            # cameras
            cam_serial = params.FLI_INTERFACES[self.nuc]['SERIALS']['cam'][HW]
            cam = USBCamera.locate_device(cam_serial)
            if cam == None: cam = FakeCamera('fake','Fake-Cam')
            self.cams.append(cam)
            # focusers
            foc_serial = params.FLI_INTERFACES[self.nuc]['SERIALS']['foc'][HW]
            foc = USBFocuser.locate_device(foc_serial)
            if foc == None: foc = FakeFocuser('fake','Fake-Foc')
            self.focs.append(foc)
            # filter wheels
            filt_serial = params.FLI_INTERFACES[self.nuc]['SERIALS']['filt'][HW]
            filt = USBFilterWheel.locate_device(filt_serial)
            if filt == None: filt = FakeFilterWheel('fake','Fake-Filt')
            self.filts.append(filt)
        
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Focuser control functions
    def step_focuser_motor(self, steps, HW):
        """Move focuser by given number of steps"""
        print 'Moving focuser',HW,'by',steps
        self.focs[int(HW)].step_motor(steps, blocking=False)
    
    def home_focuser(self, HW):
        """Move focuser to the home position"""
        print 'Homing focuser',HW
        self.focs[int(HW)].home_focuser()
    
    def get_focuser_limit(self, HW):
        """Return focuser motor limit"""
        lim = self.focs[int(HW)].max_extent
        return lim
    
    def get_focuser_position(self, HW):
        """Return focuser position"""
        pos = self.focs[int(HW)].stepper_position
        return pos
    
    def get_focuser_steps_remaining(self, HW):
        """Return focuser motor limit"""
        rem = self.focs[int(HW)].get_steps_remaining()
        return rem
    
    def get_focuser_temp(self, temp_type, HW):
        """Return focuser internal/external temperature"""
        tmp = self.focs[int(HW)].read_temperature(temp_type)
        return tmp
    
    def get_focuser_serial_number(self,HW):
        """Return focuser unique serial number"""
        ser = self.focs[int(HW)].serial_number
        return ser
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Filter wheel control functions
    def set_filter_pos(self, new_filter, HW):
        """Move filter wheel to position"""
        print 'Moving filter wheel',HW,'to position',new_filter
        self.filts[int(HW)].set_filter_pos(new_filter)
    
    def home_filter(self, HW):
        """Move filter wheel to home position"""
        print 'Homing filter wheel',HW
        self.filts[int(HW)].home()
    
    def get_filter_number(self, HW):
        """Return current filter number"""
        num = self.filts[int(HW)].get_filter_pos()
        return num
    
    def get_filter_position(self, HW):
        """Return filter wheel position"""
        pos = self.filts[int(HW)].stepper_position
        return pos
    
    def get_filter_steps_remaining(self, HW):
        """Return filter wheel steps remaining"""
        rem = self.filts[int(HW)].get_steps_remaining()
        return rem
    
    def get_filter_serial_number(self,HW):
       	"""Return filter wheel unique serial number"""
        ser = self.filts[int(HW)].serial_number
       	return ser
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    def set_exposure(self, exptime_ms, frametype, HW):
        """Set exposure time and frametype"""
        print 'Camera',HW,'starting ',str(exptime_ms/1000)+'s',frametype,'exposure'
        self.cams[int(HW)].set_exposure(exptime_ms, frametype)
    
    def start_exposure(self, HW):
        """Begin exposure"""
        self.cams[int(HW)].start_exposure()
    
    def save_exposure(self, filename, HW):
        """Fetch the image and save it temporarily"""
        print 'Camera',HW,'saving image'
        img = self.cams[int(HW)].fetch_image()
        numpy.save(filename,img)
    
    def abort_exposure(self, HW):
        """Abort current exposure"""
        print 'Camera',HW,'aborting exposure'
        self.cams[int(HW)].cancel_exposure()
    
    def set_camera_temp(self, target_temp, HW):
        """Set the camera's temperature"""
        self.cams[int(HW)].set_temperature(target_temp)
    
    def set_camera_flushes(self, target_flushes, HW):
        """Set the number of times to flush the CCD before an exposure"""
        self.cams[int(HW)].set_flushes(target_flushes)
    
    def set_camera_bins(self, hbin, vbin, HW):
        """Set the image binning"""
        self.cams[int(HW)].set_image_binning(hbin,vbin)
    
    def set_camera_area(self, ul_x, ul_y, lr_x, lr_y, HW):
        """Set the active image area"""
        self.cams[int(HW)].set_image_size(ul_x, ul_y, lr_x, lr_y)
    
    def get_camera_info(self, HW):
        """Return camera infomation dictionary"""
        dic = self.cams[int(HW)].get_info()
        return dic
    
    def get_camera_time_remaining(self, HW):
        """Return exposure time remaining"""
        rem = self.cams[int(HW)].get_exposure_timeleft()/1000.
        return rem
    
    def get_camera_temp(self, temp_type, HW):
        """Return camera CCD/base temperature"""
        tmp = self.cams[int(HW)].get_temperature(temp_type)
        return tmp
    
    def get_camera_cooler_power(self, HW):
        """Return peltier cooler power"""
        rem = self.cams[int(HW)].get_cooler_power()
        return rem
    
    def get_camera_serial_number(self,HW):
       	"""Return camera unique serial number"""
        ser = self.cams[int(HW)].serial_number
       	return ser
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        return 'ping'
    
    def prod(self):
        return
    
    def status_function(self):
        return self.running
    
    def shutdown(self):
        self.running = False

########################################################################
# Create Pyro control server
hostname = socket.gethostname()
pyro_daemon = Pyro4.Daemon(host=hostname, port=9010)
fli_daemon = FLI()

uri = pyro_daemon.register(fli_daemon, 'fli_interface')
print 'Starting FLI interface daemon at',uri

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=fli_daemon.status_function)

print 'Exiting FLI interface daemon'
time.sleep(1.)
