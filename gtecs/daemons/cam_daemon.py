#!/usr/bin/env python

########################################################################
#                            cam_daemon.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#     G-TeCS meta-daemon to control FLI camerass via fli_interface     #
#                    Martin Dyer, Sheffield, 2015-16                   #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
from math import *
import time, datetime
import Pyro4
import threading
from concurrent import futures
import os
import sys
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.controls.exq_control import Exposure
from gtecs.tecs_modules.daemons import HardwareDaemon
from gtecs.tecs_modules.fits import image_location, write_fits

########################################################################
# Camera daemon class

class CamDaemon(HardwareDaemon):
    """Camera hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'cam')

        ### command flags
        self.get_info_flag = 1
        self.take_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.set_temp_flag = 0

        ### camera variables
        self.info = {}

        self.run_number_file = os.path.join(params.CONFIG_PATH, 'run_number')

        self.image = 'None yet'
        self.images = {} # mapping between telescope and future images

        self.remaining = {}
        self.exposing_flag = {}
        self.exposure_start_time = {}
        self.ccd_temp = {}
        self.base_temp = {}
        self.cooler_power = {}
        self.cam_info = {}
        self.target_temp = {}

        for intf in params.FLI_INTERFACES:
            nHW = len(params.FLI_INTERFACES[intf]['TELS'])
            self.remaining[intf] = [0]*nHW
            self.exposing_flag[intf] = [0]*nHW
            self.exposure_start_time[intf] = [0]*nHW
            self.ccd_temp[intf] = [0]*nHW
            self.base_temp[intf] = [0]*nHW
            self.cooler_power[intf] = [0]*nHW
            self.cam_info[intf] = [0]*nHW
            self.target_temp[intf] = [0]*nHW

        self.active_tel = []

        self.finished = 0
        self.saving_flag = 0
        self.run_number = 0

        self.current_exposure = None

        self.dependency_error = 0
        self.dependency_check_time = 0

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

        # make proxies once, outside the loop
        fli_proxies = dict()
        for intf in params.FLI_INTERFACES:
            fli_proxies[intf] = Pyro4.Proxy(params.DAEMONS[intf]['ADDRESS'])
            fli_proxies[intf]._pyroTimeout = params.PROXY_TIMEOUT

        while(self.running):
            self.time_check = time.time()

            ### check dependencies
            if (self.time_check - self.dependency_check_time) > 2:
                if not misc.dependencies_are_alive('cam'):
                    if not self.dependency_error:
                        self.logfile.error('Dependencies are not responding')
                        self.dependency_error = 1
                else:
                    if self.dependency_error:
                        self.logfile.info('Dependencies responding again')
                        self.dependency_error = 0
                self.dependency_check_time = time.time()

            if self.dependency_error:
                time.sleep(5)
                continue

            ### control functions
            # request info
            if self.get_info_flag:
                try:
                    # update variables
                    for tel in params.TEL_DICT:
                        intf, HW = params.TEL_DICT[tel]
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            self.cam_info[intf][HW] = fli.get_camera_info(HW)
                            self.remaining[intf][HW] = fli.get_camera_time_remaining(HW)
                            self.ccd_temp[intf][HW] = fli.get_camera_temp('CCD',HW)
                            self.base_temp[intf][HW] = fli.get_camera_temp('BASE',HW)
                            self.cooler_power[intf][HW] = fli.get_camera_cooler_power(HW)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                    # save info
                    info = {}
                    info['current_exposure'] = self.current_exposure
                    for tel in params.TEL_DICT:
                        intf, HW = params.TEL_DICT[tel]
                        tel = str(params.FLI_INTERFACES[intf]['TELS'][HW])
                        info['remaining'+tel] = self.remaining[intf][HW]
                        if self.exposing_flag[intf][HW] == 1:
                            info['status'+tel] = 'Exposing'
                        elif self.exposing_flag[intf][HW] == 2:
                            info['status'+tel] = 'Reading'
                        else:
                            info['status'+tel] = 'Ready'
                        info['exposure_start_time'+tel] = self.exposure_start_time[intf][HW]
                        info['ccd_temp'+tel] = self.ccd_temp[intf][HW]
                        info['target_temp'+tel] = self.target_temp[intf][HW]
                        info['base_temp'+tel] = self.base_temp[intf][HW]
                        info['cooler_power'+tel] = self.cooler_power[intf][HW]
                        info['serial_number'+tel] = self.cam_info[intf][HW]['serial_number']
                        info['x_pixel_size'+tel] = self.cam_info[intf][HW]['pixel_size'][0]
                        info['y_pixel_size'+tel] = self.cam_info[intf][HW]['pixel_size'][1]

                    info['run_number'] = self.run_number
                    info['uptime'] = time.time()-self.start_time
                    info['ping'] = time.time()-self.time_check
                    now = datetime.datetime.utcnow()
                    info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

                    self.info = info
                except:
                    self.logfile.error('get_info command failed')
                    self.logfile.debug('', exc_info=True)
                self.get_info_flag = 0

            # take exposure part one - start
            if self.take_exposure_flag:
                try:
                    exptime = self.current_exposure.exptime
                    exptime_ms = exptime*1000.
                    binning = self.current_exposure.binning
                    frametype = self.current_exposure.frametype
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        self.logfile.info('Taking exposure (%is, %ix%i, %s) on camera %i (%s-%i)',
                                           exptime, binning, binning, frametype, tel, intf, HW)
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            fli.clear_exposure_queue(HW)
                            # set exposure time and frame type
                            c = fli.set_exposure(exptime_ms,frametype,HW)
                            if c: self.logfile.info(c)
                            # set binning factor
                            c = fli.set_camera_binning(binning,binning,HW)
                            if c: self.logfile.info(c)
                            # set area (always full-frame)
                            c = fli.set_camera_area(0, 0, 8304, 6220, HW)
                            if c: self.logfile.info(c)
                            # start the exposure
                            self.exposure_start_time[intf][HW] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                            c = fli.start_exposure(HW)
                            if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                        self.exposing_flag[intf][HW] = 1
                        self.get_info_flag = 1
                except:
                    self.logfile.error('take_exposure command failed')
                    self.logfile.debug('', exc_info=True)
                self.take_exposure_flag = 0

            # take exposure part two - finish
            for tel in self.active_tel:
                intf, HW = params.TEL_DICT[tel]
                if self.exposing_flag[intf][HW] == 1:
                    fli = fli_proxies[intf]
                    try:
                        fli._pyroReconnect()
                        ready = fli.exposure_ready(HW)
                        if ready:
                            self.exposing_flag[intf][HW] = 2
                            self.images[tel] =  fli.fetch_exposure(HW)
                    except:
                        self.logfile.error('No response from fli interface on %s', intf)
                        self.logfile.debug('', exc_info=True)

            # take exposure part three - save
            for tel in self.active_tel:
                intf, HW = params.TEL_DICT[tel]
                if self.exposing_flag[intf][HW] == 2 and self.images[tel] is not None:
                    # image available
                    self.logfile.info('Fetching exposure from camera %i (%s-%i)', tel, intf, HW)
                    image = self.images[tel]
                    self.images[tel] = None

                    # find destination filename
                    filename = image_location(self.run_number, tel)
                    self.logfile.info('Saving exposure to %s', filename)

                    # write the FITS file
                    write_fits(image, filename, tel, self.info)

                    # finished
                    self.active_tel.pop(self.active_tel.index(tel))
                    self.exposing_flag[intf][HW] = 0

            # abort exposure
            if self.abort_exposure_flag:
                try:
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        self.logfile.info('Aborting exposure on camera %i (%s-%i)', tel, intf, HW)
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            c = fli.abort_exposure(HW)
                            if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                    self.active_tel = []
                    for intf in params.FLI_INTERFACES:
                        nHW = len(params.FLI_INTERFACES[intf]['TELS'])
                        self.exposing_flag[intf] = [0]*nHW
                except:
                    self.logfile.error('abort_exposure command failed')
                    self.logfile.debug('', exc_info=True)
                self.abort_exposure_flag = 0

            # set camera temperature
            if self.set_temp_flag:
                try:
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        target_temp = self.target_temp[intf][HW]
                        self.logfile.info('Setting temperature on camera %i (%s-%i) to %i', tel, intf, HW, target_temp)
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            c = fli.set_camera_temp(target_temp,HW)
                            if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                except:
                    self.logfile.error('set_temp command failed')
                    self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.set_temp_flag = 0

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    def get_info(self):
        """Return camera status info"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set flag
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.1)
        return self.info


    def take_image(self, exptime, binning, imgtype, tel_list):
        """Take a normal frame with the camera"""
        # Use the common function
        return self._take_frame(exptime, binning, 'normal', imgtype, tel_list)


    def take_dark(self, exptime, binning, imgtype, tel_list):
        """Take dark frame with the camera"""
        # Use the common function
        return self._take_frame(exptime, binning, 'dark', imgtype, tel_list)


    def _take_frame(self, exptime, binning, frametype, imgtype, tel_list):
        """Take a frame with the camera"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError("Frame type must be in {}".format(params.FRAMETYPE_LIST))
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(list(params.TEL_DICT)))

        # Check current status
        for tel in self.active_tel:
            intf, HW = params.TEL_DICT[tel]
            if self.exposing_flag[intf][HW] == 1:
                raise misc.HardwareStatusError('Cameras are already exposing')

        # Find and update run number
        with open(self.run_number_file, 'r') as f:
            lines = f.readlines()
            self.run_number = int(lines[0]) + 1
        with open(self.run_number_file, 'w') as f:
            f.write('{:07d}'.format(self.run_number))

        # Set values
        exposure = Exposure(tel_list, exptime,
                            binning=binning, frametype=frametype,
                            target='NA', imgtype=imgtype)
        self.current_exposure = exposure
        for tel in tel_list:
            self.active_tel += [tel]

        # Set flag
        self.get_info_flag = 1
        self.take_exposure_flag = 1

        # Format return string
        s = 'Exposing r{:07d}:'.format(self.run_number)
        for tel in tel_list:
            s += '\n  '
            s += 'Taking exposure (%is, %ix%i, %s) on camera %i' %(exptime,
                                              binning, binning, frametype, tel)
        return s


    def take_exposure(self, exposure):
        """Take an exposure with the camera from an Exposure object"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        tel_list = exposure.tel_list
        exptime = exposure.exptime
        binning = exposure.binning
        frametype = exposure.frametype

        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(list(params.TEL_DICT)))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError("Frame type must be in {}".format(params.FRAMETYPE_LIST))

        # Check current status
        for tel in self.active_tel:
            intf, HW = params.TEL_DICT[tel]
            if self.exposing_flag[intf][HW] == 1:
                raise misc.HardwareStatusError('Cameras are already exposing')

        # Find and update run number
        with open(self.run_number_file, 'r') as f:
            lines = f.readlines()
            self.run_number = int(lines[0]) + 1
        with open(self.run_number_file, 'w') as f:
            f.write('{:07d}'.format(self.run_number))

        # Set values
        self.current_exposure = exposure
        for tel in tel_list:
            self.active_tel += [tel]

        # Set flag
        self.take_exposure_flag = 1

        # Format return string
        s = 'Exposing r{:07d}:'.format(self.run_number)
        for tel in tel_list:
            s += '\n  '
            s += 'Taking exposure (%is, %ix%i, %s) on camera %i' %(exptime,
                                              binning, binning, frametype, tel)
        return s


    def abort_exposure(self, tel_list):
        """Abort current exposure"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(list(params.TEL_DICT)))

        # Set values
        self.get_info()
        self.active_tel = []
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            if not self.remaining[intf][HW] == 0:
                self.active_tel += [tel]

        # Set flag
        self.abort_exposure_flag = 1

        # Format return string
        s = 'Aborting:'
        for tel in tel_list:
            s += '\n  '
            if tel not in self.active_tel:
                s += misc.ERROR('"HardwareStatusError: Camera %i is not currently exposing"' %tel)
            else:
                s += 'Aborting exposure on camera %i' %tel
        return s


    def set_temperature(self, target_temp, tel_list):
        """Set the camera's temperature"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if not (-55 <= target_temp <= 45):
            raise ValueError('Temperature must be between -55 and 45')
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(list(params.TEL_DICT)))

        # Set values
        for tel in tel_list:
            intf, HW = params.TEL_DICT[tel]
            self.target_temp[intf][HW] = target_temp
            self.active_tel += [tel]

        # Set flag
        self.set_temp_flag = 1

        # Format return string
        s = 'Setting:'
        for tel in tel_list:
            s += '\n  '
            s += 'Setting temperature on camera %i' %tel
        return s


    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Internal functions
    def _image_fetch(self, tel):
        intf, HW = params.TEL_DICT[tel]
        fli = Pyro4.Proxy(params.DAEMONS[intf]['ADDRESS'])
        fli._pyroTimeout = 99 #params.PROXY_TIMEOUT
        try:
            future_image = fli.fetch_exposure(HW)
        except:
            self.logfile.error('No response from fli interface on %s', intf)
            self.logfile.debug('', exc_info=True)
            future_image = None
        # release proxy connection
        fli._pyroRelease()
        return future_image


########################################################################

def start():
    '''
    Create Pyro server, register the daemon and enter request loop
    '''
    host = params.DAEMONS['cam']['HOST']
    port = params.DAEMONS['cam']['PORT']

    # Check the daemon isn't already running
    if not misc.there_can_only_be_one('cam'):
        sys.exit()

    # Start the daemon
    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        cam_daemon = CamDaemon()
        uri = pyro_daemon.register(cam_daemon, objectId='cam')
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        cam_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=cam_daemon.status_function)

    # Loop has closed
    cam_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
