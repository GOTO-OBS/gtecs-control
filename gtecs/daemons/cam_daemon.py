#!/usr/bin/env python
"""
Daemon to control FLI cameras via fli_interface
"""

import os
import sys
import time
import datetime
from math import *
import Pyro4
import threading
from concurrent.futures import ThreadPoolExecutor

from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs.controls.exq_control import Exposure
from gtecs.daemons import HardwareDaemon
from gtecs.fits import image_location, get_all_info, write_fits


DAEMON_ID = 'cam'
DAEMON_HOST = params.DAEMONS[DAEMON_ID]['HOST']
DAEMON_PORT = params.DAEMONS[DAEMON_ID]['PORT']


class CamDaemon(HardwareDaemon):
    """Camera hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        self.daemon_id = DAEMON_ID
        HardwareDaemon.__init__(self, self.daemon_id)

        ### command flags
        self.get_info_flag = 1
        self.take_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.set_temp_flag = 0

        ### camera variables
        self.info = None

        self.run_number_file = os.path.join(params.CONFIG_PATH, 'run_number')

        self.images = {} # mapping between telescope and future images
        self.future_images = {}  # use threads to download future images
        self.pool = ThreadPoolExecutor(max_workers=len(params.TEL_DICT))

        self.all_info = None

        self.exposing_flag = {}

        for tel in params.TEL_DICT:
            self.exposing_flag[tel] = 0

        self.remaining = {}
        self.exposure_start_time = {}
        self.ccd_temp = {}
        self.base_temp = {}
        self.cooler_power = {}
        self.cam_info = {}
        self.target_temp = {}

        for intf in params.FLI_INTERFACES:
            nHW = len(params.FLI_INTERFACES[intf]['TELS'])
            self.remaining[intf] = [0]*nHW
            self.exposure_start_time[intf] = [0]*nHW
            self.ccd_temp[intf] = [0]*nHW
            self.base_temp[intf] = [0]*nHW
            self.cooler_power[intf] = [0]*nHW
            self.cam_info[intf] = [0]*nHW
            self.target_temp[intf] = [0]*nHW

        self.active_tel = []

        self.exposure_status = 0

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
                if not misc.dependencies_are_alive(self.daemon_id):
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
                    if self.current_exposure != None:
                        info['current_tel_list'] = self.current_exposure.tel_list
                        info['current_exptime'] = self.current_exposure.exptime
                        info['current_binning'] = self.current_exposure.binning
                        info['current_frametype'] = self.current_exposure.frametype
                        info['current_target'] = self.current_exposure.target
                        info['current_imgtype'] = self.current_exposure.imgtype
                        info['current_set_pos'] = self.current_exposure.set_pos
                        info['current_set_total'] = self.current_exposure.set_total
                        info['current_expID'] = self.current_exposure.expID
                    for tel in params.TEL_DICT:
                        intf, HW = params.TEL_DICT[tel]
                        #tel = str(params.FLI_INTERFACES[intf]['TELS'][HW])
                        info['remaining'+str(tel)] = self.remaining[intf][HW]
                        if self.exposing_flag[tel] == 1:
                            info['status'+str(tel)] = 'Exposing'
                        elif self.exposing_flag[tel] in [2, 3, 4]:
                            info['status'+str(tel)] = 'Reading'
                        else:
                            info['status'+str(tel)] = 'Ready'
                        info['exposure_start_time'+str(tel)] = self.exposure_start_time[intf][HW]
                        info['ccd_temp'+str(tel)] = self.ccd_temp[intf][HW]
                        info['target_temp'+str(tel)] = self.target_temp[intf][HW]
                        info['base_temp'+str(tel)] = self.base_temp[intf][HW]
                        info['cooler_power'+str(tel)] = self.cooler_power[intf][HW]
                        info['serial_number'+str(tel)] = self.cam_info[intf][HW]['serial_number']
                        info['x_pixel_size'+str(tel)] = self.cam_info[intf][HW]['pixel_size'][0]
                        info['y_pixel_size'+str(tel)] = self.cam_info[intf][HW]['pixel_size'][1]

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

            # take exposure
            if self.take_exposure_flag:
                # stage 0 - start exposures
                if self.exposure_status == 0:
                    # get exposure info
                    exptime = self.current_exposure.exptime
                    exptime_ms = exptime*1000.
                    binning = self.current_exposure.binning
                    frametype = self.current_exposure.frametype

                    # set exposure info and start exposure
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        self.logfile.info('Taking exposure (%is, %ix%i, %s) on camera %i (%s-%i)',
                                           exptime, binning, binning, frametype, tel, intf, HW)
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            fli.clear_exposure_queue(HW)
                            # set exposure time and frame type
                            c = fli.set_exposure(exptime_ms, frametype, HW)
                            if c: self.logfile.info(c)
                            # set binning factor
                            c = fli.set_camera_binning(binning, binning, HW)
                            if c: self.logfile.info(c)
                            # set area (always full-frame)
                            c = fli.set_camera_area(0, 0, 8304, 6220, HW)
                            if c: self.logfile.info(c)
                            # start the exposure
                            self.exposure_start_time[intf][HW] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                            c = fli.start_exposure(HW)
                            if c: self.logfile.info(c)
                            # set this camera's exposing flag
                            self.exposing_flag[tel] = 1
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)

                    # set flag for stage 1
                    self.exposure_status = 1
                    self.get_info_flag = 1

                # stage 1 - wait for exposures to finish
                elif self.exposure_status == 1 and self.get_info_flag == 0:

                    # get daemon info (once, for all images)
                    # do it here so we know the cam info has been updated
                    if self.all_info is None:
                        self.all_info = get_all_info(self.info)

                    # check if exposures are complete
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            ready = fli.exposure_ready(HW)
                            if ready and self.exposing_flag[tel] == 1:
                                self.logfile.info('Exposure finished on camera %i (%s-%i)', tel, intf, HW)
                                self.exposing_flag[tel] = 2
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)

                    # set flag for stage 2 when all exposures are complete
                    if all(self.exposing_flag[tel] == 2 for tel in self.active_tel):
                        self.exposure_status = 2

                # stage 2 - readout images
                elif self.exposure_status == 2:
                    # start reading exposures in parallel
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            self.logfile.info('Reading exposure from camera %i (%s-%i)', tel, intf, HW)
                            self.future_images[tel] = self.pool.submit(fli.fetch_exposure, HW)
                            self.exposing_flag[tel] = 3
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)

                    # set flag for stage 3 when all images are being read
                    if all(self.exposing_flag[tel] == 3 for tel in self.active_tel):
                        self.exposure_status = 3

                # stage 3 - wait for images to be read out
                elif self.exposure_status == 3:
                    # check if exposures are read
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
                        if self.future_images[tel].done() and self.exposing_flag[tel] == 3:
                            self.images[tel] = self.future_images[tel].result()
                            self.logfile.info('Read exposure from camera %i (%s-%i)', tel, intf, HW)
                            self.exposing_flag[tel] = 4

                    # set flag for stage 4 when all images have been read
                    if all(self.exposing_flag[tel] == 4 for tel in self.active_tel):
                        self.exposure_status = 4

                # stage 4 - save
                elif self.exposure_status == 4:
                    # make sure we have all the info we need
                    if self.all_info is not None:
                        all_info = self.all_info.copy()
                        self.all_info = None
                    else:
                        all_info = get_all_info(self.info)

                    # save images in parallel
                    for tel in self.active_tel:
                        # get image and filename
                        image = self.images[tel]
                        filename = image_location(self.run_number, tel)

                        # write the FITS file
                        self.logfile.info('Saving exposure to %s', filename)
                        self.pool.submit(write_fits, image, filename, tel, all_info, log=self.logfile)
                        self.exposing_flag[tel] = 0

                    # finished
                    self.exposure_status = 0
                    self.images = {}
                    self.active_tel = []
                    self.take_exposure_flag = 0


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


    def get_info_simple(self):
        """Return plain status dict, or None"""
        try:
            info = self.get_info()
        except:
            return None
        # remove custom class
        if info:
            del info['current_exposure']
        return info


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
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Check current status
        if self.exposure_status == 1:
            raise misc.HardwareStatusError('Cameras are already exposing')
        elif self.exposure_status in [2, 3]:
            raise misc.HardwareStatusError('Cameras are reading out')

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
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError("Frame type must be in {}".format(params.FRAMETYPE_LIST))

        # Check current status
        if self.exposure_status == 1:
            raise misc.HardwareStatusError('Cameras are already exposing')
        elif self.exposure_status in [2, 3]:
            raise misc.HardwareStatusError('Cameras are reading out')

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
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Check current status
        if self.exposure_status == 0:
            return 'Cameras are not currently exposing'
        elif self.exposure_status in [2, 3]:
            return 'Cameras are reading out, no need to abort'

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
                s += 'Camera %i is not currently exposing' %tel
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
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

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


if __name__ == "__main__":
    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(DAEMON_ID):
        sys.exit()

    # Create the daemon object
    daemon = CamDaemon()

    # Start the daemon
    with Pyro4.Daemon(host=DAEMON_HOST, port=DAEMON_PORT) as pyro_daemon:
        uri = pyro_daemon.register(daemon, objectId=DAEMON_ID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=daemon.status_function)

    # Loop has closed
    daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)
