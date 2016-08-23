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
import multiprocessing
import os
import astropy.io.fits as pyfits
import numpy
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.tecs_modules.time_date import nightStarting

########################################################################
# Camera daemon functions
class CamDaemon:
    """
    Camera daemon class

    Contains 7 functions:
    - get_info()
    - take_image(exptime,telescopeIDs)
    - take_dark(exptime,telescopeIDs)
    - take_bias(telescopeIDs)
    - abort_exposure(telescopeIDs)
    - set_temperature(target_temp,telescopeIDs)
    - set_flushes(target_flushes,telescopeIDs)
    - set_area(area,telescopeIDs)
    - set_spec(run_ID,target,imgtype):
    """

    def __init__(self):
        self.running = True
        self.start_time = time.time()

        ### set up logfile
        self.logfile = logger.getLogger('cam', file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
        self.logfile.info('Daemon started')

        ### command flags
        self.take_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.set_temp_flag = 0
        self.set_flushes_flag = 0
        self.set_bins_flag = 0
        self.set_area_flag = 0

        ### camera variables
        self.info = {}
        self.ftlist = params.FRAMETYPE_LIST
        self.tel_dict = params.TEL_DICT
        self.run_number_file = os.path.join(params.CONFIG_PATH, 'run_number')

        self.image = 'None yet'

        self.remaining = {}
        self.exposing_flag = {}
        self.exptime = {}
        self.frametype = {}
        self.bins = {}
        self.area = {}
        self.ccd_temp = {}
        self.base_temp = {}
        self.cooler_power = {}
        self.cam_info = {}
        self.serial_number = {}
        self.images = {}


        for nuc in params.FLI_INTERFACES:
            self.remaining[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.exposing_flag[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.exptime[nuc] = [1]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.frametype[nuc] = ['normal']*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.bins[nuc] = [[1,1]]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.area[nuc] = [[0,0,0,0]]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.ccd_temp[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.base_temp[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.cooler_power[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.cam_info[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.serial_number[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])
            self.images[nuc] = [0]*len(params.FLI_INTERFACES[nuc]['TELS'])

        self.active_tel = []
        self.obs_times = {}
        self.target_exptime = 0
        self.target_frametype = 0
        self.target_bins = (1,1)
        self.target_area = 0
        self.target_temp = 0
        self.target_flushes = 0
        self.finished = 0
        self.saving_flag = 0
        self.spec_flag = 0
        self.run_ID = 0
        self.target = 'N/A'
        self.imgtype = 'MANUAL'

        self.manager = multiprocessing.Manager()
        self.images = self.manager.dict()

        ### start control thread
        t = threading.Thread(target=self.cam_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def cam_control(self):

        # make proxies once, outside the loop
        fli_proxies = dict()
        for nuc in params.FLI_INTERFACES:
            fli_proxies[nuc] = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
            fli_proxies[nuc]._pyroTimeout = params.PROXY_TIMEOUT

        self.get_info(fli_proxies)

        while(self.running):
            self.time_check = time.time()

            ### control functions
            # take exposure part one - start
            if(self.take_exposure_flag):
                # find and update run number
                with open(self.run_number_file,) as f:
                    lines = f.readlines()
                    self.run_ID = int(lines[0]) + 1
                with open(self.run_number_file,'w') as f:
                    f.write(str(self.run_ID))
                exptime = self.target_exptime
                exptime_ms = exptime*1000.
                frametype = self.target_frametype
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]
                    self.exptime[nuc][HW] = self.target_exptime
                    self.frametype[nuc][HW] = self.target_frametype
                    self.logfile.info('Taking exposure (%is, %s) on camera %i (%s-%i)',
                                       exptime, frametype, tel, nuc, HW)
                    fli = fli_proxies[nuc]
                    try:
                        fli._pyroReconnect()
                        c = fli.set_exposure(exptime_ms,frametype,HW)
                        if c: self.logfile.info(c)
                        self.obs_times[tel] = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                        c = fli.start_exposure(HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                    self.exposing_flag[nuc][HW] = 1

                self.take_exposure_flag = 0

            # take exposure part two - finish
            for tel in self.active_tel:
                nuc, HW = self.tel_dict[tel]
                if self.exposing_flag[nuc][HW] == 1:
                    fli = fli_proxies[nuc]
                    try:
                        fli._pyroReconnect()
                        remaining = fli.get_camera_time_remaining(HW)
                    except:
                        self.logfile.error('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                    if remaining == 0:
                        self.exposing_flag[nuc][HW] = 2
                        self.images[tel] = None
                        p = multiprocessing.Process(target=self.image_fetch, args=(tel,self.images))
                        p.daemon = True
                        p.start()

            # take exposure part three - save
            for tel in self.active_tel:
                nuc, HW = self.tel_dict[tel]
                if self.exposing_flag[nuc][HW] == 2 and self.images[tel] is not None: # image available
                    image = self.images[tel]
                    # save info to add to header
                    header_dict = {}
                    header_dict['tel'] = tel
                    self.logfile.info('Fetching exposure from camera %i (%s-%i)', tel, nuc, HW)
                    filename = self.image_location(tel)
                    self.logfile.info('Saving exposure to %s', filename)
                    self.write_fits(image,filename,tel)
                    self.exposing_flag[nuc][HW] = 0
                    self.active_tel.pop(self.active_tel.index(tel))

            # abort exposure
            if(self.abort_exposure_flag):
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]
                    self.logfile.info('Aborting exposure on camera %i (%s-%i)', tel, nuc, HW)
                    fli = fli_proxies[nuc]
                    try:
                        fli._pyroReconnect()
                        c = fli.abort_exposure(HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.abort_exposure_flag = 0

            # set camera temperature
            if(self.set_temp_flag):
                target_temp = self.target_temp
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]
                    self.logfile.info('Setting temperature on camera %i (%s-%i) to %i', tel, nuc, HW, target_temp)
                    fli = fli_proxies[nuc]
                    try:
                        fli._pyroReconnect()
                        c = fli.set_camera_temp(target_temp,HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.set_temp_flag = 0

            # set number of flushes
            if(self.set_flushes_flag):
                target_flushes = self.target_flushes
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]
                    self.logfile.info('Setting number of flushes on camera %i (%s-%i) to %i', tel, nuc, HW, target_flushes)
                    fli = fli_proxies[nuc]
                    try:
                        fli._pyroReconnect()
                        c = fli.set_camera_flushes(target_flushes,HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.set_flushes_flag = 0

            # set bins
            if(self.set_bins_flag):
                hbin, vbin = self.target_bins
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]
                    self.bins[nuc][HW] = self.target_bins
                    self.logfile.info('Setting bins on camera %i (%s-%i) to (%i,%i)', tel, nuc, HW, hbin, vbin)
                    fli = fli_proxies[nuc]
                    try:
                        fli._pyroReconnect()
                        c = fli.set_camera_bins(hbin,vbin,HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.set_bins_flag = 0

            # set active area
            if(self.set_area_flag):
                ul_x, ul_y, lr_x, lr_y = self.target_area
                for tel in self.active_tel:
                    nuc, HW = self.tel_dict[tel]
                    self.area[nuc][HW] = self.target_area
                    self.logfile.info('Setting active area on camera %i (%s-%i) to (%i,%i,%i,%i)',
                                        tel, nuc, HW, ul_x, ul_y, lr_x, lr_y)
                    fli = fli_proxies[nuc]
                    try:
                        fli._pyroReconnect()
                        c = fli.set_camera_area(ul_x, ul_y, lr_x, lr_y, HW)
                        if c: self.logfile.info(c)
                    except:
                        self.logfile.error('No response from fli interface on %s', nuc)
                        self.logfile.debug('', exc_info=True)
                self.active_tel = []
                self.set_area_flag = 0

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Camera control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    def get_info(self, fli_proxies=None):
        """Return camera status info"""
        # request info
        for tel in self.tel_dict:
            nuc, HW = self.tel_dict[tel]
            if fli_proxies:
                fli = fli_proxies[nuc]
            else:
                fli = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
                fli._pyroTimeout = params.PROXY_TIMEOUT
            try:
                fli._pyroReconnect()
                self.cam_info[nuc][HW] = fli.get_camera_info(HW)
                self.remaining[nuc][HW] = fli.get_camera_time_remaining(HW)
                self.ccd_temp[nuc][HW] = fli.get_camera_temp('CCD',HW)
                self.base_temp[nuc][HW] = fli.get_camera_temp('BASE',HW)
                self.cooler_power[nuc][HW] = fli.get_camera_cooler_power(HW)
                self.serial_number[nuc][HW] = fli.get_camera_serial_number(HW)
            except:
                self.logfile.error('No response from fli interface on %s', nuc)
                self.logfile.debug('', exc_info=True)

        # save info
        info = {}
        for tel in self.tel_dict:
            nuc, HW = self.tel_dict[tel]
            tel = str(params.FLI_INTERFACES[nuc]['TELS'][HW])
            if self.remaining[nuc][HW] > 0:
                info['status'+tel] = 'Exposing'
                info['remaining'+tel] = self.remaining[nuc][HW]
            else:
                info['status'+tel] = 'Ready'

            info['frametype'+tel] = self.frametype[nuc][HW]
            info['exptime'+tel] = self.exptime[nuc][HW]
            info['bins'+tel] = tuple(self.bins[nuc][HW])
            info['area'+tel] = tuple(self.area[nuc][HW])
            info['ccd_temp'+tel] = self.ccd_temp[nuc][HW]
            info['base_temp'+tel] = self.base_temp[nuc][HW]
            info['cooler_power'+tel] = self.cooler_power[nuc][HW]
            info['serial_number'+tel] = self.serial_number[nuc][HW]

        info['run_ID'] = self.run_ID
        info['uptime'] = time.time()-self.start_time
        info['ping'] = time.time()-self.time_check
        now = datetime.datetime.utcnow()
        info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")
        self.info = info
        return self.info

    def _take_frame(self, exptime, exp_type,
                    tel_list):
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
            nuc, HW = self.tel_dict[tel]
            if self.exposing_flag[nuc][HW] == 1:
                s = 'ERROR: Cameras are already exposing'
                occupied = True
        if not occupied:
            s = 'Exposing:'
            for tel in tel_list:
                self.active_tel += [tel]
                s += '\n  Taking %s on camera %i' % (exp_type, tel)
            self.take_exposure_flag = 1
        return s

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
            nuc, HW = self.tel_dict[tel]
            if self.remaining[nuc][HW] == 0:
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

    def set_flushes(self,target_flushes,tel_list):
        """Set the number of times to flush the CCD before an exposure"""
        self.target_flushes = target_fliushes
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        if not (0 <= target_flushes <= 16):
            return 'ERROR: Number of flushes must be between 0 and 16'
        s = 'Setting:'
        for tel in tel_list:
            self.active_tel += [tel]
            s += '\n  Setting flushes on camera %i' %tel
        self.set_flushes_flag = 1
        return s

    def set_bins(self,bins,tel_list):
        """Set the image binning"""
        self.target_bins = bins
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        s = 'Setting:'
        for tel in tel_list:
            self.active_tel += [tel]
            s += '\n  Setting image bins on camera %i' %tel
        self.set_bins_flag = 1
        return s

    def set_area(self,area,tel_list):
        """Set the active image area"""
        self.target_area = area
        for tel in tel_list:
            if tel not in self.tel_dict:
                return 'ERROR: Unit telescope ID not in list %s' %str(list(self.tel_dict))
        s = 'Setting:'
        for tel in tel_list:
            self.active_tel += [tel]
            s += '\n  Setting active image area on camera %i' %tel
        self.set_area_flag = 1
        return s

    def set_spec(self,run_ID,target,imgtype):
        """Save the run details if given by the queue daemon"""
        self.run_ID = run_ID
        self.target = target
        self.imgtype = imgtype
        self.spec_flag = 1

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Image data functions

    def image_fetch(self,tel,outarr):
        nuc, HW = self.tel_dict[tel]
        fli = Pyro4.Proxy(params.FLI_INTERFACES[nuc]['ADDRESS'])
        fli._pyroTimeout = 999 #params.PROXY_TIMEOUT
        try:
            image = fli.fetch_exposure(HW)
            outarr[tel] = image
        except:
            self.logfile.error('No response from fli interface on %s', nuc)
            self.logfile.debug('', exc_info=True)
        # release proxy connection
        fli._pyroRelease()

    def image_location(self,tel):
        # Find the date the observing night began, for the directory
        night = nightStarting()
        if not os.path.exists(params.IMAGE_PATH): os.mkdir(params.IMAGE_PATH)
        direc = params.IMAGE_PATH + night
        if not os.path.exists(direc): os.mkdir(direc)
        # Find the run number, for the file name
        if self.run_ID != 0:
            filename = '/r%05i_ut%i.fits'%(self.run_ID,tel)
        else:
            filename = '/man_ut%i.fits'%tel
        return direc + filename

    def write_fits(self,image,filename,tel):
        hdu = pyfits.PrimaryHDU(image)
        self.update_header(hdu.header,tel)
        hdulist = pyfits.HDUList([hdu])
        if os.path.exists(filename): os.remove(filename)
        hdulist.writeto(filename)

    def update_header(self,header,tel):
        # File data
        now = datetime.datetime.utcnow()
        hdu_date = now.strftime("%Y-%m-%dT%H:%M:%S")
        header.set("DATE",     value = hdu_date,                        comment = "Date HDU created")
        header.set("RUN_ID",   value = self.run_ID,                     comment = "GOTO Observation ID number")
        header.set("OBJECT",   value = self.target,                     comment = "Object name")

        # Origin data
        nuc, HW = self.tel_dict[tel]
        tel_str = "-%i (%s-%i)" %(tel,nuc,HW)
        header.set("ORIGIN",   value = params.ORIGIN,                   comment = "Origin organization")
        header.set("TELESCOP", value = params.TELESCOP+tel_str,         comment = "Origin telescope")
        cam_ID = self.cam_info[nuc][HW]['serial_number']
        header.set("INSTRUME", value = cam_ID,                          comment = "Camera serial number")

        # Camera data
        header.set("DATE-OBS", value = self.obs_times[tel],             comment = "Observation start time, UTC")
        header.set("EXPTIME",  value = self.target_exptime,             comment = "Exposure time, seconds")
        header.set("FRMTYPE",  value = self.target_frametype,           comment = "Exposure type")
        header.set("IMGTYPE",  value = self.imgtype,                    comment = "Type of image")
        x_bin = self.target_bins[0]
        y_bin = self.target_bins[1]
        header.set("XBINNING", value = x_bin,                           comment = "Width bin factor")
        header.set("YBINNING", value = y_bin,                           comment = "Height bin factor")
        x_pixel_size = self.cam_info[nuc][HW]['pixel_size'][0]*x_bin*1000000 #in microns
        y_pixel_size = self.cam_info[nuc][HW]['pixel_size'][1]*y_bin*1000000
        header.set("XPIXLSZ",  value = x_pixel_size,                    comment = "Binned pixel size, microns")
        header.set("YPIXLSZ",  value = y_pixel_size,                    comment = "Binned pixel size, microns")
        header.set("CCDTEMP",  value = self.ccd_temp[nuc][HW],          comment = "CCD temperature, C")
        header.set("CCDTEMPS", value = self.target_temp,                comment = "Set CCD temperature, C")
        header.set("BASETEMP", value = self.base_temp[nuc][HW],         comment = "Peltier base temperature, C")

        # Mount data
        mnt = Pyro4.Proxy(params.DAEMONS['mnt']['ADDRESS'])
        mnt._pyroTimeout = params.PROXY_TIMEOUT
        try:
            info = mnt.get_info()
            mount_alt = info['mount_alt']
            mount_az = info['mount_az']
            mount_ra = info['mount_ra']
            mount_dec = info['mount_dec']
            target_ra = info['target_ra']
            target_dec = info['target_dec']
        except:
            mount_alt = 'N/A'
            mount_az = 'N/A'
            mount_ra = 'N/A'
            mount_dec = 'N/A'
            target_ra = 'N/A'
            target_dec = 'N/A'
        header.set("ALT", value = mount_alt,                            comment = "Mount altitude")
        header.set("AZ", value = mount_az,                              comment = "Mount azimuth")
        header.set("RA", value = target_ra,                              comment = "RA requested")
        header.set("DEC", value = target_dec,                             comment = "Dec requested")
        header.set("RA_TEL", value = mount_ra,                          comment = "Telescope RA")
        header.set("DEC_TEL", value = mount_dec,                         comment = "Telescope Dec")

        # Focuser data
        foc = Pyro4.Proxy(params.DAEMONS['foc']['ADDRESS'])
        foc._pyroTimeout = params.PROXY_TIMEOUT
        try:
            info = foc.get_info()
            foc_pos = info['current_pos'+str(tel)]
            foc_temp_int = info['int_temp'+str(tel)]
            foc_temp_ext = info['ext_temp'+str(tel)]
            foc_ID = info['serial_number'+str(tel)]
        except:
            foc_pos = 'N/A'
            foc_temp_int = 'N/A'
            foc_temp_ext = 'N/A'
            foc_ID = 'N/A'
        header.set("FOCUSER",  value = foc_ID,                          comment = "Focuser serial number")
        header.set("FOCPOS",   value = foc_pos,                         comment = "Focuser motor position")
        header.set("FOCTEMPI", value = foc_temp_int,                    comment = "Focuser internal temperature, C")
        header.set("FOCTEMPX", value = foc_temp_ext,                    comment = "Focuser external temperature, C")

        #Filter wheel data
        flist = params.FILTER_LIST
        filt = Pyro4.Proxy(params.DAEMONS['filt']['ADDRESS'])
        filt._pyroTimeout = params.PROXY_TIMEOUT
        try:
            info = filt.get_info()
            filt = flist[info['current_filter_num'+str(tel)]]
            filt_ID = info['serial_number'+str(tel)]
        except:
            filt = 'N/A'
            filt_ID = 'N/A'
        header.set("FILTW",     value = filt_ID,                        comment = "Filter wheel serial number")
        header.set("FILTER",    value = filt,                           comment = "Filter used for exposure")

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS['cam']['PINGLIFE']:
            return 'ERROR: Last control thread time check was %.1f seconds ago' %dt_control
        else:
            return 'ping'

    def prod(self):
        return

    def status_function(self):
        return self.running

    def shutdown(self):
        self.running = False

def start():
    ########################################################################
    # Create Pyro control server
    pyro_daemon = Pyro4.Daemon(host=params.DAEMONS['cam']['HOST'], port=params.DAEMONS['cam']['PORT'])
    cam_daemon = CamDaemon()

    uri = pyro_daemon.register(cam_daemon,objectId = params.DAEMONS['cam']['PYROID'])
    cam_daemon.logfile.info('Starting camera daemon at %s',uri)

    Pyro4.config.COMMTIMEOUT = 5.
    pyro_daemon.requestLoop(loopCondition=cam_daemon.status_function)

    cam_daemon.logfile.info('Exiting camera daemon')
    time.sleep(1.)

if __name__ == "__main__":
    start()