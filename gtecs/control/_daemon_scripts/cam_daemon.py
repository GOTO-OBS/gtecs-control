#!/usr/bin/env python3
"""Daemon to control cameras."""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import database as db
from gtecs.control import params
from gtecs.control.daemons import (BaseDaemon, DaemonDependencyError, HardwareError,
                                   daemon_proxy, get_daemon_host)
from gtecs.control.exposures import Exposure
from gtecs.control.fits import (clear_glance_files, get_daemon_info, glance_location,
                                image_location, make_fits, make_header, save_fits)
from gtecs.control.slack import send_slack_msg


class CamDaemon(BaseDaemon):
    """Camera hardware daemon class."""

    def __init__(self):
        super().__init__('cam')

        # command flags
        self.take_exposure_flag = 0
        self.abort_exposure_flag = 0
        self.clear_queue_flag = 1  # clear on daemon restart
        self.set_window_flag = 0
        self.set_temp_flag = 0

        # camera variables
        self.uts = params.UTS_WITH_CAMERAS.copy()
        self.active_uts = []
        self.clear_uts = self.uts.copy()  # clear on daemon restart
        self.interfaces = {f'cam{ut}' for ut in self.uts}

        self.run_number_file = os.path.join(params.FILE_PATH, 'run_number')
        if not os.path.exists(self.run_number_file):
            with open(self.run_number_file, 'w') as f:
                f.write('0')
                f.close()
        with open(self.run_number_file, 'r') as f:
            self.latest_run_number = int(f.read())
        self.num_taken = 0

        self.queues_cleared = {ut: False for ut in self.uts}

        self.exposure_state = 'none'
        self.current_exposure = None
        self.temp_headers = None
        self.latest_headers = (self.num_taken, {ut: None for ut in self.uts})
        self.exposure_start_time = {ut: 0 for ut in self.uts}
        self.exposure_finished = {ut: False for ut in self.uts}
        self.exposing_start_time = 0
        self.image_ready = {ut: False for ut in self.uts}
        self.saving_thread_running = False

        self.target_window = {ut: None for ut in self.uts}
        self.measure_hfds = False

        self.cool_temp = {ut: int(params.UT_DICT[ut]['CAMERA']['IMAGING_TEMPERATURE'])
                          for ut in self.uts}
        self.warm_temp = {ut: int(params.UT_DICT[ut]['CAMERA']['STANDBY_TEMPERATURE'])
                          for ut in self.uts}
        self.target_temp = {ut: self.warm_temp[ut] for ut in self.uts}  # Start at standby temp

        # dependencies
        for interface_id in self.interfaces:
            self.dependencies.add(interface_id)

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
                        argstr = '{:.1f}s, {:.0f}x{:.0f}, {}'.format(exptime,
                                                                     binning, binning,
                                                                     frametype)
                        self.log.info('{}: Preparing exposure ({}) on camera {}'.format(
                                      expstr, argstr, ut))
                        try:
                            with daemon_proxy(f'cam{ut}') as interface:
                                # set exposure time and frame type
                                reply = interface.set_exposure(exptime_ms, frametype)
                                if reply:
                                    self.log.info(reply)
                                # set binning factor
                                reply = interface.set_binning(binning, binning)
                                if reply:
                                    self.log.info(reply)
                                # set window
                                if self.target_window[ut] is None:
                                    # we need to set the default to full-frame here, since on
                                    # startup the cameras default to the active area only
                                    reply = interface.set_window_full()
                                else:
                                    # if the area isn't None then it should have been set by the
                                    # set_window function, however it could be forgotten
                                    # if the cameras are rebooted
                                    x, y, dx, dy = self.target_window[ut]
                                    reply = interface.set_window(x, y, dx, dy)
                                if reply:
                                    self.log.info(reply)
                        except Exception:
                            self.log.error('No response from interface cam{}'.format(ut))
                            self.log.debug('', exc_info=True)

                    # Start the exposure
                    # (separate from the above, so they all start closer together)
                    for ut in self.active_uts:
                        self.log.info('{}: Starting exposure on camera {}'.format(
                                      expstr, ut))
                        try:
                            with daemon_proxy(f'cam{ut}') as interface:
                                # save the exact start time for each camera
                                self.exposure_start_time[ut] = time.time()
                                # start the exposure
                                reply = interface.start_exposure()
                                if reply:
                                    self.log.info(reply)
                        except Exception:
                            self.log.error('No response from interface cam{}'.format(ut))
                            self.log.debug('', exc_info=True)
                    self.exposing_start_time = self.loop_time
                    self.exposure_state = 'exposing'

                    # Add the exposure to the database
                    try:
                        with db.session_manager() as session:
                            db_exposure = db.Exposure(
                                run_number=self.current_exposure.run_number,
                                set_number=self.current_exposure.set_num,
                                exptime=self.current_exposure.exptime,
                                filt=self.current_exposure.filt,
                                frametype=self.current_exposure.frametype,
                                ut_mask=self.current_exposure.ut_mask,
                                start_time=Time(self.loop_time, format='unix'),
                                stop_time=None,
                                completed=False,
                                exposure_set_id=self.current_exposure.set_id,
                                pointing_id=self.current_exposure.pointing_id,
                            )
                            session.add(db_exposure)
                            session.commit()
                            self.current_exposure.db_id = db_exposure.db_id
                    except Exception:
                        self.log.error('Failed to add exposure to the database')
                        self.log.debug('', exc_info=True)
                        self.current_exposure.db_id = None

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
                        try:
                            with daemon_proxy(f'cam{ut}') as interface:
                                remaining = interface.get_time_remaining()
                            if remaining == 0 and not self.exposure_finished[ut]:
                                self.log.info('{}: Finished exposure on camera {}'.format(
                                              expstr, ut))
                                self.exposure_finished[ut] = True
                        except Exception:
                            self.log.error('No response from interface cam{}'.format(ut))
                            self.log.debug('', exc_info=True)

                    if all(self.exposure_finished[ut] for ut in self.active_uts):
                        self.exposure_state = 'reading_out'

                if self.exposure_state == 'reading_out':
                    # STATE 3: Wait for the readout to finish and images are ready to be saved
                    # The exposure is finished but the cameras are still reading out

                    # Construct the image headers
                    # We only need to do this once per exposure
                    if self.temp_headers is None:
                        # Fetch info from the other daemons
                        self.log.info('{}: Fetching info from other daemons'.format(expstr))
                        daemon_info, bad = get_daemon_info(self.info.copy(), log=self.log)
                        self.log.info('{}: Fetched info from other daemons'.format(expstr))
                        if len(bad) > 0:
                            # We failed to get at least one info set, log and tell Slack
                            self.log.error('Bad info: {}'.format(bad))
                            send_slack_msg(f'Cam failed to get info for: {bad}')
                        # Now make the headers for each camera
                        self.log.info('{}: Creating image headers'.format(expstr))
                        headers = {}
                        for ut in self.active_uts:
                            try:
                                headers[ut] = make_header(ut, daemon_info)
                            except Exception:
                                self.log.error('Failed to make header for camera {}'.format(ut))
                                self.log.debug('', exc_info=True)
                                send_slack_msg(f'Cam failed to make image header for UT{ut}')
                                headers[ut] = None
                        self.temp_headers = headers
                        self.log.info('{}: Created image headers'.format(expstr))

                    # Wait for the images to be ready
                    for ut in self.active_uts:
                        try:
                            with daemon_proxy(f'cam{ut}') as interface:
                                ready = interface.exposure_ready()
                            if ready and not self.image_ready[ut]:
                                self.log.info('{}: Ready to save exposure on camera {}'.format(
                                              expstr, ut))
                                self.image_ready[ut] = True
                        except Exception:
                            self.log.error('No response from interface cam{}'.format(ut))
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
                                             args=[self.active_uts.copy(),
                                                   self.temp_headers.copy(),
                                                   self.info.copy()])
                    else:
                        # tell the interfaces to save the files themselves
                        t = threading.Thread(target=self._save_images_intf,
                                             args=[self.active_uts.copy(),
                                                   self.temp_headers.copy(),
                                                   self.info.copy()])
                    t.daemon = True
                    t.start()

                    # Update the database entry
                    with db.session_manager() as session:
                        query = session.query(db.Exposure)
                        query = query.filter(db.Exposure.db_id == self.current_exposure.db_id)
                        db_exposure = query.one()
                        db_exposure.stop_time = Time(self.loop_time, format='unix')
                        db_exposure.completed = True  # Marked as completed
                        session.commit()

                    # Reset values, ready for next exposure
                    self.exposure_state = 'none'
                    self.current_exposure = None
                    self.exposing_start_time = 0
                    self.exposure_start_time = {ut: 0 for ut in self.uts}
                    self.exposure_finished = {ut: False for ut in self.uts}
                    self.image_ready = {ut: False for ut in self.uts}
                    self.temp_headers = None
                    self.active_uts = []
                    self.num_taken += 1
                    self.take_exposure_flag = 0
                    self.force_check_flag = True

            # abort exposure
            if self.abort_exposure_flag:
                try:
                    for ut in self.active_uts:
                        expstr = self.current_exposure.expstr.capitalize()
                        self.log.info('{}: Aborting exposure on camera {}'.format(
                                      expstr, ut))
                        try:
                            with daemon_proxy(f'cam{ut}') as interface:
                                reply = interface.abort_exposure()
                                if reply:
                                    self.log.info(reply)
                        except Exception:
                            self.log.error('No response from interface cam{}'.format(ut))
                            self.log.debug('', exc_info=True)

                    # We've aborted everything, stop the exposure state machine
                    # TODO: Could there be an actual "abort" state?
                    #       Or this could be within State 2 when we're waiting for exposures,
                    #       since that's the only time this should trigger.
                    self.exposure_state = 'none'

                    # Update the database entry
                    with db.session_manager() as session:
                        query = session.query(db.Exposure)
                        query = query.filter(db.Exposure.db_id == self.current_exposure.db_id)
                        db_exposure = query.one()
                        db_exposure.stop_time = Time(self.loop_time, format='unix')
                        db_exposure.completed = False  # Marked as aborted
                        session.commit()

                    # Reset values, ready for next exposure
                    # TODO: Could be one "cleanup" state
                    self.current_exposure = None
                    self.exposing_start_time = 0
                    self.exposure_start_time = {ut: 0 for ut in self.uts}
                    self.exposure_finished = {ut: False for ut in self.uts}
                    self.image_ready = {ut: False for ut in self.uts}
                    self.temp_headers = None
                    self.active_uts = []
                    self.num_taken += 1
                    self.take_exposure_flag = 0

                    # Also clear any leftover images in the camera queues
                    self.clear_uts = self.active_uts.copy()
                    self.clear_queue_flag = 1
                except Exception:
                    self.log.error('abort_exposure command failed')
                    self.log.debug('', exc_info=True)
                self.abort_exposure_flag = 0
                self.force_check_flag = True

            # clear camera data queue
            if self.clear_queue_flag:
                # TODO: Add a verification key to the interfaces to keep track of them?
                # TODO: Also verify the data size matches what's expected for the binfac!)
                try:
                    for ut in self.clear_uts:
                        self.log.info('Clearing queue on camera {}'.format(ut))
                        try:
                            with daemon_proxy(f'cam{ut}') as interface:
                                interface.clear_exposure_queue()
                        except Exception:
                            self.log.error('No response from interface cam{}'.format(ut))
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
                        if self.target_window[ut] is None:
                            # reset to full
                            areastr = 'full-frame'
                        else:
                            x, y, dx, dy = self.target_window[ut]
                            areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
                        self.log.info('Setting window on camera {} to {}'.format(ut, areastr))
                        try:
                            with daemon_proxy(f'cam{ut}') as interface:
                                if self.target_window[ut] is None:
                                    reply = interface.set_window_full()
                                else:
                                    x, y, dx, dy = self.target_window[ut]
                                    reply = interface.set_window(x, y, dx, dy)
                                if reply:
                                    self.log.info(reply)
                        except Exception:
                            self.log.error('No response from interface cam{}'.format(ut))
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
                        target_temp = self.target_temp[ut]
                        self.log.info('Setting temperature on camera {} to {}'.format(
                            ut, target_temp))
                        try:
                            with daemon_proxy(f'cam{ut}') as interface:
                                reply = interface.set_temp(target_temp)
                                if reply:
                                    self.log.info(reply)
                        except Exception:
                            self.log.error('No response from interface cam{}'.format(ut))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('set_temp command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.set_temp_flag = 0
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')

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
                ut_info['interface_id'] = f'cam{ut}'

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

                with daemon_proxy(f'cam{ut}') as interface:
                    ut_info['serial_number'] = interface.get_serial_number()
                    ut_info['hw_class'] = interface.get_class()
                    ut_info['remaining'] = interface.get_time_remaining()
                    ut_info['in_queue'] = interface.get_queue_length()
                    ut_info['ccd_temp'] = interface.get_temp('CCD')
                    ut_info['base_temp'] = interface.get_temp('BASE')
                    ut_info['target_temp'] = self.target_temp[ut]
                    ut_info['cool_temp'] = self.cool_temp[ut]
                    ut_info['warm_temp'] = self.warm_temp[ut]
                    ut_info['cooler_power'] = interface.get_cooler_power()
                    cam_info = interface.get_camera_info()
                    ut_info['cam_info'] = cam_info
                    ut_info['x_pixel_size'] = cam_info['pixel_size'][0]
                    ut_info['y_pixel_size'] = cam_info['pixel_size'][1]
                    ut_info['image_size'] = interface.get_image_size()
                    ut_info['window_area'] = interface.get_window()
                    ut_info['active_area'] = interface.get_active_area()
                    ut_info['full_area'] = interface.get_full_area()

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
            current_info['exptime'] = self.current_exposure.exptime
            current_info['binning'] = self.current_exposure.binning
            current_info['frametype'] = self.current_exposure.frametype
            current_info['target'] = self.current_exposure.target
            current_info['imgtype'] = self.current_exposure.imgtype
            current_info['glance'] = self.current_exposure.glance
            current_info['uts'] = self.current_exposure.uts
            current_info['set_num'] = self.current_exposure.set_num
            current_info['set_pos'] = self.current_exposure.set_pos
            current_info['set_tot'] = self.current_exposure.set_tot
            current_info['set_id'] = self.current_exposure.set_id
            current_info['pointing_id'] = self.current_exposure.pointing_id
            current_info['db_id'] = self.current_exposure.db_id
            temp_info['current_exposure'] = current_info
        else:
            temp_info['current_exposure'] = None
        temp_info['latest_run_number'] = self.latest_run_number
        temp_info['num_taken'] = self.num_taken

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(ut,
                                       temp_info[ut]['status']
                                       if temp_info[ut] is not None else 'ERROR')
                        for ut in self.uts]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Cameras are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(ut,
                                           self.info[ut]['status']
                                           if self.info[ut] is not None else 'ERROR')
                            for ut in self.uts]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Cameras are {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    def _save_images_cam(self, active_uts, header_info, cam_info):
        """Thread to be started whenever an exposure is completed.

        By containing fetching images from the interfaces and saving them to
        FITS files within this thread a new exposure can be started as soon as
        the previous one is finished.
        """
        self.saving_thread_running = True
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
                with daemon_proxy(f'cam{ut}', timeout=99) as interface:
                    try:
                        self.log.info('{}: Fetching exposure from camera {}'.format(expstr, ut))
                        future_images[ut] = executor.submit(interface.fetch_exposure)
                    except Exception:
                        self.log.error('No response from interface cam{}'.format(ut))
                        self.log.debug('', exc_info=True)

            # wait for images to be fetched
            images = {ut: None for ut in active_uts}
            while True:
                time.sleep(0.001)
                for ut in active_uts:
                    if future_images[ut].done() and images[ut] is None:
                        images[ut] = future_images[ut].result()
                        self.log.info('{}: Fetched exposure from camera {}'.format(expstr, ut))

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
            # We store the returned final headers, as they will include any extra cards which depend
            # on the image data (e.g. median counts, HFDs etc).
            full_headers = {ut: None for ut in self.uts}
            for ut in active_uts:
                # get image data and filename
                image_data = images[ut]
                if not glance:
                    run_number = current_exposure['run_number']
                    filename = image_location(run_number, ut, params.TELESCOPE_NUMBER)
                else:
                    filename = glance_location(ut, params.TELESCOPE_NUMBER)
                if header_info[ut] is None:
                    # There's not much we can do with no header, but we can save the raw data
                    # in a clearly-marked invalid file
                    filename += '.no_header'

                # create and fill the FITS HDU
                hdu = make_fits(image_data,
                                header_cards=header_info[ut],
                                compress=params.COMPRESS_IMAGES,
                                measure_hfds=self.measure_hfds,
                                log=self.log
                                )
                full_headers[ut] = hdu.header

                # write the FITS file
                self.log.info('{}: Saving exposure from camera {} to {}'.format(
                              expstr, ut, filename))
                executor.submit(save_fits, hdu, filename,
                                log=self.log, log_debug=False, fancy_log=True)

                # Record the image in the control database
                try:
                    with db.session_manager() as session:
                        db_image = db.Image(
                            ut=ut,
                            filename=filename,
                            header=full_headers[ut],
                            exposure_id=cam_info['current_exposure']['db_id'],
                        )
                        session.add(db_image)
                        session.commit()
                except Exception:
                    self.log.error('Failed to add image to the database')
                    self.log.debug('', exc_info=True)

        self.latest_headers = (self.num_taken, full_headers)
        self.saving_thread_running = False
        self.log.info('{}: Saving thread finished'.format(expstr))

    def _save_images_intf(self, active_uts, header_info, cam_info):
        """Save the images on the interfaces, rather than fetching and saving locally."""
        self.saving_thread_running = True
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
        # We store the returned final headers, as they will include any extra cards which depend
        # on the image data (e.g. median counts, HFDs etc).
        full_headers = {ut: None for ut in self.uts}
        for ut in active_uts:
            # get filename
            if not glance:
                run_number = current_exposure['run_number']
                filename = image_location(run_number, ut, params.TELESCOPE_NUMBER)
            else:
                filename = glance_location(ut, params.TELESCOPE_NUMBER)
            if header_info[ut] is None:
                # There's not much we can do with no header, but we can save the raw data
                # in a clearly-marked invalid file
                filename += '.no_header'

            self.log.info('{}: Saving exposure on camera {}'.format(expstr, ut))
            try:
                with daemon_proxy(f'cam{ut}') as interface:
                    full_headers[ut] = interface.save_exposure(
                        filename=filename,
                        header_cards=header_info[ut],
                        compress=params.COMPRESS_IMAGES,
                        measure_hfds=self.measure_hfds,
                    )
            except Exception:
                self.log.error('No response from interface cam{}'.format(ut))
                self.log.debug('', exc_info=True)

            # Record the image in the control database
            try:
                with db.session_manager() as session:
                    db_image = db.Image(
                        ut=ut,
                        filename=filename.split('/')[-1],
                        header=full_headers[ut],
                        exposure_id=cam_info['current_exposure']['db_id'],
                    )
                    session.add(db_image)
                    session.commit()
            except Exception:
                self.log.error('Failed to add image to the database')
                self.log.debug('', exc_info=True)

        self.latest_headers = (self.num_taken, full_headers)
        self.saving_thread_running = False
        self.log.info('{}: Saving thread finished'.format(expstr))

    # Control functions
    def take_image(self, exptime, binning, imgtype, uts=None):
        """Take a normal frame with the selected cameras."""
        if uts is None:
            uts = self.uts.copy()
        exposure = Exposure(
            exptime,
            binning=binning,
            frametype='normal',
            target='NA',
            imgtype=imgtype.upper(),
            glance=False,
            uts=uts,
        )
        return self.take_exposure(exposure)

    def take_dark(self, exptime, binning, imgtype, uts=None):
        """Take dark frame with the selected cameras."""
        if uts is None:
            uts = self.uts.copy()
        exposure = Exposure(
            exptime,
            binning=binning,
            frametype='dark',
            target='NA',
            imgtype=imgtype.upper(),
            glance=False,
            uts=uts,
        )
        return self.take_exposure(exposure)

    def take_glance(self, exptime, binning, imgtype, uts=None):
        """Take a glance frame with the selected cameras (no run number)."""
        if uts is None:
            uts = self.uts.copy()
        exposure = Exposure(
            exptime,
            binning=binning,
            frametype='normal',
            target='NA',
            imgtype=imgtype.upper(),
            glance=True,
            uts=uts,
        )
        return self.take_exposure(exposure)

    def take_exposure(self, exposure):
        """Take an exposure with the selected cameras from an Exposure object."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if int(exposure.exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if int(exposure.binning) < 1 or (int(exposure.binning) - exposure.binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if exposure.frametype not in ['normal', 'dark']:
            raise ValueError('Invalid frame type: "{}"'.format(exposure.frametype))
        uts = exposure.uts
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise HardwareError(f'Cameras are already exposing: {self.active_uts}')

        # Find and update run number, and store on the Exposure
        if not exposure.glance:
            with open(self.run_number_file, 'r') as f:
                old_run_number = int(f.read())
            new_run_number = old_run_number + 1
            with open(self.run_number_file, 'w') as f:
                f.write('{:d}'.format(new_run_number))
            exposure.run_number = new_run_number
            exposure.expstr = f'exposure r{new_run_number:07d}'
            self.latest_run_number = new_run_number
        else:
            exposure.run_number = None
            exposure.expstr = 'glance'

        self.current_exposure = exposure
        self.active_uts = sorted(uts)
        self.take_exposure_flag = 1

        return exposure.expstr

    def abort_exposure(self):
        """Abort an ongoing exposure (on all cameras)."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')

        if self.exposure_state == 'exposing':
            active_uts = self.active_uts.copy()
            self.abort_exposure_flag = 1
            return active_uts
        else:
            return []

    def clear_queue(self, uts=None):
        """Clear any leftover images in the camera memory."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if uts is None:
            uts = self.uts.copy()
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise HardwareError(f'Cameras are exposing: {self.active_uts}')

        queue_length = {ut: self.info[ut]['in_queue'] for ut in uts}
        self.clear_uts = sorted(uts)
        self.clear_queue_flag = 1

        return queue_length

    def get_latest_headers(self):
        """Get the headers for the last completed exposure."""
        while self.saving_thread_running:
            # If we're currently saving we need to wait for the headers to be stored
            raise HardwareError('Cameras are currently reading out')
        return self.latest_headers

    def set_window(self, x, y, dx, dy, uts=None):
        """Set the camera's image window area."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if x < 0 or y < 0:
            raise ValueError('Coordinates must be >= 0')
        if dx < 1 or dy < 1:
            raise ValueError('Width/height must be >= 1')
        if uts is None:
            uts = self.uts.copy()
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise HardwareError(f'Cameras are exposing: {self.active_uts}')

        self.active_uts = sorted(uts)
        for ut in uts:
            self.target_window[ut] = (int(x), int(y), int(dx), int(dy))
        self.set_window_flag = 1

    def remove_window(self, uts=None):
        """Set the camera's image window area to full-frame."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if uts is None:
            uts = self.uts.copy()
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise HardwareError(f'Cameras are exposing: {self.active_uts}')

        self.active_uts = sorted(uts)
        for ut in uts:
            self.target_window[ut] = None
        self.set_window_flag = 1

    def measure_image_hfds(self, command):
        """Enable or disable measuring image HFDs when saving."""
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        if command == 'on' and self.measure_hfds is False:
            self.log.info('Enabling HFD measurement')
            self.measure_hfds = True
        elif command == 'off' and self.measure_hfds is True:
            self.log.info('Disabling HFD measurement')
            self.measure_hfds = False

    def set_temperature(self, target_temp, uts=None):
        """Set the camera's temperature."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if target_temp.lower() not in ['cool', 'warm']:
            try:
                target_temp = float(target_temp)
            except ValueError:
                raise ValueError('Temperature must be a float or "cool" or "warm"')
            if not (-55 <= target_temp <= 45):
                raise ValueError('Temperature must be between -55 and 45')
        if uts is None:
            uts = self.uts.copy()
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')
        if self.exposure_state != 'none' or len(self.active_uts) > 0:
            raise HardwareError(f'Cameras are exposing: {self.active_uts}')

        self.active_uts = sorted(uts)
        for ut in uts:
            if target_temp == 'cool':
                self.target_temp[ut] = self.cool_temp[ut]
            elif target_temp == 'warm':
                self.target_temp[ut] = self.warm_temp[ut]
            else:
                self.target_temp[ut] = target_temp
        self.set_temp_flag = 1

    def is_exposing(self):
        """Return if the cameras are exposing.

        Used to save time when the exposure queue doesn't need the full info.
        """
        return self.take_exposure_flag

    # Info function
    def get_info_string(self, verbose=False, force_update=False):
        """Get a string for printing status info."""
        info = self.get_info(force_update)
        if not verbose:
            msg = ''
            for ut in info['uts']:
                host, port = get_daemon_host(info[ut]['interface_id'])
                msg += 'CAMERA {} ({}:{}) '.format(ut, host, port)
                if info[ut]['status'] != 'Exposing':
                    msg += '  Temp: {:6.2f}C '.format(info[ut]['ccd_temp'])
                    msg += '  [{}]\n'.format(info[ut]['status'])
                else:
                    current_exposure = info['current_exposure']
                    expstr = current_exposure['expstr']
                    if 'exposure' in expstr:
                        expstr = expstr.split(' ')[1]
                    msg += '  {} {} ({:.2f}s)\n'.format(
                        info[ut]['status'], expstr, info[ut]['remaining'])
            msg = msg.rstrip()
        else:
            msg = '####### CAMERA INFO #######\n'
            for ut in info['uts']:
                host, port = get_daemon_host(info[ut]['interface_id'])
                msg += 'CAMERA {} ({}:{})\n'.format(ut, host, port)
                if info[ut]['status'] != 'Exposing':
                    msg += 'Status: {}\n'.format(info[ut]['status'])
                else:
                    current_exposure = info['current_exposure']
                    expstr = current_exposure['expstr']
                    if 'exposure' in expstr:
                        expstr = expstr.split(' ')[1]
                    if current_exposure and ut in current_exposure['uts']:
                        msg += 'Status: {} {} ({:.2f}s)\n'.format(
                            info[ut]['status'], expstr, info[ut]['remaining'])
                        msg += 'Exposure time:      {:.2f}s\n'.format(current_exposure['exptime'])
                        msg += 'Binning:            {:.0f}\n'.format(current_exposure['binning'])
                        msg += 'Frame type:         {}\n'.format(current_exposure['frametype'])
                msg += 'Image window:       {}\n'.format(info[ut]['window_area'])
                msg += 'CCD Temperature:    {:.2f}C\n'.format(info[ut]['ccd_temp'])
                msg += 'Target Temperature: {:.2f}C\n'.format(info[ut]['target_temp'])
                msg += 'Base Temperature:   {:.2f}C\n'.format(info[ut]['base_temp'])
                msg += 'Cooler power:       {:.0f}%\n'.format(info[ut]['cooler_power'])
                msg += 'Serial number:      {}\n'.format(info[ut]['serial_number'])
                msg += 'Hardware class:     {}\n'.format(info[ut]['hw_class'])
                msg += '~~~~~~~\n'
            msg += 'Latest run number:  {:d}\n'.format(info['latest_run_number'])
            msg += 'Exposures taken:    {:d}\n'.format(info['num_taken'])
            msg += '~~~~~~~\n'
            msg += 'Uptime: {:.1f}s\n'.format(info['uptime'])
            msg += 'Timestamp: {}\n'.format(info['timestamp'])
            msg += '###########################'
        return msg


if __name__ == '__main__':
    daemon = CamDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
