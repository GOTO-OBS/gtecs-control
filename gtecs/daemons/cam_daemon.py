#!/usr/bin/env python
"""Daemon to control FLI cameras via fli_interface."""

import datetime
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.controls.exq_control import Exposure
from gtecs.daemons import HardwareDaemon, daemon_proxy
from gtecs.fits import get_all_info, glance_location, image_location, write_fits


class CamDaemon(HardwareDaemon):
    """Camera hardware daemon class."""

    def __init__(self):
        super().__init__('cam')

        # cam is dependent on all the FLI interfaces
        for daemon_id in params.FLI_INTERFACES:
            self.dependencies.add(daemon_id)

        # command flags
        self.get_info_flag = 1
        self.take_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.set_temp_flag = 0

        # camera variables
        self.run_number_file = os.path.join(params.CONFIG_PATH, 'run_number')
        self.run_number = 0

        self.pool = ThreadPoolExecutor(max_workers=len(params.TEL_DICT))

        self.all_info = None

        self.exposing = 0
        self.image_ready = {tel: 0 for tel in params.TEL_DICT}
        self.image_saving = {tel: 0 for tel in params.TEL_DICT}

        self.remaining = {}
        self.exposure_start_time = {}
        self.ccd_temp = {}
        self.base_temp = {}
        self.cooler_power = {}
        self.cam_info = {}
        self.target_temp = {}

        for intf in params.FLI_INTERFACES:
            nhw = len(params.FLI_INTERFACES[intf]['TELS'])
            self.remaining[intf] = [0] * nhw
            self.exposure_start_time[intf] = [0] * nhw
            self.ccd_temp[intf] = [0] * nhw
            self.base_temp[intf] = [0] * nhw
            self.cooler_power[intf] = [0] * nhw
            self.cam_info[intf] = [0] * nhw
            self.target_temp[intf] = [0] * nhw

        self.active_tel = []
        self.abort_tel = []

        self.exposure_status = 0

        self.finished = 0
        self.saving_flag = 0

        self.current_exposure = None

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        self.log.info('Daemon control thread started')

        while(self.running):
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time

                # Check the dependencies
                self._check_dependencies()

                # If there is an error then keep looping.
                if self.dependency_error:
                    time.sleep(1)
                    continue

            # control functions
            # request info
            if self.get_info_flag:
                try:
                    # update variables
                    for tel in params.TEL_DICT:
                        intf, hw = params.TEL_DICT[tel]
                        try:
                            with daemon_proxy(intf) as fli:
                                self.cam_info[intf][hw] = fli.get_camera_info(hw)
                                self.remaining[intf][hw] = fli.get_camera_time_remaining(hw)
                                self.ccd_temp[intf][hw] = fli.get_camera_temp('CCD', hw)
                                self.base_temp[intf][hw] = fli.get_camera_temp('BASE', hw)
                                self.cooler_power[intf][hw] = fli.get_camera_cooler_power(hw)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)
                    # save info
                    info = {}
                    info['exposing'] = self.exposing
                    info['current_exposure'] = self.current_exposure
                    if self.current_exposure is not None:
                        info['current_tel_list'] = self.current_exposure.tel_list
                        info['current_exptime'] = self.current_exposure.exptime
                        info['current_binning'] = self.current_exposure.binning
                        info['current_frametype'] = self.current_exposure.frametype
                        info['current_target'] = self.current_exposure.target
                        info['current_imgtype'] = self.current_exposure.imgtype
                        info['current_set_pos'] = self.current_exposure.set_pos
                        info['current_set_total'] = self.current_exposure.set_total
                        info['current_db_id'] = self.current_exposure.db_id
                    for tel in params.TEL_DICT:
                        intf, hw = params.TEL_DICT[tel]
                        # tel = str(params.FLI_INTERFACES[intf]['TELS'][hw])
                        info['remaining' + str(tel)] = self.remaining[intf][hw]
                        if self.exposing == 1 and tel in self.active_tel:
                            info['status' + str(tel)] = 'Exposing'
                        elif self.image_saving[tel] == 1:
                            info['status' + str(tel)] = 'Reading'
                        else:
                            info['status' + str(tel)] = 'Ready'
                        info['image_ready' + str(tel)] = self.image_ready[tel]
                        info['image_saving' + str(tel)] = self.image_saving[tel]
                        info['exposure_start_time' + str(tel)] = self.exposure_start_time[intf][hw]
                        info['ccd_temp' + str(tel)] = self.ccd_temp[intf][hw]
                        info['target_temp' + str(tel)] = self.target_temp[intf][hw]
                        info['base_temp' + str(tel)] = self.base_temp[intf][hw]
                        info['cooler_power' + str(tel)] = self.cooler_power[intf][hw]
                        info['serial_number' + str(tel)] = self.cam_info[intf][hw]['serial_number']
                        info['x_pixel_size' + str(tel)] = self.cam_info[intf][hw]['pixel_size'][0]
                        info['y_pixel_size' + str(tel)] = self.cam_info[intf][hw]['pixel_size'][1]

                    info['run_number'] = self.run_number
                    info['glance'] = self.run_number < 0
                    info['uptime'] = time.time() - self.start_time
                    info['ping'] = time.time() - self.loop_time
                    now = datetime.datetime.utcnow()
                    info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

                    self.info = info
                except Exception:
                    self.log.error('get_info command failed')
                    self.log.debug('', exc_info=True)
                self.get_info_flag = 0

            # take exposure
            if self.take_exposure_flag:
                # start exposures
                if self.exposing == 0:
                    self.exposing = 1
                    # get exposure info
                    exptime = self.current_exposure.exptime
                    exptime_ms = exptime * 1000.
                    binning = self.current_exposure.binning
                    frametype = self.current_exposure.frametype

                    # set exposure info and start exposure
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]
                        if self.run_number > 0:
                            expstr = 'exposure r%07d' % self.run_number
                        else:
                            expstr = 'glance'
                        argstr = '%is, %ix%i, %s' % (exptime, binning, binning, frametype)
                        camstr = 'camera %i (%s-%i)' % (tel, intf, hw)
                        self.log.info('Taking %s (%s) on %s' % (expstr, argstr, camstr))

                        try:
                            with daemon_proxy(intf) as fli:
                                fli.clear_exposure_queue(hw)
                                # set exposure time and frame type
                                c = fli.set_exposure(exptime_ms, frametype, hw)
                                if c:
                                    self.log.info(c)
                                # set binning factor
                                c = fli.set_camera_binning(binning, binning, hw)
                                if c:
                                    self.log.info(c)
                                # set area (always full-frame)
                                c = fli.set_camera_area(0, 0, 8304, 6220, hw)
                                if c:
                                    self.log.info(c)
                                # start the exposure
                                now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                                self.exposure_start_time[intf][hw] = now
                                c = fli.start_exposure(hw)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)

                    # set flags
                    self.exposing = 1
                    self.get_info_flag = 1

                # wait for exposures to finish
                elif self.exposing == 1 and self.get_info_flag == 0:

                    # get daemon info (once, for all images)
                    # do it here so we know the cam info has been updated
                    if self.all_info is None:
                        self.all_info = get_all_info(self.info)

                    # check if exposures are complete
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]
                        try:
                            with daemon_proxy(intf) as fli:
                                ready = fli.exposure_ready(hw)
                                if ready and self.image_ready[tel] == 0:
                                    if self.run_number > 0:
                                        expstr = 'Exposure r%07d' % self.run_number
                                    else:
                                        expstr = 'Glance'
                                    camstr = 'camera %i (%s-%i)' % (tel, intf, hw)
                                    self.log.info('%s finished on %s', expstr, camstr)
                                    self.image_ready[tel] = 1
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)

                    # start saving thread when all exposures are complete
                    if all(self.image_ready[tel] == 1 for tel in self.active_tel):
                        t = threading.Thread(target=self._exposure_saving_thread,
                                             args=[self.active_tel.copy(),
                                                   self.all_info.copy()])
                        t.daemon = True
                        t.start()

                        # clear tags, ready for next exposure
                        self.exposing = 0
                        self.image_ready = {tel: 0 for tel in params.TEL_DICT}
                        self.active_tel = []
                        self.all_info = None
                        self.take_exposure_flag = 0

            # abort exposure
            if self.abort_exposure_flag:
                try:
                    for tel in self.abort_tel:
                        intf, hw = params.TEL_DICT[tel]
                        if self.run_number > 0:
                            expstr = 'exposure r%07d' % self.run_number
                        else:
                            expstr = 'glance'
                        camstr = 'camera %i (%s-%i)' % (tel, intf, hw)
                        self.log.info('Aborting %s on %s', expstr, camstr)
                        try:
                            with daemon_proxy(intf) as fli:
                                c = fli.abort_exposure(hw)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)

                    # reset flags
                    for tel in self.abort_tel:
                        self.active_tel.remove(tel)
                    if len(self.active_tel) == 0:
                        # we've aborted everything, stop the exposure
                        self.exposing = 0
                        self.active_tel = []
                        self.all_info = None
                        self.take_exposure_flag = 0
                except Exception:
                    self.log.error('abort_exposure command failed')
                    self.log.debug('', exc_info=True)
                self.abort_tel = []
                self.abort_exposure_flag = 0

            # set camera temperature
            if self.set_temp_flag:
                try:
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]
                        target_temp = self.target_temp[intf][hw]
                        camstr = 'camera %i (%s-%i)' % (tel, intf, hw)
                        self.log.info('Setting temperature on %s to %i', camstr, target_temp)
                        try:
                            with daemon_proxy(intf) as fli:
                                c = fli.set_camera_temp(target_temp, hw)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('set_temp command failed')
                    self.log.debug('', exc_info=True)
                self.active_tel = []
                self.set_temp_flag = 0

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Camera control functions
    def get_info(self):
        """Return camera status info."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Set flag
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.1)
        return self.info

    def get_info_simple(self):
        """Return plain status dict, or None."""
        try:
            info = self.get_info()
        except errors.DaemonStatusError:
            return None
        # remove custom class
        if info:
            del info['current_exposure']
        return info

    def take_image(self, exptime, binning, imgtype, tel_list):
        """Take a normal frame with the camera."""
        # Create exposure object
        exposure = Exposure(tel_list, exptime,
                            binning=binning, frametype='normal',
                            target='NA', imgtype=imgtype)

        # Use the common function
        return self.take_exposure(exposure)

    def take_dark(self, exptime, binning, imgtype, tel_list):
        """Take dark frame with the camera."""
        # Create exposure object
        exposure = Exposure(tel_list, exptime,
                            binning=binning, frametype='dark',
                            target='NA', imgtype=imgtype)

        # Use the common function
        return self.take_exposure(exposure)

    def take_glance(self, exptime, binning, imgtype, tel_list):
        """Take a glance frame with the camera (no run number)."""
        # Create exposure object
        exposure = Exposure(tel_list, exptime,
                            binning=binning, frametype='normal',
                            target='NA', imgtype=imgtype,
                            glance=True)

        # Use the common function
        return self.take_exposure(exposure)

    def take_exposure(self, exposure):
        """Take an exposure with the camera from an Exposure object."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        tel_list = exposure.tel_list
        exptime = exposure.exptime
        binning = exposure.binning
        frametype = exposure.frametype
        glance = exposure.glance

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
        if self.exposing == 1:
            raise errors.HardwareStatusError('Cameras are already exposing')

        # Find and update run number
        if not glance:
            with open(self.run_number_file, 'r') as f:
                lines = f.readlines()
                self.run_number = int(lines[0]) + 1
            with open(self.run_number_file, 'w') as f:
                f.write('{:07d}'.format(self.run_number))
        else:
            self.run_number = -1

        # Set values
        self.current_exposure = exposure
        for tel in tel_list:
            self.active_tel += [tel]

        # Set flag
        self.take_exposure_flag = 1

        # Format return string
        if not glance:
            s = 'Exposing r{:07d}:'.format(self.run_number)
        else:
            s = 'Exposing glance:'
        for tel in tel_list:
            argstr = '(%is, %ix%i, %s)' % (exptime, binning, binning, frametype)
            s += '\n  '
            s += 'Taking exposure %s on camera %i' % (argstr, tel)
        return s

    def abort_exposure(self, tel_list):
        """Abort current exposure."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Check current status
        if self.exposing == 0:
            return 'Cameras are not currently exposing'

        # Set values
        for tel in tel_list:
            if tel in self.active_tel:
                self.abort_tel += [tel]

        # Set flag
        self.abort_exposure_flag = 1

        # Format return string
        s = 'Aborting:'
        for tel in tel_list:
            s += '\n  '
            if tel not in self.abort_tel:
                s += 'Camera %i is not currently exposing' % tel
            else:
                s += 'Aborting exposure on camera %i' % tel
        return s

    def set_temperature(self, target_temp, tel_list):
        """Set the camera's temperature."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if not (-55 <= target_temp <= 45):
            raise ValueError('Temperature must be between -55 and 45')
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            self.target_temp[intf][hw] = target_temp
            self.active_tel += [tel]

        # Set flag
        self.set_temp_flag = 1

        # Format return string
        s = 'Setting:'
        for tel in tel_list:
            s += '\n  '
            s += 'Setting temperature on camera %i' % tel
        return s

    def is_exposing(self):
        """Return if the cameras are exposing.

        Used to save time when the exposure queue doesn't need the full info.
        """
        return self.exposing

    # Internal functions
    def _exposure_saving_thread(self, active_tel, all_info):
        """Thread to be started whenever an exposure is completed.

        By containing fetching images from the interfaces and saving them to
        FITS files within this thread a new exposure can be started as soon as
        the previous one is finished.
        """
        pool = ThreadPoolExecutor(max_workers=len(active_tel))

        run_number = all_info['cam']['run_number']

        # start fetching images from the interfaces in parallel
        future_images = {tel: None for tel in active_tel}
        for tel in active_tel:
            self.image_saving[tel] = 1
            intf, hw = params.TEL_DICT[tel]
            fli = daemon_proxy(intf, timeout=99)
            try:
                if run_number > 0:
                    expstr = 'exposure r%07d' % run_number
                else:
                    expstr = 'glance'
                camstr = 'camera %i (%s-%i)' % (tel, intf, hw)
                self.log.info('Fetching %s from %s', expstr, camstr)
                future_images[tel] = pool.submit(fli.fetch_exposure, hw)
            except Exception:
                self.log.error('No response from fli interface on %s', intf)
                self.log.debug('', exc_info=True)

        # wait for images to be fetched
        images = {tel: None for tel in active_tel}
        while True:
            time.sleep(0.001)
            for tel in active_tel:
                intf, hw = params.TEL_DICT[tel]
                if future_images[tel].done() and images[tel] is None:
                    images[tel] = future_images[tel].result()
                    if run_number > 0:
                        expstr = 'exposure r%07d' % run_number
                    else:
                        expstr = 'glance'
                    camstr = 'camera %i (%s-%i)' % (tel, intf, hw)
                    self.log.info('Fetched %s from %s', expstr, camstr)

            # keep looping until all the images are fetched
            if all(images[tel] is not None for tel in active_tel):
                break

        # save images in parallel
        for tel in active_tel:
            # get image and filename
            image = images[tel]
            if run_number > 0:
                filename = image_location(run_number, tel)
            else:
                filename = glance_location(tel)

            # write the FITS file
            if run_number > 0:
                expstr = 'exposure r%07d' % run_number
            else:
                expstr = 'glance'
            self.log.info('Saving %s to %s', expstr, filename)
            pool.submit(write_fits, image, filename, tel, all_info, log=self.log)

            self.image_saving[tel] = 0


if __name__ == "__main__":
    daemon_id = 'cam'
    with misc.make_pid_file(daemon_id):
        CamDaemon()._run()
