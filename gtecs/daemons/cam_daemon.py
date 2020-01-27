#!/usr/bin/env python3
"""Daemon to control cameras via the UT interface daemons."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon, daemon_proxy
from gtecs.exposures import Exposure
from gtecs.fits import get_all_info, glance_location, image_location, write_fits


class CamDaemon(BaseDaemon):
    """Camera hardware daemon class."""

    def __init__(self):
        super().__init__('cam')

        # cam is dependent on all the interfaces
        for interface_id in params.INTERFACES:
            self.dependencies.add(interface_id)

        # command flags
        self.take_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.set_temp_flag = 0

        # camera variables
        self.uts = params.UTS_WITH_CAMERAS.copy()
        self.active_uts = []
        self.abort_uts = []

        self.run_number_file = os.path.join(params.FILE_PATH, 'run_number')
        self.run_number = 0
        self.num_taken = 0

        self.pool = ThreadPoolExecutor(max_workers=len(self.uts))

        self.current_exposure = None
        self.exposing = False
        self.exposure_start_time = 0
        self.all_info = None
        self.image_ready = {ut: 0 for ut in self.uts}
        self.image_saving = {ut: 0 for ut in self.uts}

        self.target_temp = {ut: 0 for ut in self.uts}

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
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

                    # set exposure info
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        if self.run_number > 0:
                            expstr = 'exposure r{:07d}'.format(self.run_number)
                        else:
                            expstr = 'glance'
                        argstr = '{:.1f}s, {}x{}, {}'.format(exptime, binning, binning, frametype)
                        camstr = 'camera {} ({})'.format(ut, interface_id)
                        self.log.info('Taking {} ({}) on {}'.format(expstr, argstr, camstr))
                        try:
                            with daemon_proxy(interface_id) as interface:
                                interface.clear_exposure_queue(ut)
                                # set exposure time and frame type
                                c = interface.set_exposure(exptime_ms, frametype, ut)
                                if c:
                                    self.log.info(c)
                                # set binning factor
                                c = interface.set_camera_binning(binning, binning, ut)
                                if c:
                                    self.log.info(c)
                                # set area (always full-frame)
                                c = interface.set_camera_area(0, 0, 8304, 6220, ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                    # start exposure
                    # (seperate from the above, so they all start closer together)
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        try:
                            with daemon_proxy(interface_id) as interface:
                                # start the exposure
                                self.exposure_start_time = self.loop_time
                                c = interface.start_exposure(ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
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
                        self.all_info = get_all_info(self.info, self.log)

                    # check if exposures are complete
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        try:
                            with daemon_proxy(interface_id) as interface:
                                ready = interface.exposure_ready(ut)
                                if ready and self.image_ready[ut] == 0:
                                    if self.run_number > 0:
                                        expstr = 'Exposure r{:07d}'.format(self.run_number)
                                    else:
                                        expstr = 'Glance'
                                    camstr = 'camera {} ({})'.format(ut, interface_id)
                                    self.log.info('{} finished on {}'.format(expstr, camstr))
                                    self.image_ready[ut] = 1
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                    # start saving thread when all exposures are complete
                    # also make sure there's only one thread running at once.
                    if (all(self.image_ready[ut] == 1 for ut in self.active_uts) and
                            not any(self.image_saving[ut] for ut in self.active_uts)):
                        t = threading.Thread(target=self._exposure_saving_thread,
                                             args=[self.active_uts.copy(),
                                                   self.all_info.copy()])
                        t.daemon = True
                        t.start()

                        # clear tags, ready for next exposure
                        self.exposing = False
                        self.exposure_start_time = 0
                        self.image_ready = {ut: 0 for ut in self.uts}
                        self.active_uts = []
                        self.all_info = None
                        self.num_taken += 1
                        self.take_exposure_flag = 0
                        self.force_check_flag = True

            # abort exposure
            if self.abort_exposure_flag:
                try:
                    for ut in self.abort_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        if self.run_number > 0:
                            expstr = 'exposure r{:07d}'.format(self.run_number)
                        else:
                            expstr = 'glance'
                        camstr = 'camera {} ({})'.format(ut, interface_id)
                        self.log.info('Aborting {} on {}'.format(expstr, camstr))
                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.abort_exposure(ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                    # reset flags
                    for ut in self.abort_uts:
                        self.active_uts.remove(ut)
                    if len(self.active_uts) == 0:
                        # we've aborted everything, stop the exposure
                        self.exposing = False
                        self.active_uts = []
                        self.all_info = None
                        self.num_taken += 1
                        self.take_exposure_flag = 0
                except Exception:
                    self.log.error('abort_exposure command failed')
                    self.log.debug('', exc_info=True)
                self.abort_uts = []
                self.abort_exposure_flag = 0
                self.force_check_flag = True

            # set camera temperature
            if self.set_temp_flag:
                try:
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        target_temp = self.target_temp[ut]
                        camstr = 'camera {} ({})'.format(ut, interface_id)
                        self.log.info('Setting temperature on {} to {}'.format(camstr, target_temp))
                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.set_camera_temp(target_temp, ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('set_temp command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
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

        for ut in self.uts:
            # Get info from each interface
            try:
                interface_id = params.UT_DICT[ut]['INTERFACE']
                interface_info = {}
                interface_info['interface_id'] = interface_id

                if self.exposing and ut in self.active_uts:
                    interface_info['status'] = 'Exposing'
                elif self.image_saving[ut] == 1:
                    interface_info['status'] = 'Reading'
                else:
                    interface_info['status'] = 'Ready'
                interface_info['image_ready'] = self.image_ready[ut]
                interface_info['image_saving'] = self.image_saving[ut]
                interface_info['target_temp'] = self.target_temp[ut]

                with daemon_proxy(interface_id) as interface:
                    interface_info['remaining'] = interface.get_camera_time_remaining(ut)
                    interface_info['ccd_temp'] = interface.get_camera_temp('CCD', ut)
                    interface_info['base_temp'] = interface.get_camera_temp('BASE', ut)
                    interface_info['cooler_power'] = interface.get_camera_cooler_power(ut)
                    cam_info = interface.get_camera_info(ut)
                    interface_info['serial_number'] = cam_info['serial_number']
                    interface_info['x_pixel_size'] = cam_info['pixel_size'][0]
                    interface_info['y_pixel_size'] = cam_info['pixel_size'][1]

                temp_info[ut] = interface_info
            except Exception:
                self.log.error('Failed to get camera {} info'.format(ut))
                self.log.debug('', exc_info=True)
                temp_info[ut] = None

        # Get other internal info
        temp_info['exposing'] = self.exposing
        temp_info['exposure_start_time'] = self.exposure_start_time
        if self.current_exposure is not None:
            current_info = {}
            current_info['ut_list'] = self.current_exposure.ut_list
            current_info['exptime'] = self.current_exposure.exptime
            current_info['binning'] = self.current_exposure.binning
            current_info['frametype'] = self.current_exposure.frametype
            current_info['target'] = self.current_exposure.target
            current_info['imgtype'] = self.current_exposure.imgtype
            current_info['set_pos'] = self.current_exposure.set_pos
            current_info['set_total'] = self.current_exposure.set_total
            current_info['db_id'] = self.current_exposure.db_id
            temp_info['current_exposure'] = current_info
        else:
            temp_info['current_exposure'] = None
        temp_info['run_number'] = self.run_number
        temp_info['num_taken'] = self.num_taken
        temp_info['glance'] = self.run_number < 0

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(ut, temp_info[ut]['status'])
                        for ut in self.uts]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Cameras are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(ut, self.info[ut]['status'])
                            for ut in self.uts]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Cameras are {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    def _exposure_saving_thread(self, active_uts, all_info):
        """Thread to be started whenever an exposure is completed.

        By containing fetching images from the interfaces and saving them to
        FITS files within this thread a new exposure can be started as soon as
        the previous one is finished.
        """
        pool = ThreadPoolExecutor(max_workers=len(active_uts))

        run_number = all_info['cam']['run_number']

        # start fetching images from the interfaces in parallel
        future_images = {ut: None for ut in active_uts}
        for ut in active_uts:
            self.image_saving[ut] = 1
            interface_id = params.UT_DICT[ut]['INTERFACE']
            interface = daemon_proxy(interface_id, timeout=99)
            try:
                if run_number > 0:
                    expstr = 'exposure r{:07d}'.format(run_number)
                else:
                    expstr = 'glance'
                camstr = 'camera {} ({})'.format(ut, interface_id)
                self.log.info('Fetching {} from {}'.format(expstr, camstr))
                future_images[ut] = pool.submit(interface.fetch_exposure, ut)
            except Exception:
                self.log.error('No response from interface {}'.format(interface_id))
                self.log.debug('', exc_info=True)

        # wait for images to be fetched
        images = {ut: None for ut in active_uts}
        while True:
            time.sleep(0.001)
            for ut in active_uts:
                interface_id = params.UT_DICT[ut]['INTERFACE']
                if future_images[ut].done() and images[ut] is None:
                    images[ut] = future_images[ut].result()
                    if run_number > 0:
                        expstr = 'exposure r{:07d}'.format(run_number)
                    else:
                        expstr = 'glance'
                    camstr = 'camera {} ({})'.format(ut, interface_id)
                    self.log.info('Fetched {} from {}'.format(expstr, camstr))

            # keep looping until all the images are fetched
            if all(images[ut] is not None for ut in active_uts):
                break

        # if taking glance images, clear all old glances
        if run_number <= 0:
            glance_files = [os.path.join(params.IMAGE_PATH, 'glance_UT{:d}.fits'.format(ut))
                            for ut in self.uts]
            for glance_file in glance_files:
                if os.path.exists(glance_file):
                    os.remove(glance_file)

        # save images in parallel
        for ut in active_uts:
            # get image and filename
            image = images[ut]
            if run_number > 0:
                filename = image_location(run_number, ut)
            else:
                filename = glance_location(ut)

            # write the FITS file
            if run_number > 0:
                expstr = 'exposure r{:07d}'.format(run_number)
            else:
                expstr = 'glance'
            self.log.info('Saving {} to {}'.format(expstr, filename))
            pool.submit(write_fits, image, filename, ut, all_info, log=self.log)

            self.image_saving[ut] = 0

    # Control functions
    def take_image(self, exptime, binning, imgtype, ut_list):
        """Take a normal frame with the camera."""
        # Create exposure object
        exposure = Exposure(ut_list, exptime,
                            binning=binning, frametype='normal',
                            target='NA', imgtype=imgtype)

        # Use the common function
        return self.take_exposure(exposure)

    def take_dark(self, exptime, binning, imgtype, ut_list):
        """Take dark frame with the camera."""
        # Create exposure object
        exposure = Exposure(ut_list, exptime,
                            binning=binning, frametype='dark',
                            target='NA', imgtype=imgtype)

        # Use the common function
        return self.take_exposure(exposure)

    def take_glance(self, exptime, binning, imgtype, ut_list):
        """Take a glance frame with the camera (no run number)."""
        # Create exposure object
        exposure = Exposure(ut_list, exptime,
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
        ut_list = exposure.ut_list
        exptime = exposure.exptime
        binning = exposure.binning
        frametype = exposure.frametype
        glance = exposure.glance

        for ut in ut_list:
            if ut not in self.uts:
                raise ValueError('Unit telescope ID not in list {}'.format(self.uts))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError('Frame type must be in {}'.format(params.FRAMETYPE_LIST))

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
        for ut in ut_list:
            self.active_uts += [ut]

        # Set flag
        self.take_exposure_flag = 1

        # Format return string
        if not glance:
            s = 'Exposing r{:07d}:'.format(self.run_number)
        else:
            s = 'Exposing glance:'
        for ut in ut_list:
            argstr = '{:.1f}s, {}x{}, {}'.format(exptime, binning, binning, frametype)
            s += '\n  '
            s += 'Taking exposure {} on camera {}'.format(argstr, ut)
        return s

    def abort_exposure(self, ut_list):
        """Abort current exposure."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for ut in ut_list:
            if ut not in self.uts:
                raise ValueError('Unit telescope ID not in list {}'.format(self.uts))

        # Check current status
        if not self.exposing:
            return 'Cameras are not currently exposing'

        # Set values
        for ut in ut_list:
            if ut in self.active_uts:
                self.abort_uts += [ut]

        # Set flag
        self.abort_exposure_flag = 1

        # Format return string
        s = 'Aborting:'
        for ut in ut_list:
            s += '\n  '
            if ut not in self.abort_uts:
                s += 'Camera {} is not currently exposing'.format(ut)
            else:
                s += 'Aborting exposure on camera {}'.format(ut)
        return s

    def set_temperature(self, target_temp, ut_list):
        """Set the camera's temperature."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if not (-55 <= target_temp <= 45):
            raise ValueError('Temperature must be between -55 and 45')
        for ut in ut_list:
            if ut not in self.uts:
                raise ValueError('Unit telescope ID not in list {}'.format(self.uts))

        # Set values
        for ut in ut_list:
            self.target_temp[ut] = target_temp
            self.active_uts += [ut]

        # Set flag
        self.set_temp_flag = 1

        # Format return string
        s = 'Setting:'
        for ut in ut_list:
            s += '\n  '
            s += 'Setting temperature on camera {}'.format(ut)
        return s

    def is_exposing(self):
        """Return if the cameras are exposing.

        Used to save time when the exposure queue doesn't need the full info.
        """
        return self.take_exposure_flag or self.exposing


if __name__ == '__main__':
    daemon_id = 'cam'
    with misc.make_pid_file(daemon_id):
        CamDaemon()._run()
