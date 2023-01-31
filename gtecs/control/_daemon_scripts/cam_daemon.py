#!/usr/bin/env python3
"""Daemon to control cameras via the UT interface daemons."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import errors
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, daemon_proxy
from gtecs.control.exposures import Exposure
from gtecs.control.fits import (clear_glance_files, get_all_info, glance_location,
                                image_location, make_fits, save_fits)
from gtecs.control.slack import send_slack_msg


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
        self.clear_queue_flag = 1  # clear on daemon restart
        self.set_window_flag = 0
        self.set_temp_flag = 0

        # camera variables
        self.uts = params.UTS_WITH_CAMERAS.copy()
        self.active_uts = []
        self.abort_uts = []
        self.clear_uts = self.uts.copy()  # clear on daemon restart

        self.run_number_file = os.path.join(params.FILE_PATH, 'run_number')
        if not os.path.exists(self.run_number_file):
            with open(self.run_number_file, 'w') as f:
                f.write('0')
                f.close()
        with open(self.run_number_file, 'r') as f:
            self.latest_run_number = int(f.read())
        self.num_taken = 0
        self.latest_headers = (0, {ut: None for ut in self.uts})

        self.queues_cleared = {ut: False for ut in self.uts}

        self.exposure_state = 'none'
        self.current_exposure = None
        self.all_info = None
        self.exposure_start_time = {ut: 0 for ut in self.uts}
        self.exposure_finished = {ut: False for ut in self.uts}
        self.exposing_start_time = 0
        self.image_ready = {ut: False for ut in self.uts}
        self.saving_thread_running = False

        self.target_window = {ut: None for ut in self.uts}
        self.measure_hfds = False

        self.cool_temp = int(params.CAM_IMAGING_TEMPERATURE)
        self.warm_temp = int(params.CAM_STANDBY_TEMPERATURE)
        self.target_temp = {ut: self.warm_temp for ut in self.uts}

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')
        self.check_period = params.DAEMON_CHECK_PERIOD
        self.check_time = 0
        self.force_check_flag = True

        while self.running:
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
                expstr = self.current_exposure.expstr.capitalize()

                # Exposure state machine
                if self.exposure_state == 'none':
                    # STATE 1: Set up and start the exposure
                    # Set up the exposure
                    exptime = self.current_exposure.exptime
                    exptime_ms = exptime * 1000.
                    binning = self.current_exposure.binning
                    frametype = self.current_exposure.frametype
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        argstr = '{:.1f}s, {:.0f}x{:.0f}, {}'.format(exptime,
                                                                     binning, binning,
                                                                     frametype)
                        self.log.info('{}: Preparing exposure ({}) on camera {} ({})'.format(
                                      expstr, argstr, ut, interface_id))
                        try:
                            with daemon_proxy(interface_id) as interface:
                                # set exposure time and frame type
                                c = interface.set_exposure(exptime_ms, frametype, ut)
                                if c:
                                    self.log.info(c)
                                # set binning factor
                                c = interface.set_camera_binning(binning, binning, ut)
                                if c:
                                    self.log.info(c)
                                # set window
                                if self.target_window[ut] is None:
                                    # we need to set the default to full-frame here, since on
                                    # startup the cameras default to the active area only
                                    c = interface.set_camera_window_full(ut)
                                else:
                                    # if the area isn't None then it should have been set by the
                                    # set_window function, however it could be forgotten
                                    # if the cameras are rebooted
                                    x, y, dx, dy = self.target_window[ut]
                                    c = interface.set_camera_window(x, y, dx, dy, ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                    # Start the exposure
                    # (separate from the above, so they all start closer together)
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        self.log.info('{}: Starting exposure on camera {} ({})'.format(
                                      expstr, ut, interface_id))
                        try:
                            with daemon_proxy(interface_id) as interface:
                                # save the exact start time for each camera
                                self.exposure_start_time[ut] = time.time()
                                # start the exposure
                                c = interface.start_exposure(ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                    self.exposing_start_time = self.loop_time
                    self.exposure_state = 'exposing'

                if (self.exposure_state == 'exposing' and
                        self.info['time'] > self.exposing_start_time):
                    # STATE 2: Wait for exposures to finish
                    # Note we need to wait for the info dict to be updated with the current exposure
                    # details, which also gives time for the exposures to start.

                    # Check if exposures are complete
                    # This won't mean the images are ready to save, since they need to be
                    # read out of the cameras first: that usually takes ~5s, and is done by a
                    # thread within the interfaces (actually within fliapi.USBCamera)
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        try:
                            with daemon_proxy(interface_id) as interface:
                                remaining = interface.get_camera_time_remaining(ut)
                            if remaining == 0 and not self.exposure_finished[ut]:
                                self.log.info('{}: Finished exposure on camera {} ({})'.format(
                                              expstr, ut, interface_id))
                                self.exposure_finished[ut] = True
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                    if all(self.exposure_finished[ut] for ut in self.active_uts):
                        self.exposure_state = 'reading_out'

                if self.exposure_state == 'reading_out':
                    # STATE 3: Wait for the readout to finish and images are ready to be saved
                    # The exposure is finished but the cameras are still reading out

                    # Fetch the daemon info
                    if self.all_info is None:
                        self.log.info('{}: Fetching info from other daemons'.format(expstr))
                        self.all_info, bad_info = get_all_info(self.info.copy(), log=self.log)
                        self.log.info('{}: Fetched info from other daemons'.format(expstr))
                        if len(bad_info) > 0:
                            # We failed to get at least one info set, log and tell Slack
                            self.log.error('Bad info: {}'.format(bad_info))
                            send_slack_msg('Cam failed to get info for: {}'.format(bad_info))

                    # Wait for the images to be ready
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        try:
                            with daemon_proxy(interface_id) as interface:
                                ready = interface.exposure_ready(ut)
                            if ready and not self.image_ready[ut]:
                                self.log.info('{}: Ready to save exposure on camera {} ({})'.format(
                                              expstr, ut, interface_id))
                                self.image_ready[ut] = True
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                    if all(self.image_ready[ut] for ut in self.active_uts):
                        self.exposure_state = 'images_ready'

                if (self.exposure_state == 'images_ready' and
                        self.saving_thread_running is False and
                        self.loop_time > self.exposing_start_time + params.MIN_EXPOSURE_DELAY):
                    # STATE 4: Begin fetching/saving the images
                    # Note we need to ensure that the image thread isn't currently running,
                    # and we also enforce a minimum 10s exposure time to stop saving too often.

                    if params.SAVE_IMAGES_LOCALLY:
                        # fetch image data from the interfaces and save them from the cam daemon
                        t = threading.Thread(target=self._save_images_cam,
                                             args=[self.active_uts.copy(), self.all_info.copy()])
                    else:
                        # tell the interfaces to save the files themselves
                        t = threading.Thread(target=self._save_images_intf,
                                             args=[self.active_uts.copy(), self.all_info.copy()])
                    t.daemon = True
                    t.start()

                    # Clear tags, ready for next exposure
                    self.exposure_state = 'none'
                    self.current_exposure = None
                    self.exposing_start_time = 0
                    self.exposure_start_time = {ut: 0 for ut in self.uts}
                    self.exposure_finished = {ut: False for ut in self.uts}
                    self.image_ready = {ut: False for ut in self.uts}
                    self.all_info = None
                    self.active_uts = []
                    self.num_taken += 1
                    self.take_exposure_flag = 0
                    self.force_check_flag = True

            # abort exposure
            if self.abort_exposure_flag:
                try:
                    for ut in self.abort_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        expstr = self.current_exposure.expstr.capitalize()
                        self.log.info('{}: Aborting exposure on camera {} ({})'.format(
                                      expstr, ut, interface_id))
                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.abort_exposure(ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                        if ut in self.active_uts:
                            self.active_uts.remove(ut)

                    if len(self.active_uts) == 0:
                        # we've aborted everything, stop the exposure
                        self.exposure_state = 'none'
                        self.current_exposure = None
                        self.exposing_start_time = 0
                        self.exposure_start_time = {ut: 0 for ut in self.uts}
                        self.exposure_finished = {ut: False for ut in self.uts}
                        self.image_ready = {ut: False for ut in self.uts}
                        self.all_info = None
                        self.active_uts = []
                        self.num_taken += 1
                        self.take_exposure_flag = 0
                except Exception:
                    self.log.error('abort_exposure command failed')
                    self.log.debug('', exc_info=True)
                self.abort_uts = []
                self.abort_exposure_flag = 0
                self.force_check_flag = True

            # clear camera data queue
            if self.clear_queue_flag:
                # TODO: Add a verification key to the interfaces to keep track of them?
                # TODO: Also verify the data size matches what's expected for the binfac!)
                try:
                    for ut in self.clear_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        self.log.info('Clearing queue on camera {} ({})'.format(ut, interface_id))
                        try:
                            with daemon_proxy(interface_id) as interface:
                                interface.clear_exposure_queue(ut)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                        if ut in self.active_uts:
                            self.active_uts.remove(ut)

                except Exception:
                    self.log.error('clear_queue command failed')
                    self.log.debug('', exc_info=True)
                self.clear_uts = []
                self.clear_queue_flag = 0
                self.force_check_flag = True

            # set camera window
            if self.set_window_flag:
                try:
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        camstr = 'camera {} ({})'.format(ut, interface_id)
                        if self.target_window[ut] is None:
                            # reset to full
                            areastr = 'full-frame'
                        else:
                            x, y, dx, dy = self.target_window[ut]
                            areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
                        self.log.info('Setting window on {} to {}'.format(camstr, areastr))
                        try:
                            with daemon_proxy(interface_id) as interface:
                                if self.target_window[ut] is None:
                                    c = interface.set_camera_window_full(ut)
                                else:
                                    x, y, dx, dy = self.target_window[ut]
                                    c = interface.set_camera_window(x, y, dx, dy, ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('set_window command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.set_window_flag = 0
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
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from each UT
        temp_info['uts'] = self.uts.copy()
        for ut in self.uts:
            try:
                ut_info = {}
                interface_id = params.UT_DICT[ut]['INTERFACE']
                ut_info['interface_id'] = interface_id

                if ut in self.active_uts:
                    if self.exposure_state == 'exposing':
                        ut_info['status'] = 'Exposing'
                    elif self.exposure_state != 'none':
                        ut_info['status'] = 'Reading'
                    else:
                        ut_info['status'] = 'Ready'
                else:
                    ut_info['status'] = 'Ready'
                ut_info['exposure_start_time'] = self.exposure_start_time[ut]
                ut_info['image_ready'] = self.image_ready[ut]

                with daemon_proxy(interface_id) as interface:
                    ut_info['serial_number'] = interface.get_camera_serial_number(ut)
                    ut_info['hw_class'] = interface.get_camera_class(ut)
                    ut_info['remaining'] = interface.get_camera_time_remaining(ut)
                    ut_info['in_queue'] = interface.get_camera_queue_length(ut)
                    ut_info['ccd_temp'] = interface.get_camera_temp('CCD', ut)
                    ut_info['base_temp'] = interface.get_camera_temp('BASE', ut)
                    ut_info['target_temp'] = self.target_temp[ut]
                    ut_info['cooler_power'] = interface.get_camera_cooler_power(ut)
                    cam_info = interface.get_camera_info(ut)
                    ut_info['cam_info'] = cam_info
                    ut_info['x_pixel_size'] = cam_info['pixel_size'][0]
                    ut_info['y_pixel_size'] = cam_info['pixel_size'][1]
                    ut_info['image_size'] = interface.get_camera_image_size(ut)
                    ut_info['window_area'] = interface.get_camera_window(ut)
                    ut_info['active_area'] = interface.get_camera_active_area(ut)
                    ut_info['full_area'] = interface.get_camera_full_area(ut)

                temp_info[ut] = ut_info
            except Exception:
                self.log.error('Failed to get camera {} info'.format(ut))
                self.log.debug('', exc_info=True)
                temp_info[ut] = None

        # Get other internal info
        if self.current_exposure is not None:
            current_info = {}
            current_info['expstr'] = self.current_exposure.expstr
            current_info['run_number'] = self.current_exposure.run_number
            current_info['ut_list'] = self.current_exposure.ut_list
            current_info['exptime'] = self.current_exposure.exptime
            current_info['binning'] = self.current_exposure.binning
            current_info['frametype'] = self.current_exposure.frametype
            current_info['target'] = self.current_exposure.target
            current_info['imgtype'] = self.current_exposure.imgtype
            current_info['glance'] = self.current_exposure.glance
            current_info['set_num'] = self.current_exposure.set_num
            current_info['set_pos'] = self.current_exposure.set_pos
            current_info['set_tot'] = self.current_exposure.set_tot
            current_info['set_id'] = self.current_exposure.set_id
            current_info['pointing_id'] = self.current_exposure.pointing_id
            temp_info['current_exposure'] = current_info
        else:
            temp_info['current_exposure'] = None
        temp_info['latest_run_number'] = self.latest_run_number
        temp_info['num_taken'] = self.num_taken

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

    def _save_images_cam(self, active_uts, all_info):
        """Thread to be started whenever an exposure is completed.

        By containing fetching images from the interfaces and saving them to
        FITS files within this thread a new exposure can be started as soon as
        the previous one is finished.
        """
        self.saving_thread_running = True
        cam_info = all_info['cam']
        current_exposure = cam_info['current_exposure']
        expstr = current_exposure['expstr'].capitalize()
        self.log.info('{}: Saving thread started'.format(expstr))

        if len(active_uts) == 0:
            # We must have aborted before we got to this stage
            self.log.warning('{}: Saving thread aborted'.format(expstr))
            self.saving_thread_running = False
            return

        # wait for the thread to loop, otherwise fetching delays the info check
        while True:
            if (self.info['time'] <= cam_info['time']) or (self.loop_time <= self.info['time']):
                # This is a little dogey...
                # If the exposure queue is running we want it to send the next exposure to start
                # before we start fetching the previous exposure.
                # The loops are to be fair pretty slow, due to the dependency check.
                # So we want to wait for 1 full loop, which will include an info check and the
                # prepare and start steps of the new exposure.
                # The first check is enough to ensure a new loop has started, but that isn't enough
                # - we need to let the entire new loop run through. So the second check waits until
                # the NEXT loop starts, which will update the loop time. Then this should break
                # BEFORE the info updates, again due to the dependency check.
                time.sleep(0.01)
            else:
                break

        # start fetching images from the interfaces in parallel
        future_images = {ut: None for ut in active_uts}
        with ThreadPoolExecutor(max_workers=len(active_uts)) as executor:
            for ut in active_uts:
                interface_id = params.UT_DICT[ut]['INTERFACE']
                interface = daemon_proxy(interface_id, timeout=99)
                try:
                    self.log.info('{}: Fetching exposure from camera {} ({})'.format(
                                  expstr, ut, interface_id))
                    future_images[ut] = executor.submit(interface.fetch_exposure, ut)
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
                        self.log.info('{}: Fetched exposure from camera {} ({})'.format(
                                      expstr, ut, interface_id))

                # keep looping until all the images and info are fetched
                # TODO: won't work if it fails
                if all(images[ut] is not None for ut in active_uts):
                    break

        # if taking glance images, clear all old glances (all, not just those in active UTs)
        glance = current_exposure['glance']
        if glance:
            clear_glance_files(params.TELESCOPE_NUMBER)

        # save images in parallel
        with ThreadPoolExecutor(max_workers=len(active_uts)) as executor:
            headers = {ut: None for ut in self.uts}
            for ut in active_uts:
                # get image data and filename
                image_data = images[ut]
                if not glance:
                    run_number = current_exposure['run_number']
                    filename = image_location(run_number, ut, params.TELESCOPE_NUMBER)
                else:
                    filename = glance_location(ut, params.TELESCOPE_NUMBER)

                # create and fill the FITS HDU
                hdu = make_fits(image_data, ut, all_info,
                                compress=params.COMPRESS_IMAGES,
                                measure_hfds=self.measure_hfds,
                                log=self.log
                                )
                headers[ut] = hdu.header

                # write the FITS file
                interface_id = params.UT_DICT[ut]['INTERFACE']
                self.log.info('{}: Saving exposure from camera {} ({}) to {}'.format(
                              expstr, ut, interface_id, filename))
                executor.submit(save_fits, hdu, filename,
                                log=self.log, log_debug=False, fancy_log=True)

        self.latest_headers = (self.num_taken, headers)
        self.saving_thread_running = False
        self.log.info('{}: Saving thread finished'.format(expstr))

    def _save_images_intf(self, active_uts, all_info):
        """Save the images on the interfaces, rather than fetching and saving locally."""
        self.saving_thread_running = True
        cam_info = all_info['cam']
        current_exposure = cam_info['current_exposure']
        expstr = current_exposure['expstr'].capitalize()
        self.log.info('{}: Saving thread started'.format(expstr))

        if len(active_uts) == 0:
            # We must have aborted before we got to this stage
            self.log.warning('{}: Saving thread aborted'.format(expstr))
            self.saving_thread_running = False
            return

        # if taking glance images, clear all old glances (all, not just those in active UTs)
        glance = current_exposure['glance']
        if glance:
            clear_glance_files(params.TELESCOPE_NUMBER)

        # save images on the interfaces in turn
        # no need for parallelisation here, they should return immediately as the interface
        # creates new processes for each
        headers = {ut: None for ut in self.uts}
        for ut in active_uts:
            interface_id = params.UT_DICT[ut]['INTERFACE']

            self.log.info('{}: Saving exposure on camera {} ({})'.format(expstr, ut, interface_id))
            try:
                with daemon_proxy(interface_id) as interface:
                    headers[ut] = interface.save_exposure(ut, all_info,
                                                          compress=params.COMPRESS_IMAGES,
                                                          measure_hfds=self.measure_hfds,
                                                          method='thread',
                                                          )
            except Exception:
                self.log.error('No response from interface {}'.format(interface_id))
                self.log.debug('', exc_info=True)

        self.latest_headers = (self.num_taken, headers)
        self.saving_thread_running = False
        self.log.info('{}: Saving thread finished'.format(expstr))

    # Control functions
    def take_image(self, exptime, binning, imgtype, ut_list):
        """Take a normal frame with the camera."""
        # Create exposure object
        exposure = Exposure(ut_list, exptime,
                            binning=binning, frametype='normal',
                            target='NA', imgtype=imgtype.upper())

        # Use the common function
        return self.take_exposure(exposure)

    def take_dark(self, exptime, binning, imgtype, ut_list):
        """Take dark frame with the camera."""
        # Create exposure object
        exposure = Exposure(ut_list, exptime,
                            binning=binning, frametype='dark',
                            target='NA', imgtype=imgtype.upper())

        # Use the common function
        return self.take_exposure(exposure)

    def take_glance(self, exptime, binning, imgtype, ut_list):
        """Take a glance frame with the camera (no run number)."""
        # Create exposure object
        exposure = Exposure(ut_list, exptime,
                            binning=binning, frametype='normal',
                            target='NA', imgtype=imgtype.upper(),
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
        if self.exposure_state != 'none':
            raise errors.HardwareStatusError('Cameras are already exposing')

        # Find and update run number, and store on the Exposure
        if not exposure.glance:
            with open(self.run_number_file, 'r') as f:
                old_run_number = int(f.read())
            new_run_number = old_run_number + 1
            with open(self.run_number_file, 'w') as f:
                f.write('{:d}'.format(new_run_number))
            exposure.run_number = new_run_number
            exposure.expstr = 'exposure r{:07d}'.format(new_run_number)
            self.latest_run_number = new_run_number
        else:
            exposure.run_number = None
            exposure.expstr = 'glance'

        # Set values
        self.current_exposure = exposure
        self.active_uts = sorted([ut for ut in ut_list])

        # Set flag
        self.take_exposure_flag = 1

        # Format return string
        s = 'Taking {}:'.format(exposure.expstr)
        for ut in ut_list:
            argstr = '{:.1f}s, {:.0f}x{:.0f}, {}'.format(exptime, binning, binning, frametype)
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
        if self.exposure_state != 'exposing':
            return 'Cameras are not currently exposing'

        # Set values
        self.abort_uts = sorted([ut for ut in ut_list if ut in self.active_uts])
        self.clear_uts = self.abort_uts.copy()

        # Set flag
        self.abort_exposure_flag = 1
        self.clear_queue_flag = 1

        # Format return string
        s = 'Aborting:'
        for ut in ut_list:
            s += '\n  '
            if ut not in self.abort_uts:
                s += 'Camera {} is not currently exposing'.format(ut)
            else:
                s += 'Aborting exposure on camera {}'.format(ut)
        return s

    def clear_queue(self, ut_list):
        """Clear any leftover images in the camera memory."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for ut in ut_list:
            if ut not in self.uts:
                raise ValueError('Unit telescope ID not in list {}'.format(self.uts))

        # Check current status
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise errors.HardwareStatusError('Cameras are exposing')

        # Set values
        self.clear_uts = sorted([ut for ut in ut_list])

        # Set flag
        self.clear_queue_flag = 1

        # Format return string
        s = 'Clearing:'
        for ut in ut_list:
            s += '\n  '
            if self.info[ut]['in_queue'] == 0:
                s += 'Camera {} has no images to clear'.format(ut)
            else:
                s += 'Clearing {} images from queue on camera {}'.format(
                    self.info[ut]['in_queue'], ut)
        return s

    def get_latest_headers(self):
        """Get the headers for the last completed exposure."""
        while self.saving_thread_running:
            # If we're currently saving we need to wait for the headers to be stored
            time.sleep(0.1)
        return self.latest_headers

    def set_window(self, x, y, dx, dy, ut_list):
        """Set the camera's image window area."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if x < 0 or y < 0:
            raise ValueError('Coordinates must be >= 0')
        if dx < 1 or dy < 1:
            raise ValueError('Width./height must be >= 1')
        for ut in ut_list:
            if ut not in self.uts:
                raise ValueError('Unit telescope ID not in list {}'.format(self.uts))

        # Check current status
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise errors.HardwareStatusError('Cameras are exposing')

        # Set values
        self.active_uts = sorted([ut for ut in ut_list])
        for ut in ut_list:
            self.target_window[ut] = (int(x), int(y), int(dx), int(dy))

        # Set flag
        self.set_window_flag = 1

        # Format return string
        s = 'Setting:'
        areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
        for ut in ut_list:
            s += '\n  '
            s += 'Setting window on camera {} to {}'.format(ut, areastr)
        return s

    def remove_window(self, ut_list):
        """Set the camera's image window area to full-frame."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for ut in ut_list:
            if ut not in self.uts:
                raise ValueError('Unit telescope ID not in list {}'.format(self.uts))

        # Check current status
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise errors.HardwareStatusError('Cameras are exposing')

        # Set values
        self.active_uts = sorted([ut for ut in ut_list])
        for ut in ut_list:
            self.target_window[ut] = None

        # Set flag
        self.set_window_flag = 1

        # Format return string
        s = 'Setting:'
        for ut in ut_list:
            s += '\n  '
            s += 'Setting window on camera {} to full-frame'.format(ut)
        return s

    def measure_image_hfds(self, command):
        """Enable or disable measuring image HFDs when saving."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.wait_for_info()
        if command == 'on' and self.measure_hfds:
            return 'Measuring HFDs is already enabled'
        elif command == 'off' and not self.measure_hfds:
            return 'Measuring HFDs is already disabled'

        # Set flag
        if command == 'on':
            self.log.info('Enabling HFD measurement')
            self.measure_hfds = True
        elif command == 'off':
            self.log.info('Disabling HFD measurement')
            self.measure_hfds = False

        if command == 'on':
            return 'Enabling image HFD measurement'
        elif command == 'off':
            return 'Disabling image HFD measurement'

    def set_temperature(self, target_temp, ut_list):
        """Set the camera's temperature."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if target_temp.lower() == 'cool':
            target_temp = self.cool_temp
        elif target_temp.lower() == 'warm':
            target_temp = self.warm_temp
        else:
            try:
                target_temp = float(target_temp)
            except ValueError:
                raise ValueError('Temperature must be a float or "cool" or "warm"')
            if not (-55 <= target_temp <= 45):
                raise ValueError('Temperature must be between -55 and 45')
        for ut in ut_list:
            if ut not in self.uts:
                raise ValueError('Unit telescope ID not in list {}'.format(self.uts))

        # Check current status
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise errors.HardwareStatusError('Cameras are exposing')

        # Set values
        self.active_uts = sorted([ut for ut in ut_list])
        for ut in ut_list:
            self.target_temp[ut] = target_temp

        # Set flag
        self.set_temp_flag = 1

        # Format return string
        s = 'Setting:'
        for ut in ut_list:
            s += '\n  '
            s += 'Setting temperature on camera {} to {}'.format(ut, target_temp)
        return s

    def is_exposing(self):
        """Return if the cameras are exposing.

        Used to save time when the exposure queue doesn't need the full info.
        """
        return self.take_exposure_flag


if __name__ == '__main__':
    daemon = CamDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
