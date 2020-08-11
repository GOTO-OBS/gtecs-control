#!/usr/bin/env python3
"""Interface to access hardware connected to the UTs (cameras, focusers, filter wheels)."""

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon
from gtecs.hardware.asa import FakeH400, H400
from gtecs.hardware.fli import FLICamera, FLIFilterWheel, FLIFocuser
from gtecs.hardware.fli import FakeCamera, FakeFilterWheel, FakeFocuser
from gtecs.hardware.rasa import FocusLynx


class UTInterfaceDaemon(BaseDaemon):
    """UT interface daemon class."""

    def __init__(self, interface_id, hw_dict):
        super().__init__(interface_id)

        # hardware
        self.hw_dict = hw_dict
        self.uts = hw_dict.keys()

        self.otas = {ut: self.hw_dict[ut]['OTA'] for ut in self.uts}

        self.cameras = {ut: None for ut in self.uts}
        self.cam_params = {ut: self.hw_dict[ut]['CAMERA'] for ut in self.uts}

        self.focusers = {ut: None for ut in self.uts}
        self.foc_params = {ut: self.hw_dict[ut]['FOCUSER'] for ut in self.uts}

        self.filterwheels = {ut: None for ut in self.uts}
        self.filt_params = {ut: self.hw_dict[ut]['FILTERWHEEL'] for ut in self.uts}

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
                        if camera is None:
                            raise ValueError('Could not locate hardware')

                    else:
                        raise ValueError('Unknown class: {}'.format(hw_class))

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
                        if focuser is None:
                            raise ValueError('Could not locate hardware')

                    elif hw_class == 'RASA':
                        # RASA in-built FocusLynx Focuser, needs a port and a serial number
                        if 'PORT' not in hw_params:
                            raise ValueError('Missing serial port')
                        if 'SERIAL' not in hw_params:
                            raise ValueError('Missing serial number')
                        focuser = FocusLynx.locate_device(hw_params['PORT'], hw_params['SERIAL'])
                        if focuser is None:
                            raise ValueError('Could not locate hardware')

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
                        if filterwheel is None:
                            raise ValueError('Could not locate hardware')

                    else:
                        raise ValueError('Unknown class: {}'.format(hw_class))

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
        """Get the latest status info from the heardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        temp_info['interface_id'] = self.daemon_id

        temp_info['cam_params'] = self.cam_params
        temp_info['cam_serials'] = {}
        for ut in self.cameras:
            # Get info from each camera
            if self.cam_params[ut] is not None:
                try:
                    #assert self.cameras[ut].connected
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

        temp_info['foc_params'] = self.foc_params
        temp_info['foc_serials'] = {}
        for ut in self.focusers:
            # Get info from each focuser
            if self.foc_params[ut] is not None:
                try:
                    #assert self.focusers[ut].connected
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

        temp_info['filt_params'] = self.filt_params
        temp_info['filt_serials'] = {}
        for ut in self.filterwheels:
            # Get info from each filterwheel
            if self.filt_params[ut] is not None:
                try:
                    #assert self.filterwheels[ut].connected
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

        # Get other internal info
        temp_info['uts'] = list(self.uts)
        temp_info['ota_serials'] = dict(self.otas)

        # Write debug log line
        # NONE, nothing really changes

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    # Focuser control functions
    def step_focuser_motor(self, steps, ut):
        """Move focuser by given number of steps."""
        self.log.info('Focuser {} moving by {}'.format(ut, steps))
        self.focusers[ut].step_motor(steps, blocking=False)

    def home_focuser(self, ut):
        """Move focuser to the home position."""
        self.log.info('Focuser {} moving to home'.format(ut))
        self.focusers[ut].home_focuser()

    def get_focuser_limit(self, ut):
        """Return focuser motor limit."""
        return self.focusers[ut].max_extent

    def get_focuser_position(self, ut):
        """Return focuser position."""
        return self.focusers[ut].stepper_position

    def get_focuser_status(self, ut):
        """Return focuser status."""
        if isinstance(self.focusers[ut], (FLIFocuser, FakeFocuser)):
            raise NotImplementedError("FLI focusers don't have a status")
        return self.focusers[ut].get_status()

    def get_focuser_steps_remaining(self, ut):
        """Return focuser motor limit."""
        if isinstance(self.focusers[ut], (H400, FakeH400)):
            raise NotImplementedError("ASA H400s don't store steps remaining")
        return self.focusers[ut].get_steps_remaining()

    def get_focuser_temp(self, temp_type, ut):
        """Return focuser internal/external temperature."""
        if isinstance(self.focusers[ut], (H400, FakeH400)):
            raise NotImplementedError("ASA H400s don't have temperature sensors")
        return self.focusers[ut].read_temperature(temp_type)

    def get_focuser_serial_number(self, ut):
        """Return focuser unique serial number."""
        return self.focusers[ut].serial_number

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

    def set_camera_area(self, ul_x, ul_y, lr_x, lr_y, ut):
        """Set the active image area."""
        areastr = '({},{},{},{})'.format(ul_x, ul_y, lr_x, lr_y)
        self.log.info('Camera {} setting active area to {}'.format(ut, areastr))
        self.cameras[ut].set_image_size(ul_x, ul_y, lr_x, lr_y)

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

    def get_camera_serial_number(self, ut):
        """Return camera unique serial number."""
        return self.cameras[ut].serial_number

    # OTA functions
    def get_ota_serial_number(self, ut):
        """Return OTA unique serial number."""
        return self.otas[ut]


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

        # Then should be the assigned OTA serial number
        serial = str(ut_list[1].strip('ota='))
        ut_dict['OTA'] = serial

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
    with misc.make_pid_file(interface_id):
        UTInterfaceDaemon(interface_id, hw_dict)._run()
