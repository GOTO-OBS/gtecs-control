#!/usr/bin/env python
"""
Interface to access FLI hardware
"""

import sys
import pid
import time
from math import *
import Pyro4
from concurrent import futures
from concurrent.futures import ThreadPoolExecutor

from fliapi import USBCamera, USBFocuser, USBFilterWheel
from fliapi import FakeCamera, FakeFocuser, FakeFilterWheel

from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs.daemons import InterfaceDaemon


class FLIDaemon(InterfaceDaemon):
    """FLI interface daemon class"""

    def __init__(self, intf):
        ### initiate daemon
        InterfaceDaemon.__init__(self, daemon_ID=intf)
        self.intf = intf

        ### fli objects
        self.cams = []
        self.focs = []
        self.filts = []
        missing_hardware = []
        for HW in range(len(params.FLI_INTERFACES[self.intf]['TELS'])):
            # cameras
            cam_serial = params.FLI_INTERFACES[self.intf]['SERIALS']['cam'][HW]
            cam = USBCamera.locate_device(cam_serial)
            if cam == None and params.USE_FAKE_FLI:
                    cam = FakeCamera('fake','Fake-Cam')
            if cam != None:
                self.cams.append(cam)
                self.logfile.info('Connected to Camera {}: serial "{}"'.format(HW, cam.serial_number))
            else:
                missing_hardware.append('Camera {}: {}'.format(HW, cam_serial))

            # focusers
            foc_serial = params.FLI_INTERFACES[self.intf]['SERIALS']['foc'][HW]
            foc = USBFocuser.locate_device(foc_serial)
            if foc == None and params.USE_FAKE_FLI:
                    foc = FakeFocuser('fake','Fake-Foc')
            if cam != None:
                self.focs.append(foc)
                self.logfile.info('Connected to Focuser {}: serial "{}"'.format(HW, foc.serial_number))
            else:
                missing_hardware.append('Focuser {}: {}'.format(HW, foc_serial))

            # filter wheels
            filt_serial = params.FLI_INTERFACES[self.intf]['SERIALS']['filt'][HW]
            filt = USBFilterWheel.locate_device(filt_serial)
            if filt == None and params.USE_FAKE_FLI:
                    filt = FakeFilterWheel('fake','Fake-Filt')
            if filt != None:
                self.filts.append(filt)
                self.logfile.info('Connected to Filter Wheel {}: serial "{}"'.format(HW, filt.serial_number))
            else:
                missing_hardware.append('Filter Wheel {}: {}'.format(HW, filt_serial))

        if len(missing_hardware) > 0:
            # can't run if the hardware isn't found (and we won't make fake ones)
            self.logfile.error('FLI hardware not found: {!r}'.format(missing_hardware))
            self.shutdown()


    # Focuser control functions
    def step_focuser_motor(self, steps, HW):
        """Move focuser by given number of steps"""
        self.logfile.info('Moving Focuser {} by {}'.format(HW, steps))
        self.focs[int(HW)].step_motor(steps, blocking=False)


    def home_focuser(self, HW):
        """Move focuser to the home position"""
        self.logfile.info('Homing Focuser {}'.format(HW))
        self.focs[int(HW)].home_focuser()


    def get_focuser_limit(self, HW):
        """Return focuser motor limit"""
        return self.focs[int(HW)].max_extent


    def get_focuser_position(self, HW):
        """Return focuser position"""
        return self.focs[int(HW)].stepper_position


    def get_focuser_steps_remaining(self, HW):
        """Return focuser motor limit"""
        return self.focs[int(HW)].get_steps_remaining()


    def get_focuser_temp(self, temp_type, HW):
        """Return focuser internal/external temperature"""
        return self.focs[int(HW)].read_temperature(temp_type)


    def get_focuser_serial_number(self,HW):
        """Return focuser unique serial number"""
        return self.focs[int(HW)].serial_number


    # Filter wheel control functions
    def set_filter_pos(self, new_filter, HW):
        """Move filter wheel to position"""
        self.logfile.info('Moving filter wheel {} to position {}'.format(HW, new_filter))
        pool = ThreadPoolExecutor(max_workers=4)
        ignored_future = pool.submit(self.filts[int(HW)].set_filter_pos, new_filter)


    def home_filter(self, HW):
        """Move filter wheel to home position"""
        self.logfile.info('Homing filter wheel {}'.format(HW))
        self.filts[int(HW)].home()


    def get_filter_number(self, HW):
        """Return current filter number"""
        return self.filts[int(HW)].get_filter_pos()


    def get_filter_position(self, HW):
        """Return filter wheel position"""
        return self.filts[int(HW)].stepper_position


    def get_filter_steps_remaining(self, HW):
        """Return filter wheel steps remaining"""
        return self.filts[int(HW)].get_steps_remaining()


    def get_filter_homed(self, HW):
        """Return if filter wheel has been homed"""
        return self.filts[int(HW)].homed


    def get_filter_serial_number(self,HW):
        """Return filter wheel unique serial number"""
        return self.filts[int(HW)].serial_number


    # Camera control functions
    def set_exposure(self, exptime_ms, frametype, HW):
        """Set exposure time and frametype"""
        self.logfile.info('Camera {} setting {}s {} exposure'.format(HW, str(exptime_ms/1000), frametype))
        self.cams[int(HW)].set_exposure(exptime_ms, frametype)


    def start_exposure(self, HW):
        """Begin exposure"""
        self.logfile.info('Camera {} starting exposure'.format(HW))
        self.cams[int(HW)].start_exposure()


    def exposure_ready(self, HW):
        """Check if an exposure is ready"""
        return self.cams[int(HW)].image_ready


    def fetch_exposure(self, HW):
        """Fetch the image"""
        self.logfile.info('Camera {} fetching image'.format(HW))
        return self.cams[int(HW)].fetch_image()


    def abort_exposure(self, HW):
        """Abort current exposure"""
        self.logfile.info('Camera {} aborting exposure'.format(HW))
        self.cams[int(HW)].cancel_exposure()


    def clear_exposure_queue(self, HW):
        """Clear exposure queue"""
        self.logfile.info('Camera {} clearing exposure queue'.format(HW))
        self.cams[int(HW)].image_queue.clear()


    def set_camera_temp(self, target_temp, HW):
        """Set the camera's temperature"""
        self.logfile.info('Camera {} setting temperature to {}'.format(HW, target_temp))
        self.cams[int(HW)].set_temperature(target_temp)


    def set_camera_flushes(self, target_flushes, HW):
        """Set the number of times to flush the CCD before an exposure"""
        self.logfile.info('Camera {} setting flushes to {}'.format(HW, target_flushes))
        self.cams[int(HW)].set_flushes(target_flushes)


    def set_camera_binning(self, hbin, vbin, HW):
        """Set the image binning"""
        self.logfile.info('Camera {} setting binning factor to ({},{})'.format(HW, hbin, vbin))
        self.cams[int(HW)].set_image_binning(hbin,vbin)


    def set_camera_area(self, ul_x, ul_y, lr_x, lr_y, HW):
        """Set the active image area"""
        self.logfile.info('Camera {} setting active area to ({},{},{},{})'.format(HW, ul_x, ul_y, lr_x, lr_y))
        self.cams[int(HW)].set_image_size(ul_x, ul_y, lr_x, lr_y)


    def get_camera_info(self, HW):
        """Return camera infomation dictionary"""
        return self.cams[int(HW)].get_info()


    def get_camera_state(self, HW):
        """Return camera state string"""
        return self.cams[int(HW)].state


    def get_camera_data_state(self, HW):
        """Return True if data is available"""
        return self.cams[int(HW)].dataAvailable


    def get_camera_time_remaining(self, HW):
        """Return exposure time remaining"""
        return self.cams[int(HW)].get_exposure_timeleft()/1000.


    def get_camera_temp(self, temp_type, HW):
        """Return camera CCD/base temperature"""
        return self.cams[int(HW)].get_temperature(temp_type)


    def get_camera_cooler_power(self, HW):
        """Return peltier cooler power"""
        return self.cams[int(HW)].get_cooler_power()


    def get_camera_serial_number(self,HW):
        """Return camera unique serial number"""
        return self.cams[int(HW)].serial_number


if __name__ == "__main__":
    try:
        intf = misc.find_interface_ID(params.LOCAL_HOST)
        with pid.PidFile(intf, piddir=params.CONFIG_PATH):
            FLIDaemon(intf)._run()
    except pid.PidFileError:
        raise misc.MultipleDaemonError('Daemon already running')
