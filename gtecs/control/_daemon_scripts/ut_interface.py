#!/usr/bin/env python3
"""Interface to access hardware connected to the UTs (cameras, focusers, filter wheels)."""

import argparse
import json
import multiprocessing as mp
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon
from gtecs.control.fits import glance_location, image_location, make_fits, save_fits
from gtecs.control.hardware.fli import FLICamera, FLIFilterWheel, FLIFocuser
from gtecs.control.hardware.fli import FakeCamera, FakeFilterWheel, FakeFocuser
from gtecs.control.hardware.ota import FakeH400, H400
from gtecs.control.hardware.rasa import FocusLynxHub


class UTInterfaceDaemon(BaseDaemon):
    """UT interface daemon class."""

    def __init__(self, interface_id, hw_dict):
        super().__init__(interface_id)

        # hardware
        self.hw_dict = hw_dict
        self.uts = hw_dict.keys()

        self.ota_params = {ut: self.hw_dict[ut]['OTA'] for ut in self.uts}

        self.cameras = {ut: None for ut in self.uts}
        self.cam_params = {ut: self.hw_dict[ut]['CAMERA'] for ut in self.uts}

        self.focusers = {ut: None for ut in self.uts}
        self.foc_params = {ut: self.hw_dict[ut]['FOCUSER'] for ut in self.uts}

        self.filterwheels = {ut: None for ut in self.uts}
        self.filt_params = {ut: self.hw_dict[ut]['FILTERWHEEL'] for ut in self.uts}

        # Extra dictionary for RASA focuser hubs
        self.focuser_hubs = {}
        for ut in self.foc_params:
            if self.foc_params[ut] is not None and self.foc_params[ut]['CLASS'] == 'RASA':
                port = self.foc_params[ut]['PORT']
                if port not in self.focuser_hubs:
                    self.focuser_hubs[port] = [ut]
                elif ut not in self.focuser_hubs[port]:
                    self.focuser_hubs[port] += [ut]

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

                # Try to connect to the hardware
                self._connect()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's sucsessful
                if self.hardware_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

                # If there is an error then getting info failed.
                # Restart the loop to try reconnecting above.
                if self.hardware_error:
                    continue

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _connect(self):
        """Connect to hardware."""
        # Connect to cameras
        for ut in self.cameras:
            if self.cameras[ut] is None and self.cam_params[ut] is not None:
                hw_name = 'camera_{}'.format(ut)
                hw_params = self.cam_params[ut]
                if hw_name not in self.bad_hardware:
                    self.log.info('Connecting to Camera {}'.format(ut))
                    self.log.debug(hw_params)
                try:
                    if 'CLASS' not in hw_params:
                        raise ValueError('Missing class')
                    hw_class = hw_params['CLASS']

                    # Connect to appropriate hardware class
                    if hw_class == 'FLI':
                        # FLI USB Camera, needs a serial number
                        if 'SERIAL' not in hw_params:
                            raise ValueError('Missing serial number')
                        camera = FLICamera.locate_device(hw_params['SERIAL'])
                        if camera is None and params.FAKE_FLI:
                            self.log.info('Creating a fake Camera')
                            camera = FakeCamera('/dev/fake', 'FakeCamera')
                            camera.serial_number = hw_params['SERIAL']
                            camera.connected = True
                        if camera is None:
                            raise ValueError('Could not locate hardware')

                    else:
                        raise ValueError('Unknown class: {}'.format(hw_class))

                    if not camera.connected:
                        raise ValueError('Could not connect to hardware')

                    self.log.info('Connected to {}'.format(camera.serial_number))
                    self.cameras[ut] = camera
                    if hw_name in self.bad_hardware:
                        self.bad_hardware.remove(hw_name)

                except Exception:
                    self.cameras[ut] = None
                    self.log.debug('', exc_info=True)
                    if hw_name not in self.bad_hardware:
                        self.log.error('Failed to connect to hardware')
                        self.bad_hardware.add(hw_name)

        # Connect to focusers
        for ut in self.focusers:
            if self.focusers[ut] is None and self.foc_params[ut] is not None:
                hw_name = 'focuser_{}'.format(ut)
                hw_params = self.foc_params[ut]
                if hw_name not in self.bad_hardware:
                    self.log.info('Connecting to Focuser {}'.format(ut))
                    self.log.debug(hw_params)
                try:
                    if 'CLASS' not in hw_params:
                        raise ValueError('Missing class')
                    hw_class = hw_params['CLASS']

                    # Connect to appropriate hardware class
                    if hw_class == 'FLI':
                        # FLI USB Focuser, needs a serial number
                        if 'SERIAL' not in hw_params:
                            raise ValueError('Missing serial number')
                        focuser = FLIFocuser.locate_device(hw_params['SERIAL'])
                        if focuser is None and params.FAKE_FLI:
                            self.log.info('Creating a fake Focuser')
                            focuser = FakeFocuser('/dev/fake', 'FakeCamera')
                            focuser.serial_number = hw_params['SERIAL']
                            focuser.connected = True
                        if focuser is None:
                            raise ValueError('Could not locate hardware')

                    elif hw_class == 'RASA':
                        # RASA FocusLynx Focuser, needs a port and a serial number
                        if 'PORT' not in hw_params:
                            raise ValueError('Missing serial port')
                        if 'SERIAL' not in hw_params:
                            raise ValueError('Missing serial number')
                        # We have a single class for the FocusLynxHub, which is connected to
                        # two focusers. It makes things more complicated here, but having two
                        # FocusLynx classes sharing the same port caused all sorts of problems.
                        # Try to connect only if neither UTs are connected. We set the focuser_hubs
                        # dict in __init__ to match the two UTs by port.
                        hub_uts = self.focuser_hubs[hw_params['PORT']]
                        # Always try to connect to the lower UT number, to simplfy things.
                        if ut == min(hub_uts):
                            focuser = FocusLynxHub.locate_device(hw_params['PORT'])
                        else:
                            focuser = self.focusers[min(hub_uts)]
                        if focuser is None:
                            raise ValueError('Could not locate hardware')
                        # We need to get the device number.
                        if 'DEV_NUMBER' not in hw_params:
                            dev_number = focuser.get_dev_number(hw_params['SERIAL'])
                            self.foc_params[ut]['DEV_NUMBER'] = dev_number

                    elif hw_class == 'ASA':
                        # ASA H400 in-built Focuser, needs a port and a serial number
                        if 'PORT' not in hw_params:
                            raise ValueError('Missing serial port')
                        if 'SERIAL' not in hw_params:
                            raise ValueError('Missing serial number')
                        focuser = H400.locate_device(hw_params['PORT'], hw_params['SERIAL'])
                        if focuser is None and params.FAKE_ASA:
                            self.log.info('Creating a fake Focuser')
                            focuser = FakeH400('/dev/fake', hw_params['SERIAL'])
                        if focuser is None:
                            raise ValueError('Could not locate hardware')

                    else:
                        raise ValueError('Unknown class: {}'.format(hw_class))

                    if not focuser.connected:
                        raise ValueError('Could not connect to hardware')

                    self.log.info('Connected to {}'.format(focuser.serial_number))
                    self.focusers[ut] = focuser
                    if hw_name in self.bad_hardware:
                        self.bad_hardware.remove(hw_name)

                except Exception:
                    self.focusers[ut] = None
                    self.log.debug('', exc_info=True)
                    if hw_name not in self.bad_hardware:
                        self.log.error('Failed to connect to hardware')
                        self.bad_hardware.add(hw_name)

        # Connect to filter wheels
        for ut in self.filterwheels:
            if self.filterwheels[ut] is None and self.filt_params[ut] is not None:
                hw_name = 'filterwheel_{}'.format(ut)
                hw_params = self.filt_params[ut]
                if hw_name not in self.bad_hardware:
                    self.log.info('Connecting to Filter Wheel {}'.format(ut))
                    self.log.debug(hw_params)
                try:
                    if 'CLASS' not in hw_params:
                        raise ValueError('Missing class')
                    hw_class = hw_params['CLASS']

                    # Connect to appropriate hardware class
                    if hw_class == 'FLI':
                        # FLI USB Filter Wheel, needs a serial number
                        if 'SERIAL' not in hw_params:
                            raise ValueError('Missing serial number')
                        if 'PORT' in hw_params:
                            # Deal with unserialized hardware
                            filterwheel = FLIFilterWheel.locate_device(hw_params['PORT'])
                            filterwheel.serial_number = hw_params['SERIAL']
                        else:
                            filterwheel = FLIFilterWheel.locate_device(hw_params['SERIAL'])
                        if filterwheel is None and params.FAKE_FLI:
                            self.log.info('Creating a fake Filter Wheel')
                            filterwheel = FakeFilterWheel('/dev/fake', 'FakeFilterWheel')
                            filterwheel.serial_number = hw_params['SERIAL']
                            filterwheel.connected = True
                        if filterwheel is None:
                            raise ValueError('Could not locate hardware')

                    else:
                        raise ValueError('Unknown class: {}'.format(hw_class))

                    if not filterwheel.connected:
                        raise ValueError('Could not connect to hardware')

                    self.log.info('Connected to {}'.format(filterwheel.serial_number))
                    self.filterwheels[ut] = filterwheel
                    if hw_name in self.bad_hardware:
                        self.bad_hardware.remove(hw_name)

                except Exception:
                    self.filterwheels[ut] = None
                    self.log.debug('', exc_info=True)
                    if hw_name not in self.bad_hardware:
                        self.log.error('Failed to connect to hardware')
                        self.bad_hardware.add(hw_name)

        # Finally check if we need to report an error
        self._check_errors()

    def _get_info(self):
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        temp_info['interface_id'] = self.daemon_id
        temp_info['uts'] = list(self.uts)

        # Get OTA info
        temp_info['ota_uts'] = [ut for ut in self.ota_params if self.ota_params[ut] is not None]
        temp_info['ota_params'] = self.ota_params
        temp_info['ota_serials'] = {ut: self.ota_params[ut]['SERIAL'] for ut in self.ota_params}

        # Get Camera info
        temp_info['cam_uts'] = [ut for ut in self.cam_params if self.cam_params[ut] is not None]
        temp_info['cam_params'] = self.cam_params
        temp_info['cam_serials'] = {}
        for ut in self.cameras:
            # Get info from each camera
            if self.cam_params[ut] is not None:
                try:
                    assert self.cameras[ut].connected
                    temp_info['cam_serials'][ut] = self.cameras[ut].serial_number
                except Exception:
                    self.log.error('Failed to get Camera {} info'.format(ut))
                    self.log.debug('', exc_info=True)
                    temp_info['cam_serials'][ut] = None
                    # Report the connection as failed
                    self.cameras[ut] = None
                    hw_name = 'camera_{}'.format(ut)
                    if hw_name not in self.bad_hardware:
                        self.bad_hardware.add(hw_name)

        # Get Focuser info
        temp_info['foc_uts'] = [ut for ut in self.foc_params if self.foc_params[ut] is not None]
        temp_info['foc_params'] = self.foc_params
        temp_info['foc_serials'] = {}
        for ut in self.focusers:
            # Get info from each focuser
            if self.foc_params[ut] is not None:
                try:
                    assert self.focusers[ut].connected
                    temp_info['foc_serials'][ut] = self.focusers[ut].serial_number
                except Exception:
                    self.log.error('Failed to get Focuser {} info'.format(ut))
                    self.log.debug('', exc_info=True)
                    temp_info['foc_serials'][ut] = None
                    # Report the connection as failed
                    self.focusers[ut] = None
                    hw_name = 'focuser_{}'.format(ut)
                    if hw_name not in self.bad_hardware:
                        self.bad_hardware.add(hw_name)

        # Get Filter Wheel info
        temp_info['filt_uts'] = [ut for ut in self.filt_params if self.filt_params[ut] is not None]
        temp_info['filt_params'] = self.filt_params
        temp_info['filt_serials'] = {}
        for ut in self.filterwheels:
            # Get info from each filterwheel
            if self.filt_params[ut] is not None:
                try:
                    assert self.filterwheels[ut].connected
                    temp_info['filt_serials'][ut] = self.filterwheels[ut].serial_number
                except Exception:
                    self.log.error('Failed to get Filter Wheel {} info'.format(ut))
                    self.log.debug('', exc_info=True)
                    temp_info['filt_serials'][ut] = None
                    # Report the connection as failed
                    self.filterwheels[ut] = None
                    hw_name = 'filterwheel_{}'.format(ut)
                    if hw_name not in self.bad_hardware:
                        self.bad_hardware.add(hw_name)

        # Write debug log line
        # NONE, nothing really changes

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    # OTA control functions
    def get_ota_serial_number(self, ut):
        """Return OTA unique serial number."""
        return self.ota_params[ut]['SERIAL']

    def get_ota_class(self, ut):
        """Return OTA hardware class."""
        return self.ota_params[ut]['CLASS']

    # Focuser control functions
    def move_focuser(self, steps, ut):
        """Move focuser by given number of steps."""
        self.log.info('Focuser {} moving by {}'.format(ut, steps))
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            self.focusers[ut].move_focuser(dev_number, steps, blocking=False)
        elif isinstance(self.focusers[ut], (FakeH400, H400)):
            self.focusers[ut].move_focuser(steps, blocking=False)
        else:
            self.focusers[ut].step_motor(steps, blocking=False)

    def set_focuser(self, position, ut):
        """Move focuser to given position."""
        self.log.info('Focuser {} moving to {}'.format(ut, position))
        if isinstance(self.focusers[ut], (FakeH400, H400)):
            self.focusers[ut].set_focuser(position, blocking=False)
        else:
            raise NotImplementedError("Focuser doesn't have a set function")

    def focuser_can_set(self, ut):
        """Check if the focuser has a set command."""
        if isinstance(self.focusers[ut], (FakeH400, H400)):
            return True
        return False

    def home_focuser(self, ut):
        """Move focuser to the home position."""
        self.log.info('Focuser {} moving to home'.format(ut))
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            self.focusers[ut].home_focuser(dev_number)
        else:
            self.focusers[ut].home_focuser()

    def stop_focuser(self, ut):
        """Stop the focuser from moving."""
        if isinstance(self.focusers[ut], (FLIFocuser, FakeFocuser)):
            raise NotImplementedError("Focuser doesn't have a stop function")
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            return self.focusers[ut].stop_focuser(dev_number)
        else:
            return self.focusers[ut].stop_focuser()

    def focuser_can_stop(self, ut):
        """Check if the focuser has a stop command."""
        if isinstance(self.focusers[ut], (FLIFocuser, FakeFocuser)):
            return False
        return True

    def sync_focuser(self, position, ut):
        """Set the current motor position to the given value."""
        if not isinstance(self.focusers[ut], (FocusLynxHub)):
            raise NotImplementedError("Focuser doesn't have a sync function")
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            return self.focusers[ut].sync_focuser(dev_number, position)
        else:
            return self.focusers[ut].sync_focuser(position)

    def focuser_can_sync(self, ut):
        """Check if the focuser has a sync command."""
        if not isinstance(self.focusers[ut], (FocusLynxHub)):
            return False
        return True

    def get_focuser_limit(self, ut):
        """Return focuser motor limit."""
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            return self.focusers[ut].get_max_extent(dev_number)
        else:
            return self.focusers[ut].max_extent

    def get_focuser_position(self, ut):
        """Return focuser position."""
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            return self.focusers[ut].get_stepper_position(dev_number)
        else:
            return self.focusers[ut].stepper_position

    def get_focuser_status(self, ut):
        """Return focuser status."""
        if isinstance(self.focusers[ut], (FLIFocuser, FakeFocuser)):
            raise NotImplementedError("Focuser doesn't have a status")
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            return self.focusers[ut].get_status(dev_number)
        else:
            return self.focusers[ut].get_status()

    def get_focuser_steps_remaining(self, ut):
        """Return focuser motor limit."""
        if isinstance(self.focusers[ut], (H400, FakeH400)):
            raise NotImplementedError("Focuser doesn't store steps remaining")
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            return self.focusers[ut].get_steps_remaining(dev_number)
        else:
            return self.focusers[ut].get_steps_remaining()

    def get_focuser_temp(self, temp_type, ut):
        """Return focuser internal/external temperature."""
        if isinstance(self.focusers[ut], (H400, FakeH400)):
            raise NotImplementedError("Focuser doesn't have temperature sensors")
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            return self.focusers[ut].read_temperature(dev_number)
        else:
            return self.focusers[ut].read_temperature(temp_type)

    def get_focuser_serial_number(self, ut):
        """Return focuser unique serial number."""
        if isinstance(self.focusers[ut], FocusLynxHub):
            dev_number = self.foc_params[ut]['DEV_NUMBER']
            return self.focusers[ut].get_serial_number(dev_number)
        else:
            return self.focusers[ut].serial_number

    def get_focuser_class(self, ut):
        """Return focuser hardware class."""
        return self.foc_params[ut]['CLASS']

    # Mirror cover control functions (part of the ASA H400 class, under focusers)
    def open_mirror_cover(self, ut):
        """Open the mirror cover."""
        if not isinstance(self.focusers[ut], (H400, FakeH400)):
            raise NotImplementedError('UT {} does not have a mirror cover'.format(ut))
        return self.focusers[ut].open_cover()

    def close_mirror_cover(self, ut):
        """Close the mirror cover."""
        if not isinstance(self.focusers[ut], (H400, FakeH400)):
            raise NotImplementedError('UT {} does not have a mirror cover'.format(ut))
        return self.focusers[ut].close_cover()

    def stop_mirror_cover(self, ut):
        """Stop the mirror cover from moving."""
        if not isinstance(self.focusers[ut], (H400, FakeH400)):
            raise NotImplementedError('UT {} does not have a mirror cover'.format(ut))
        return self.focusers[ut].stop_cover()

    def get_mirror_cover_position(self, ut):
        """Return mirror cover position."""
        if not isinstance(self.focusers[ut], (H400, FakeH400)):
            raise NotImplementedError('UT {} does not have a mirror cover'.format(ut))
        return self.focusers[ut].get_cover_position()

    # Filter wheel control functions
    def set_filter_pos(self, new_filter, ut):
        """Move filter wheel to position."""
        self.log.info('Filter Wheel {} moving to position {}'.format(ut, new_filter))
        pool = ThreadPoolExecutor(max_workers=4)
        pool.submit(self.filterwheels[ut].set_filter_pos, new_filter)

    def home_filter(self, ut):
        """Move filter wheel to home position."""
        self.log.info('Filter Wheel {} moving to home'.format(ut))
        self.filterwheels[ut].home()

    def get_filter_number(self, ut):
        """Return current filter number."""
        return self.filterwheels[ut].get_filter_pos()

    def get_filter_position(self, ut):
        """Return filter wheel position."""
        return self.filterwheels[ut].stepper_position

    def get_filter_steps_remaining(self, ut):
        """Return filter wheel steps remaining."""
        return self.filterwheels[ut].get_steps_remaining()

    def get_filter_homed(self, ut):
        """Return if filter wheel has been homed."""
        return self.filterwheels[ut].homed

    def get_filter_serial_number(self, ut):
        """Return filter wheel unique serial number."""
        return self.filterwheels[ut].serial_number

    def get_filter_class(self, ut):
        """Return filter wheel hardware class."""
        return self.filt_params[ut]['CLASS']

    # Camera control functions
    def set_exposure(self, exptime_ms, frametype, ut):
        """Set exposure time and frametype."""
        expstr = '{}s {} exposure'.format(str(exptime_ms / 1000), frametype)
        self.log.info('Camera {} setting {}'.format(ut, expstr))
        self.cameras[ut].set_exposure(exptime_ms, frametype)

    def start_exposure(self, ut):
        """Begin exposure."""
        self.log.info('Camera {} starting exposure'.format(ut))
        self.cameras[ut].start_exposure()

    def exposure_ready(self, ut):
        """Check if an exposure is ready."""
        return self.cameras[ut].image_ready

    def fetch_exposure(self, ut):
        """Fetch the image."""
        self.log.info('Camera {} fetching image'.format(ut))
        return self.cameras[ut].fetch_image()

    def fetch_exposures(self):
        """Fetch images from all cameras."""
        images = {}
        for ut in self.uts:
            self.log.info('Camera {} fetching image'.format(ut))
            try:
                images[ut] = self.cameras[ut].fetch_image()
            except IndexError:
                images[ut] = None
        return images

    def _write_fits(self, hdu):
        """Write image HDU to a FITS file."""
        ut = hdu.header['UT      ']
        run_number = hdu.header['RUN     ']
        tel_number = hdu.header['TEL     ']
        if not hdu.header['GLANCE  ']:
            filename = image_location(run_number, ut, tel_number)
        else:
            filename = glance_location(ut, tel_number)

        self.log.info('Camera {} saving image to {}'.format(ut, filename))
        save_fits(hdu, filename, log=self.log, confirm=False)
        self.log.info('Camera {} saved image'.format(ut))

    def save_exposure(self, ut, all_info, compress=False, method='proc'):
        """Fetch the image data and save to a FITS file."""
        self.log.info('Camera {} fetching image'.format(ut))
        image_data = self.cameras[ut].fetch_image()
        hdu = make_fits(image_data, ut, all_info, compress, log=self.log)

        if method == 'proc':
            # Start image saving in a new process
            p = mp.Process(target=self._write_fits, args=[hdu])
            p.start()
            p.join()
        elif method == 'thread':
            # Start image saving in a new thread
            t = threading.Thread(target=self._write_fits, args=[hdu])
            t.daemon = True
            t.start()
        else:
            # Just save directly here
            self._write_fits(hdu)

        # return the image header
        return hdu.header

    def abort_exposure(self, ut):
        """Abort current exposure."""
        self.log.info('Camera {} aborting exposure'.format(ut))
        self.cameras[ut].cancel_exposure()

    def clear_exposure_queue(self, ut):
        """Clear exposure queue."""
        self.log.info('Camera {} clearing exposure queue'.format(ut))
        self.cameras[ut].image_queue.clear()

    def set_camera_temp(self, target_temp, ut):
        """Set the camera's temperature."""
        self.log.info('Camera {} setting temperature to {}'.format(ut, target_temp))
        self.cameras[ut].set_temperature(target_temp)

    def set_camera_flushes(self, target_flushes, ut):
        """Set the number of times to flush the CCD before an exposure."""
        self.log.info('Camera {} setting flushes to {}'.format(ut, target_flushes))
        self.cameras[ut].set_flushes(target_flushes)

    def set_camera_binning(self, hbin, vbin, ut):
        """Set the image binning."""
        self.log.info('Camera {} setting binning factor to ({},{})'.format(ut, hbin, vbin))
        self.cameras[ut].set_image_binning(hbin, vbin)

    def set_camera_window(self, x, y, dx, dy, ut):
        """Set the image window area in unbinned pixels."""
        areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
        self.log.info('Camera {} setting image window to {}'.format(ut, areastr))
        self.cameras[ut].set_image_size(x, y, dx, dy)

    def set_camera_window_active(self, ut):
        """Set the image window to the active area (excluding overscan)."""
        x, y, dx, dy = self.get_camera_active_area(ut)
        areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
        self.log.info('Camera {} setting image window to active {}'.format(ut, areastr))
        self.cameras[ut].set_image_size(x, y, dx, dy)

    def set_camera_window_full(self, ut):
        """Set the image window to the full frame (including overscan)."""
        x, y, dx, dy = self.get_camera_full_area(ut)
        areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
        self.log.info('Camera {} setting image window to full {}'.format(ut, areastr))
        self.cameras[ut].set_image_size(x, y, dx, dy)

    def get_camera_info(self, ut):
        """Return camera infomation dictionary."""
        return self.cameras[ut].get_info()

    def get_camera_state(self, ut):
        """Return camera state string."""
        return self.cameras[ut].state

    def get_camera_data_state(self, ut):
        """Return True if data is available."""
        return self.cameras[ut].dataAvailable

    def get_camera_time_remaining(self, ut):
        """Return exposure time remaining."""
        return self.cameras[ut].get_exposure_timeleft() / 1000.

    def get_camera_temp(self, temp_type, ut):
        """Return camera CCD/base temperature."""
        return self.cameras[ut].get_temperature(temp_type)

    def get_camera_cooler_power(self, ut):
        """Return peltier cooler power."""
        return self.cameras[ut].get_cooler_power()

    def get_camera_image_size(self, ut):
        """Return the image size in binned pixels."""
        return self.cameras[ut].get_image_size()

    def get_camera_window(self, ut):
        """Return the current image area in unbinned pixels."""
        info = self.cameras[ut].get_info()['readout_pars']
        x = info['xstart']
        y = info['ystart']
        dx = info['nx'] * info['xbin']
        dy = info['ny'] * info['ybin']
        return (x, y, dx, dy)

    def get_camera_active_area(self, ut):
        """Return the active image area (excluding overscan) in unbinned pixels."""
        info = self.cameras[ut].get_info()['active_area']
        x = info[0]
        y = info[1]
        dx = info[2] - info[0]
        dy = info[3] - info[1]
        return (x, y, dx, dy)

    def get_camera_full_area(self, ut):
        """Return the full frame image area (including overscan) in unbinned pixels."""
        info = self.cameras[ut].get_info()['array_area']
        x = info[0]
        y = info[1]
        dx = info[2] - info[0]
        dy = info[3] - info[1]
        return (x, y, dx, dy)

    def get_camera_serial_number(self, ut):
        """Return camera unique serial number."""
        return self.cameras[ut].serial_number

    def get_camera_class(self, ut):
        """Return camera hardware class."""
        return self.cam_params[ut]['CLASS']


def parse_args():
    """Parse arguments.

    See also `get_args()` in the intf script.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument('interface_id')
    parser.add_argument('--uts', nargs='+', action='append')
    args = parser.parse_args()

    # Interface ID should be first argument
    interface_id = args.interface_id

    # Each UT should have a number, a name, and details of any attached hardware
    ut_lists = args.uts
    hw_dict = {}
    for ut_list in ut_lists:
        ut_dict = {}

        # ID number should be first
        ut = int(ut_list[0])

        # Then should be the OTA details
        ut_dict['OTA'] = json.loads(ut_list[1].strip('ota='))

        # Then should be arguments with JSON dictionaries
        for arg in ut_list[2:]:
            if arg.startswith('cam='):
                ut_dict['CAMERA'] = json.loads(arg.strip('cam='))
            if arg.startswith('foc='):
                ut_dict['FOCUSER'] = json.loads(arg.strip('foc='))
            if arg.startswith('filt='):
                ut_dict['FILTERWHEEL'] = json.loads(arg.strip('filt='))

        # Add `None`s for missing HW
        if 'CAMERA' not in ut_dict:
            ut_dict['CAMERA'] = None
        if 'FOCUSER' not in ut_dict:
            ut_dict['FOCUSER'] = None
        if 'FILTERWHEEL' not in ut_dict:
            ut_dict['FILTERWHEEL'] = None

        # Add to main dict
        hw_dict[ut] = ut_dict
    return interface_id, hw_dict


if __name__ == '__main__':
    interface_id, hw_dict = parse_args()
    with make_pid_file(interface_id):
        UTInterfaceDaemon(interface_id, hw_dict)._run()
