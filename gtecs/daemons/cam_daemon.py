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
import astropy.io.fits as pyfits
from astropy.time import Time
import astropy.units as u
import numpy
import math
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.tecs_modules import astronomy
from gtecs.tecs_modules.time_date import nightStarting
from gtecs.controls.exq_control import Exposure
from gtecs.tecs_modules.daemons import HardwareDaemon

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
        self.ftlist = params.FRAMETYPE_LIST
        self.run_number_file = os.path.join(params.CONFIG_PATH, 'run_number')

        self.image = 'None yet'
        self.images = {} # mapping between telescope and future images

        self.remaining = {}
        self.exposing_flag = {}
        self.exptime = {}
        self.frametype = {}
        self.binning = {}
        self.ccd_temp = {}
        self.base_temp = {}
        self.cooler_power = {}
        self.cam_info = {}
        self.serial_number = {}

        for intf in params.FLI_INTERFACES:
            nHW = len(params.FLI_INTERFACES[intf]['TELS'])
            self.remaining[intf] = [0]*nHW
            self.exposing_flag[intf] = [0]*nHW
            self.exptime[intf] = [1]*nHW
            self.frametype[intf] = ['normal']*nHW
            self.binning[intf] = [1]*nHW
            self.ccd_temp[intf] = [0]*nHW
            self.base_temp[intf] = [0]*nHW
            self.cooler_power[intf] = [0]*nHW
            self.cam_info[intf] = [0]*nHW
            self.serial_number[intf] = [0]*nHW

        self.active_tel = []
        self.obs_times = {}

        self.finished = 0
        self.saving_flag = 0
        self.run_number = 0

        self.current_exposure = None

        self.target_temp = 0

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
                            self.serial_number[intf][HW] = fli.get_camera_serial_number(HW)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                    # save info
                    info = {}
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
                        info['frametype'+tel] = self.frametype[intf][HW]
                        info['exptime'+tel] = self.exptime[intf][HW]
                        info['binning'+tel] = self.binning[intf][HW]
                        info['ccd_temp'+tel] = self.ccd_temp[intf][HW]
                        info['base_temp'+tel] = self.base_temp[intf][HW]
                        info['cooler_power'+tel] = self.cooler_power[intf][HW]
                        info['serial_number'+tel] = self.serial_number[intf][HW]

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
                        self.exptime[intf][HW] = exptime
                        self.binning[intf][HW] = binning
                        self.frametype[intf][HW] = frametype
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
                            self.obs_times[tel] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                            c = fli.start_exposure(HW)
                            if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                        self.exposing_flag[intf][HW] = 1
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
                    image = self.images[tel]
                    # reset entry
                    self.images[tel] = None

                    # save info to add to header
                    header_dict = {}
                    header_dict['tel'] = tel
                    self.logfile.info('Fetching exposure from camera %i (%s-%i)', tel, intf, HW)
                    filename = self._image_location(tel)
                    self.logfile.info('Saving exposure to %s', filename)
                    self._write_fits(image, filename, tel)
                    self.exposing_flag[intf][HW] = 0
                    self.active_tel.pop(self.active_tel.index(tel))

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
                    target_temp = self.target_temp
                    for tel in self.active_tel:
                        intf, HW = params.TEL_DICT[tel]
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
        if frametype not in ['normal', 'dark']:
            raise ValueError("Frame type must be 'normal' or 'dark'")
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
        if frametype not in ['normal', 'dark']:
            raise ValueError("Frame type must be 'normal' or 'dark'")

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
            if tel not in self.active_tel == 0:
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
        self.target_temp = target_temp
        for tel in tel_list:
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

    def _image_location(self,tel):
        # Find the directory, using the date the observing night began
        night = nightStarting()
        direc = params.IMAGE_PATH + night
        if not os.path.exists(direc):
            os.mkdir(direc)

        # Find the file name, using the run number and UT number
        filename = '/r{:07d}_UT{:d}.fits'.format(self.run_number, tel)

        return direc + filename

    def _write_fits(self,image,filename,tel):
        hdu = pyfits.PrimaryHDU(image)
        self._update_header(hdu.header,tel)
        hdulist = pyfits.HDUList([hdu])
        if os.path.exists(filename): os.remove(filename)
        hdulist.writeto(filename)

    def _update_header(self,header,tel):
        """Add observation, exposure and hardware info to the FITS header"""

        # These cards are set automatically by AstroPy, we just give them
        # better comments
        header.comments["SIMPLE  "] = "Standard FITS"
        header.comments["BITPIX  "] = "Bits per pixel"
        header.comments["NAXIS   "] = "Number of dimensions"
        header.comments["NAXIS1  "] = "Number of columns"
        header.comments["NAXIS2  "] = "Number of rows"
        header.comments["EXTEND  "] = "Can contain extensions"
        header.comments["BSCALE  "] = "Pixel scale factor"
        header.comments["BZERO   "] = "Real = Pixel * BSCALE + BZERO"


        # Observation info
        run_id = 'r{:07d}'.format(self.run_number)
        header["RUN     "] = (self.run_number, "GOTO run number")
        header["RUN-ID  "] = (run_id, "Padded run ID string")

        now = datetime.datetime.utcnow()
        hdu_date = now.strftime("%Y-%m-%dT%H:%M:%S")
        header["DATE    "] = (hdu_date, "Date HDU created")

        header["ORIGIN  "] = (params.ORIGIN, "Origin organisation")
        header["TELESCOP"] = (params.TELESCOP, "Origin telescope")

        intf, HW = params.TEL_DICT[tel]
        ut_mask = misc.ut_list_to_mask(self.current_exposure.tel_list)
        ut_string = misc.ut_mask_to_string(ut_mask)
        header["INSTRUME"] = ('UT'+str(tel), "Origin unit telescope")
        header["UT      "] = (tel, "Integer UT number")
        header["UTMASK  "] = (ut_mask, "Run UT mask integer")
        header["UTMASKBN"] = (ut_string, "Run UT mask binary string")
        header["INTERFAC"] = (intf + '-' + str(HW), "System interface code")

        header["SWVN    "] = (params.GTECS_VERSION, "Software version number")

        observer = misc.get_observer()
        header["OBSERVER"] = (observer, "Who started the exposure")
        header["OBJECT  "] = (self.current_exposure.target, "Observed object name")

        header["SET-POS "] = (self.current_exposure.set_pos, "Position of this exposure in this set")
        header["SET-TOT "] = (self.current_exposure.set_total, "Total number of exposures in this set")

        header["SITE-LAT"] = (params.SITE_LATITUDE, "Site latitude, degrees +N")
        header["SITE-LON"] = (params.SITE_LONGITUDE, "Site longitude, degrees +E")
        header["SITE-ALT"] = (params.SITE_ALTITUDE, "Site elevation, m above sea level")
        header["SITE-LOC"] = (params.SITE_LOCATION, "Site location")


        # Exposure data
        header["EXPTIME "] = (self.current_exposure.exptime, "Exposure time, seconds")

        start_time = Time(self.obs_times[tel])
        start_time.precision = 0
        mid_time = start_time + (self.current_exposure.exptime*u.second)/2.
        header["DATE-OBS"] = (start_time.isot, "Exposure start time, UTC")
        header["DATE-MID"] = (mid_time.isot, "Exposure midpoint, UTC")

        mid_jd = mid_time.jd
        header["JD      "] = (mid_jd, "Exposure midpoint, Julian Date")

        lst = astronomy.find_lst(mid_time)
        lst_m, lst_s = divmod(abs(lst)*3600,60)
        lst_h, lst_m = divmod(lst_m,60)
        if lst < 0: lst_h = -lst_h
        mid_lst = '{:02.0f}:{:02.0f}:{:02.0f}'.format(lst_h, lst_m, lst_s)
        header["LST     "] = (mid_lst, "Exposure midpoint, Local Sidereal Time")


        # Frame info
        header["FRMTYPE "] = (self.current_exposure.frametype, "Frame type (shutter open/closed)")
        header["IMGTYPE "] = (self.current_exposure.imgtype, "Image type")

        header["FULLSEC "] = ('[1:8304,1:6220]', "Size of the full frame")
        header["TRIMSEC "] = ('[65:8240,46:6177]', "Central data region (both channels)")

        header["CHANNELS"] = (2, "Number of CCD channels")

        header["TRIMSEC1"] = ('[65:4152,46:6177]', "Data section for left channel")
        header["TRIMSEC2"] = ('[4153:8240,46:6177]', "Data section for right channel")
        header["BIASSEC1"] = ('[3:10,3:6218]', "Recommended bias section for left channel")
        header["BIASSEC2"] = ('[8295:8302,3:6218]', "Recommended bias section for right channel")
        header["DARKSEC1"] = ('[26:41,500:5721]', "Recommended dark section for left channel")
        header["DARKSEC2"] = ('[8264:8279,500:5721]', "Recommended dark section for right channel")


        # Database info
        from_db = False
        expsetID = 'NA'
        pointingID = 'NA'
        ToO_flag = 'NA'
        rank = 'NA'
        userID = 'NA'
        userName = 'NA'
        mpointingID = 'NA'
        repeatID = 'NA'
        repeatNum = 'NA'
        ligoTileID = 'NA'
        ligoTileProb = 'NA'
        surveyTileID = 'NA'
        eventID = 'NA'
        eventName = 'NA'
        eventIVO = 'NA'
        eventSource = 'NA'

        if self.current_exposure.expID != 0:
            from_db = True
            from gtecs import database as db
            with db.open_session() as session:
                expsetID = self.current_exposure.expID
                expset = session.query(db.ExposureSet).filter(
                         db.ExposureSet.expID == expsetID).one_or_none()

                if expset.pointingID:
                    pointingID = expset.pointingID
                    pointing = session.query(db.Pointing).filter(
                               db.Pointing.pointingID == pointingID).one_or_none()
                    rank = pointing.rank
                    ToO_flag = bool(pointing.ToO)
                    userID = pointing.userKey
                    user = session.query(db.User).filter(
                           db.User.userKey == userID).one_or_none()
                    userName = user.fullName

                    if pointing.mpointingID:
                        mpointingID = expset.mpointingID

                    if pointing.repeatID:
                        repeatID = pointing.repeatID
                        repeat = session.query(db.Repeat).filter(
                                   db.Repeat.repeatID == repeatID).one_or_none()
                        repeatNum = repeat.repeatNum

                    if pointing.ligoTileID:
                        ligoTileID = pointing.ligoTileID
                        ligoTile = session.query(db.LigoTile).filter(
                                   db.LigoTile.ligoTileID == pointingID).one_or_none()
                        ligoTileProb = ligoTile.probability

                    if pointing.surveyTileID:
                        surveyTileID = pointing.surveyTileID

                    if pointing.eventID:
                        eventID = pointing.eventID
                        event = session.query(db.Event).filter(
                                   db.Event.eventID == eventID).one_or_none()
                        eventName = event.name
                        eventIVO = event.ivo
                        eventSource = event.source

        header["FROMDB  "] = (from_db, "Exposure linked to database set")
        header["EXPS-ID "] = (expsetID, "Database ExposureSet ID")
        header["PNT-ID  "] = (pointingID, "Database Pointing ID")
        header["TOO     "] = (ToO_flag, "ToO flag for this Pointing")
        header["RANK    "] = (rank, "Rank of this Pointing")
        header["USER-ID "] = (userID, "Database User ID who submitted this Pointing")
        header["USER    "] = (userName, "User who submitted this Pointing")
        header["MPNT-ID "] = (mpointingID, "Database Mpointing ID")
        header["REP-ID  "] = (repeatID, "Database Repeat ID")
        header["REP-N   "] = (repeatNum, "Number of this Repeat")
        header["GW-ID   "] = (ligoTileID, "Database LIGO tile ID")
        header["GW-PROB "] = (ligoTileProb, "LIGO tile contained probability")
        header["SVY-ID  "] = (surveyTileID, "Database Survey tile ID")
        header["EVENT-ID"] = (eventID, "Database Event ID")
        header["EVENT   "] = (eventName, "Event name for this Pointing")
        header["IVO     "] = (eventIVO, "IVOA identifier for this event")
        header["SOURCE  "] = (eventSource, "Source of this event")

        # Camera info
        cam_serial = self.cam_info[intf][HW]['serial_number']
        header["CAMERA  "] = (cam_serial, "Camera serial number")

        header["XBINNING"] = (self.current_exposure.binning, "CCD x binning factor")
        header["YBINNING"] = (self.current_exposure.binning, "CCD y binning factor")

        x_pixel_size = self.cam_info[intf][HW]['pixel_size'][0]*self.current_exposure.binning
        y_pixel_size = self.cam_info[intf][HW]['pixel_size'][1]*self.current_exposure.binning
        header["XPIXSZ  "] = (x_pixel_size, "Binned x pixel size, microns")
        header["YPIXSZ  "] = (y_pixel_size, "Binned y pixel size, microns")

        header["CCDTEMP "] = (self.ccd_temp[intf][HW], "CCD temperature, C")
        header["CCDTEMPS"] = (self.target_temp, "Requested CCD temperature, C")
        header["BASETEMP"] = (self.base_temp[intf][HW], "Peltier base temperature, C")


        # Focuser info
        foc = Pyro4.Proxy(params.DAEMONS['foc']['ADDRESS'])
        foc._pyroTimeout = params.PROXY_TIMEOUT
        try:
            info = foc.get_info()
            foc_serial = info['serial_number'+str(tel)]
            foc_pos = info['current_pos'+str(tel)]
            foc_temp_int = info['int_temp'+str(tel)]
            foc_temp_ext = info['ext_temp'+str(tel)]
        except:
            foc_serial = 'NA'
            foc_pos = 'NA'
            foc_temp_int = 'NA'
            foc_temp_ext = 'NA'

        header["FOCUSER "] = (foc_serial, "Focuser serial number")
        header["FOCPOS  "] = (foc_pos, "Focuser motor position")
        header["FOCTEMPI"] = (foc_temp_int, "Focuser internal temperature, C")
        header["FOCTEMPX"] = (foc_temp_ext, "Focuser external temperature, C")


        #Filter wheel info
        filt = Pyro4.Proxy(params.DAEMONS['filt']['ADDRESS'])
        filt._pyroTimeout = params.PROXY_TIMEOUT
        try:
            info = filt.get_info()
            filt_serial = info['serial_number'+str(tel)]
            if info['current_filter_num'+str(tel)] != -1:
                filt_filter_num = info['current_filter_num'+str(tel)]
                filt_filter = params.FILTER_LIST[filt_filter_num]
            else:
                filt_filter = 'UNHOMED'
            filt_num = info['current_filter_num'+str(tel)]
            filt_pos = info['current_pos'+str(tel)]
        except:
            filt_serial = 'NA'
            filt_filter = 'NA'
            filt_num = 'NA'
            filt_pos = 'NA'
        filter_list_str = ''.join(params.FILTER_LIST)

        header["FLTWHEEL"] = (filt_serial, "Filter wheel serial number")
        header["FILTER  "] = (filt_filter, "Filter used for exposure [{}]".format(filter_list_str))
        header["FILTNUM "] = (filt_num, "Filter wheel position number")
        header["FILTPOS "] = (filt_pos, "Filter wheel motor position")

        # Mount info
        mnt = Pyro4.Proxy(params.DAEMONS['mnt']['ADDRESS'])
        mnt._pyroTimeout = params.PROXY_TIMEOUT
        try:
            info = mnt.get_info()
            targ_ra = info['target_ra']
            if targ_ra:
                ra_m, ra_s = divmod(abs(targ_ra)*3600,60)
                ra_h, ra_m = divmod(ra_m,60)
                if targ_ra < 0: ra_h = -ra_h
                targ_ra_str = '{:+03.0f}:{:02.0f}:{:04.1f}'.format(ra_h, ra_m, ra_s)
            else:
                targ_ra_str = 'NA'

            targ_dec = info['target_dec']
            if targ_dec:
                dec_m, dec_s = divmod(abs(targ_dec)*3600,60)
                dec_d, dec_m = divmod(dec_m,60)
                if targ_dec < 0: dec_d = -dec_d
                targ_dec_str = '{:+03.0f}:{:02.0f}:{:04.1f}'.format(dec_d, dec_m, dec_s)
            else:
                targ_dec_str = 'NA'

            targ_dist_a = info['target_dist']
            if targ_dist_a:
                targ_dist = numpy.around(targ_dist_a, decimals=1)
            else:
                targ_dist = 'NA'

            mnt_ra = info['mount_ra']
            ra_m, ra_s = divmod(abs(mnt_ra)*3600,60)
            ra_h, ra_m = divmod(ra_m,60)
            if mnt_ra < 0: ra_h = -ra_h
            mnt_ra_str = '{:+03.0f}:{:02.0f}:{:04.1f}'.format(ra_h, ra_m, ra_s)

            mnt_dec = info['mount_dec']
            dec_m, dec_s = divmod(abs(mnt_dec)*3600,60)
            dec_d, dec_m = divmod(dec_m,60)
            if mnt_dec < 0: dec_d = -dec_d
            mnt_dec_str = '{:+03.0f}:{:02.0f}:{:04.1f}'.format(dec_d, dec_m, dec_s)

            mnt_alt = numpy.around(info['mount_alt'], decimals=2)
            mnt_az = numpy.around(info['mount_az'], decimals=2)

            zen_dist = numpy.around(90-mnt_alt, decimals=1)
            airmass = 1/(math.cos(math.pi/2-(mnt_alt*math.pi/180)))
            airmass = numpy.around(airmass, decimals=2)
            equinox = 2000
        except:
            targ_ra_str = 'NA'
            targ_dec_str = 'NA'
            targ_dist = 'NA'
            mnt_ra_str = 'NA'
            mnt_dec_str = 'NA'
            mnt_alt = 'NA'
            mnt_az = 'NA'
            zen_dist = 'NA'
            airmass = 'NA'
            equinox = 'NA'

        header["RA-TARG "] = (targ_ra_str, "Requested pointing RA")
        header["DEC-TARG"] = (targ_dec_str, "Requested pointing Dec")

        header["RA-TEL  "] = (mnt_ra_str, "Reported mount pointing RA")
        header["DEC-TEL "] = (mnt_dec_str, "Reported mount pointing Dec")

        header["EQUINOX "] = (equinox, "RA/Dec equinox, years")

        header["TARGDIST"] = (targ_dist, "Distance from target, degrees")

        header["ALT     "] = (mnt_alt, "Mount altitude")
        header["AZ      "] = (mnt_az, "Mount azimuth")

        header["ZENDIST "] = (zen_dist, "Distance from zenith, degrees")

        header["AIRMASS "] = (airmass, "Airmass")


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
