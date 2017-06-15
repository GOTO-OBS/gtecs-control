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
from gtecs.tecs_modules.daemons import HardwareDaemon

########################################################################
# Camera daemon class

class CamDaemon(HardwareDaemon):
    """
    Camera daemon class

    Contains 7 functions:
    - get_info()
    - take_image(exptime,telescopeIDs)
    - take_dark(exptime,telescopeIDs)
    - take_bias(telescopeIDs)
    - abort_exposure(telescopeIDs)
    - set_temperature(target_temp,telescopeIDs)
    - set_spec(target,imgtype)
    """

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'cam')

        ### command flags
        self.take_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.set_temp_flag = 0
        self.set_binning_flag = 0

        ### camera variables
        self.info = {}
        self.ftlist = params.FRAMETYPE_LIST
        self.tel_dict = params.TEL_DICT
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
        self.target_exptime = 0
        self.target_frametype = 0
        self.target_binning = 1
        self.target_temp = 0
        self.finished = 0
        self.saving_flag = 0
        self.run_number = 0
        self.target = 'NA'
        self.imgtype = 'MANUAL'
        self.set_pos = 1
        self.set_total = 1
        self.stored_tel_list = []

        ### start control thread
        t = threading.Thread(target=self.cam_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def cam_control(self):
        self.logfile.info('Daemon control thread started')

        # make proxies once, outside the loop
        fli_proxies = dict()
        for intf in params.FLI_INTERFACES:
            fli_proxies[intf] = Pyro4.Proxy(params.FLI_INTERFACES[intf]['ADDRESS'])
            fli_proxies[intf]._pyroTimeout = params.PROXY_TIMEOUT

        self.get_info(fli_proxies)

        while(self.running):
            self.time_check = time.time()

            ### control functions
            # take exposure part one - start
            if(self.take_exposure_flag):
                exptime = self.target_exptime
                exptime_ms = exptime*1000.
                frametype = self.target_frametype
                for tel in self.active_tel:
                    intf, HW = self.tel_dict[tel]
                    self.exptime[intf][HW] = self.target_exptime
                    self.frametype[intf][HW] = self.target_frametype
                    self.logfile.info('Taking exposure (%is, %s) on camera %i (%s-%i)',
                                       exptime, frametype, tel, intf, HW)
                    fli = fli_proxies[intf]
                    try:
                        fli._pyroReconnect()
                        c = fli.set_camera_area(0, 0, 8304, 6220, HW)
                        if c: self.logfile.info(c)
                        c = fli.set_exposure(exptime_ms,frametype,HW)
                        if c: self.logfile.info(c)
                        self.obs_times[tel] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                        c = fli.start_exposure(HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', intf)
                        self.logfile.debug('', exc_info=True)
                    self.exposing_flag[intf][HW] = 1

                self.take_exposure_flag = 0

            # take exposure part two - finish
            for tel in self.active_tel:
                intf, HW = self.tel_dict[tel]
                if self.exposing_flag[intf][HW] == 1:
                    fli = fli_proxies[intf]
                    try:
                        fli._pyroReconnect()
                        remaining = fli.get_camera_time_remaining(HW)
                    except:
                        self.logfile.error('No response from fli interface on %s', intf)
                        self.logfile.debug('', exc_info=True)
                    if remaining == 0:
                        self.exposing_flag[intf][HW] = 2
                        self.images[tel] = self._image_fetch(tel) # store a future image

            # take exposure part three - save
            for tel in self.active_tel:
                intf, HW = self.tel_dict[tel]
                if self.exposing_flag[intf][HW] == 2 and self.images[tel] is not None and self.images[tel].done():
                    # image available
                    image = self.images[tel].result()
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
            if(self.abort_exposure_flag):
                for tel in self.active_tel:
                    intf, HW = self.tel_dict[tel]
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
                self.abort_exposure_flag = 0

            # set camera temperature
            if(self.set_temp_flag):
                target_temp = self.target_temp
                for tel in self.active_tel:
                    intf, HW = self.tel_dict[tel]
                    self.logfile.info('Setting temperature on camera %i (%s-%i) to %i', tel, intf, HW, target_temp)
                    fli = fli_proxies[intf]
                    try:
                        fli._pyroReconnect()
                        c = fli.set_camera_temp(target_temp,HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', intf)
                        self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.set_temp_flag = 0

            # set binning
            if(self.set_binning_flag):
                binning = self.target_binning
                for tel in self.active_tel:
                    intf, HW = self.tel_dict[tel]
                    if self.exposing_flag[intf][HW] == 1:
                        self.logfile.info('Not setting binning on camera %i (%s-%i) as it is exposing', tel, intf, HW)
                    else:
                        self.binning[intf][HW] = self.target_binning
                        self.logfile.info('Setting binning on camera %i (%s-%i) to %i', tel, intf, HW, binning)
                        fli = fli_proxies[intf]
                        try:
                            fli._pyroReconnect()
                            c = fli.set_camera_binning(binning,binning,HW)
                            if c: self.logfile.info(c)
                        except:
                            self.logfile.error('No response from fli interface on %s', intf)
                            self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.set_binning_flag = 0

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    def get_info(self, fli_proxies=None):
        """Return camera status info"""
        # request info
        for tel in self.tel_dict:
            intf, HW = self.tel_dict[tel]
            if fli_proxies:
                fli = fli_proxies[intf]
            else:
                fli = Pyro4.Proxy(params.FLI_INTERFACES[intf]['ADDRESS'])
                fli._pyroTimeout = params.PROXY_TIMEOUT
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
            finally:
                if not fli_proxies:
                    fli._pyroRelease()

        # save info
        info = {}
        for tel in self.tel_dict:
            intf, HW = self.tel_dict[tel]
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
        return self.info

    def take_image(self,exptime,tel_list):
        """Take image with camera"""
        return self._take_frame(exptime, 'image', tel_list)

    def take_dark(self,exptime,tel_list):
        """Take dark frame with camera"""
        return self._take_frame(exptime, 'dark', tel_list)

    def take_bias(self,tel_list):
        """Take bias frame with camera"""
        return self._take_frame(0, 'bias', tel_list)

    def abort_exposure(self,tel_list):
        """Abort current exposure"""
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        self.get_info()
        s = 'Aborting:'
        for tel in tel_list:
            intf, HW = self.tel_dict[tel]
            if self.remaining[intf][HW] == 0:
                s += '\n  ERROR: Camera %i is not currently exposing' %tel
            else:
                self.active_tel += [tel]
                s += '\n  Aborting exposure on camera %i' %tel
        self.abort_exposure_flag = 1
        return s

    def set_temperature(self,target_temp,tel_list):
        """Set the camera's temperature"""
        self.target_temp = target_temp
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        if not (-55 <= target_temp <= 45):
            return 'ERROR: Temperature must be between -55 and 45'
        s = 'Setting:'
        for tel in tel_list:
            self.active_tel += [tel]
            s += '\n  Setting temperature on camera %i' %tel
        self.set_temp_flag = 1
        return s

    def set_binning(self,binning,tel_list):
        """Set the image binning"""
        self.target_binning = binning
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        s = 'Setting:'
        for tel in tel_list:
            self.active_tel += [tel]
            s += '\n  Setting image binning on camera %i' %tel
        self.set_binning_flag = 1
        return s

    def set_spec(self,target,imgtype,set_pos,set_total):
        """Save the run details if given by the queue daemon"""
        self.target = target
        self.imgtype = imgtype
        self.set_pos = set_pos
        self.set_total = set_total

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Internal functions
    def _take_frame(self, exptime, exp_type, tel_list):
        """
        Take a frame with camera.

        Parameters
        -----------
        exptime : float
            exposure time in seconds

        exp_type : str
            'image', 'dark', 'bias'

        tel_list : list
            list of unit telescopes
        """
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))

        self.stored_tel_list = tel_list
        self.target_exptime = exptime

        if exp_type == 'image':
            self.target_frametype = 'normal'
        elif exp_type == 'dark' or exp_type == 'bias':
            self.target_frametype = 'dark'
        else:
            raise ValueError("Exposure type not recognised: must be 'image', 'dark' or 'bias'")

        time.sleep(0.1)

        occupied = False
        for tel in self.active_tel:
            intf, HW = self.tel_dict[tel]
            if self.exposing_flag[intf][HW] == 1:
                s = 'ERROR: Cameras are already exposing'
                occupied = True

        if not occupied:
            # find and update run number
            with open(self.run_number_file, 'r') as f:
                lines = f.readlines()
                self.run_number = int(lines[0]) + 1
            with open(self.run_number_file, 'w') as f:
                f.write(str(self.run_number))

            s = 'Exposing run {:06d}:'.format(self.run_number)
            for tel in tel_list:
                self.active_tel += [tel]
                s += '\n  Taking {:.2f}s {:s} on camera {:d}'.format(exptime,
                                                                     exp_type,
                                                                     tel)
            self.take_exposure_flag = 1

        return s

    def _image_fetch(self, tel):
        intf, HW = self.tel_dict[tel]
        fli = Pyro4.Proxy(params.FLI_INTERFACES[intf]['ADDRESS'])
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
        filename = '/r{:06d}_ut{:d}.fits'.format(self.run_number, tel)

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
        header["RUN     "] = (self.run_number, "GOTO run number")

        now = datetime.datetime.utcnow()
        hdu_date = now.strftime("%Y-%m-%dT%H:%M:%S")
        header["DATE    "] = (hdu_date, "Date HDU created")

        header["ORIGIN  "] = (params.ORIGIN, "Origin organisation")
        header["TELESCOP"] = (params.TELESCOP, "Origin telescope")

        intf, HW = self.tel_dict[tel]
        ut_mask = misc.ut_list_to_mask(self.stored_tel_list)
        header["INSTRUME"] = ('UT'+str(tel), "Origin unit telescope")
        header["UT      "] = (tel, "Integer UT number")
        header["UTMASK  "] = (ut_mask, "Run UT mask integer")
        header["INTERFAC"] = (intf + '-' + str(HW), "System interface code")

        header["SWVN    "] = ('0.1', "Software version number")

        header["OBSERVER"] = ('Martin Dyer', "Who started the exposure")
        header["OBJECT  "] = (self.target, "Observed object name")

        header["SET-POS "] = (self.set_pos, "Position of this exposure in this set")
        header["SET-TOT "] = (self.set_total, "Total number of exposures in this set")

        header["SITE-LAT"] = (params.SITE_LATITUDE, "Site latitude, degrees +N")
        header["SITE-LON"] = (params.SITE_LONGITUDE, "Site longitude, degrees +E")
        header["SITE-ALT"] = (params.SITE_ALTITUDE, "Site elevation, m above sea level")
        header["SITE-LOC"] = (params.SITE_LOCATION, "Site location")


        # Exposure data
        header["EXPTIME "] = (self.target_exptime, "Exposure time, seconds")

        start_time = Time(self.obs_times[tel])
        start_time.precision = 0
        mid_time = start_time + self.target_exptime*u.second
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
        header["FRMTYPE "] = (self.target_frametype, "Frame type (shutter open/closed)")
        header["IMGTYPE "] = (self.imgtype, "Image type")

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
        header["FROMDB  "] = (from_db, "True if originated from database, False if manual")


        # Camera info
        cam_serial = self.cam_info[intf][HW]['serial_number']
        header["CAMERA  "] = (cam_serial, "Camera serial number")

        header["XBINNING"] = (self.target_binning, "CCD x binning factor")
        header["YBINNING"] = (self.target_binning, "CCD y binning factor")

        x_pixel_size = self.cam_info[intf][HW]['pixel_size'][0]*self.target_binning
        y_pixel_size = self.cam_info[intf][HW]['pixel_size'][1]*self.target_binning
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
        flist = params.FILTER_LIST
        filt = Pyro4.Proxy(params.DAEMONS['filt']['ADDRESS'])
        filt._pyroTimeout = params.PROXY_TIMEOUT
        try:
            info = filt.get_info()
            filt_serial = info['serial_number'+str(tel)]
            filt_filter = flist[info['current_filter_num'+str(tel)]]
        except:
            filt_serial = 'NA'
            filt_filter = 'NA'
        flist_str = ''.join(flist)

        header["FLTWHEEL"] = (filt_serial, "Filter wheel serial number")
        header["FILTER  "] = (filt_filter, "Filter used for exposure [{}]".format(flist_str))


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
            dec_m, dec_s = divmod(abs(targ_dec)*3600,60)
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

        header["RA      "] = (targ_ra_str, "Requested pointing RA")
        header["DEC     "] = (targ_dec_str, "Requested pointing Dec")

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
    pyroID = params.DAEMONS['cam']['PYROID']

    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        cam_daemon = CamDaemon()
        uri = pyro_daemon.register(cam_daemon, objectId=pyroID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        cam_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=cam_daemon.status_function)

    # Loop has closed
    cam_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
