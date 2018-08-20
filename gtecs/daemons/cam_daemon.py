#!/usr/bin/env python
"""Daemon to control FLI cameras via fli_interface."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import HardwareDaemon, daemon_proxy
from gtecs.exposures import Exposure
from gtecs.fits import get_all_info, glance_location, image_location, write_fits


class CamDaemon(HardwareDaemon):
    """Camera hardware daemon class."""

    def __init__(self):
        super().__init__('cam')

        # cam is dependent on all the FLI interfaces
        for daemon_id in params.FLI_INTERFACES:
            self.dependencies.add(daemon_id)

        # command flags
        self.take_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.set_temp_flag = 0

        # camera variables
        self.active_tel = []
        self.abort_tel = []

        self.run_number_file = os.path.join(params.CONFIG_PATH, 'run_number')
        self.run_number = 0

        self.pool = ThreadPoolExecutor(max_workers=len(params.TEL_DICT))

        self.current_exposure = None
        self.exposing = False
        self.exposure_start_time = 0
        self.all_info = None
        self.image_ready = {tel: 0 for tel in params.TEL_DICT}
        self.image_saving = {tel: 0 for tel in params.TEL_DICT}

        self.target_temp = {tel: 0 for tel in params.TEL_DICT}

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
                self.force_check_flag = False

                # Check the dependencies
                self._check_dependencies()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's successful
                if self.dependency_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

            # control functions
            # take exposure
            if self.take_exposure_flag:
                # start exposures
                if not self.exposing:
                    self.exposing = True
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
                                # now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                                # FORMAT FITS
                                self.exposure_start_time = self.loop_time
                                c = fli.start_exposure(hw)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)

                    # set flags
                    self.exposing = True
                    self.force_check_flag = True

                # wait for exposures to finish
                # need to wait for at least a single check to update the info dict
                elif self.exposing and self.info['time'] > self.exposure_start_time:
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
                        self.exposing = False
                        self.exposure_start_time = 0
                        self.image_ready = {tel: 0 for tel in params.TEL_DICT}
                        self.active_tel = []
                        self.all_info = None
                        self.take_exposure_flag = 0
                        self.force_check_flag = True

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
                        self.exposing = False
                        self.active_tel = []
                        self.all_info = None
                        self.take_exposure_flag = 0
                except Exception:
                    self.log.error('abort_exposure command failed')
                    self.log.debug('', exc_info=True)
                self.abort_tel = []
                self.abort_exposure_flag = 0
                self.force_check_flag = True

            # set camera temperature
            if self.set_temp_flag:
                try:
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]
                        target_temp = self.target_temp[tel]
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
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the heardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        for tel in params.TEL_DICT:
            # Get info from each interface
            try:
                intf, hw = params.TEL_DICT[tel]
                tel_info = {}
                tel_info['intf'] = intf
                tel_info['hw'] = hw

                if self.exposing and tel in self.active_tel:
                    tel_info['status'] = 'Exposing'
                elif self.image_saving[tel] == 1:
                    tel_info['status'] = 'Reading'
                else:
                    tel_info['status'] = 'Ready'
                tel_info['image_ready'] = self.image_ready[tel]
                tel_info['image_saving'] = self.image_saving[tel]
                tel_info['target_temp'] = self.target_temp[tel]

                with daemon_proxy(intf) as fli:
                    tel_info['remaining'] = fli.get_camera_time_remaining(hw)
                    tel_info['ccd_temp'] = fli.get_camera_temp('CCD', hw)
                    tel_info['base_temp'] = fli.get_camera_temp('BASE', hw)
                    tel_info['cooler_power'] = fli.get_camera_cooler_power(hw)
                    cam_info = fli.get_camera_info(hw)
                    tel_info['serial_number'] = cam_info['serial_number']
                    tel_info['x_pixel_size'] = cam_info['pixel_size'][0]
                    tel_info['y_pixel_size'] = cam_info['pixel_size'][1]

                temp_info[tel] = tel_info
            except Exception:
                self.log.error('Failed to get camera {} info'.format(tel))
                self.log.debug('', exc_info=True)
                temp_info[tel] = None

        # Get other internal info
        temp_info['exposing'] = self.exposing
        temp_info['exposure_start_time'] = self.exposure_start_time
        temp_info['current_exposure'] = self.current_exposure
        if self.current_exposure is not None:
            temp_info['current_tel_list'] = self.current_exposure.tel_list
            temp_info['current_exptime'] = self.current_exposure.exptime
            temp_info['current_binning'] = self.current_exposure.binning
            temp_info['current_frametype'] = self.current_exposure.frametype
            temp_info['current_target'] = self.current_exposure.target
            temp_info['current_imgtype'] = self.current_exposure.imgtype
            temp_info['current_set_pos'] = self.current_exposure.set_pos
            temp_info['current_set_total'] = self.current_exposure.set_total
            temp_info['current_db_id'] = self.current_exposure.db_id
        temp_info['run_number'] = self.run_number
        temp_info['glance'] = self.run_number < 0

        # Update the master info dict
        self.info = temp_info

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

    # Control functions
    def get_info(self):
        """Return camera status info."""
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
        if self.exposing:
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
        if not self.exposing:
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
            self.target_temp[tel] = target_temp
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


if __name__ == "__main__":
    daemon_id = 'cam'
    with misc.make_pid_file(daemon_id):
        CamDaemon()._run()
