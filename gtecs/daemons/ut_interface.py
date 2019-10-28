#!/usr/bin/env python
"""Interface to access hardware connected to the UTs (cameras, focusers, filter wheels)."""

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon, daemon_is_running
from gtecs.hardware.fli import Camera, FilterWheel, Focuser
from gtecs.hardware.fli import FakeCamera, FakeFilterWheel, FakeFocuser


class UTInterfaceDaemon(BaseDaemon):
    """UT interface daemon class."""

    def __init__(self, interface_id, serial_dict):
        super().__init__(interface_id)

        # hardware
        self.serial_dict = serial_dict
        self.uts = serial_dict.keys()

        self.cameras = {ut: None for ut in self.uts}
        self.cam_targets = {ut: self.serial_dict[ut]['cam'] for ut in self.uts}

        self.focusers = {ut: None for ut in self.uts}
        self.foc_targets = {ut: self.serial_dict[ut]['foc'] for ut in self.uts}

        self.filterwheels = {ut: None for ut in self.uts}
        self.filt_targets = {ut: self.serial_dict[ut]['filt'] for ut in self.uts}

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
            if not self.cameras[ut] and self.cam_targets[ut]:
                hw_name = 'camera_{}'.format(ut)
                target = self.cam_targets[ut]
                try:
                    self.log.info('Connecting to Camera {} ({})'.format(ut, target))
                    camera = Camera.locate_device(target)
                    if camera is None and params.FAKE_FLI:
                        self.log.info('Creating a fake Camera {}'.format(ut))
                        camera = FakeCamera('fake', 'FakeCamera')
                    if camera is not None:
                        self.cameras[ut] = camera
                        serial = camera.serial_number
                        self.log.info('Connected to Camera {} ({})'.format(ut, serial))
                        if hw_name in self.bad_hardware:
                            self.bad_hardware.remove(hw_name)
                    else:
                        raise Exception('Connection failed')
                except Exception:
                    self.cameras[ut] = None
                    self.log.error('Failed to connect to Camera {} ({})'.format(ut, target))
                    if hw_name not in self.bad_hardware:
                        self.bad_hardware.add(hw_name)

        # Connect to focusers
        for ut in self.focusers:
            if not self.focusers[ut] and self.foc_targets[ut]:
                target = self.foc_targets[ut]
                hw_name = 'focuser_{}'.format(ut)
                try:
                    self.log.info('Connecting to Focuser {} ({})'.format(ut, target))
                    focuser = Focuser.locate_device(target)
                    if focuser is None and params.FAKE_FLI:
                        self.log.info('Creating a fake Focuser {}'.format(ut))
                        focuser = FakeFocuser('fake', 'FakeFocuser')
                    if focuser is not None:
                        self.focusers[ut] = focuser
                        serial = focuser.serial_number
                        self.log.info('Connected to Focuser {} ({})'.format(ut, serial))
                        if hw_name in self.bad_hardware:
                            self.bad_hardware.remove(hw_name)
                    else:
                        raise Exception('Connection failed')
                except Exception:
                    self.focusers[ut] = None
                    self.log.error('Failed to connect to Focuser {} ({})'.format(ut, target))
                    if hw_name not in self.bad_hardware:
                        self.bad_hardware.add(hw_name)

        # Connect to filter wheels
        for ut in self.filterwheels:
            if not self.filterwheels[ut] and self.filt_targets[ut]:
                target = self.filt_targets[ut]
                hw_name = 'filterwheel_{}'.format(ut)
                try:
                    self.log.info('Connecting to Filter Wheel {} ({})'.format(ut, target))
                    filterwheel = FilterWheel.locate_device(target)
                    if filterwheel is None and params.FAKE_FLI:
                        self.log.info('Creating a fake Filter Wheel {}'.format(ut))
                        filterwheel = FakeFilterWheel('fake', 'FakeFilterWheel')
                    if filterwheel is not None:
                        self.filterwheels[ut] = filterwheel
                        serial = filterwheel.serial_number
                        self.log.info('Connected to Filter Wheel {} ({})'.format(ut, serial))
                        if hw_name in self.bad_hardware:
                            self.bad_hardware.remove(hw_name)
                    else:
                        raise Exception('Connection failed')
                except Exception:
                    self.filterwheels[ut] = None
                    self.log.error('Failed to connect to Filter Wheel {} ({})'.format(ut, target))
                    if hw_name not in self.bad_hardware:
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

        temp_info['cam_targets'] = self.cam_targets
        temp_info['cam_serials'] = {}
        for ut in self.cameras:
            # Get info from each camera
            if self.cam_targets[ut]:
                try:
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

        temp_info['foc_targets'] = self.foc_targets
        temp_info['foc_serials'] = {}
        for ut in self.focusers:
            # Get info from each focuser
            if self.foc_targets[ut]:
                try:
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

        temp_info['filt_targets'] = self.filt_targets
        temp_info['filt_serials'] = {}
        for ut in self.filterwheels:
            # Get info from each filterwheel
            if self.filt_targets[ut]:
                try:
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

    def get_focuser_steps_remaining(self, ut):
        """Return focuser motor limit."""
        return self.focusers[ut].get_steps_remaining()

    def get_focuser_temp(self, temp_type, ut):
        """Return focuser internal/external temperature."""
        return self.focusers[ut].read_temperature(temp_type)

    def get_focuser_serial_number(self, ut):
        """Return focuser unique serial number."""
        return self.focusers[ut].serial_number

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


def parse_args():
    """Parse arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--uts', nargs='+', action='append')
    args = parser.parse_args()
    ut_dicts = args.uts
    serial_dict = {}
    for ut_dict in ut_dicts:
        if len(ut_dict) == 4:
            # Should be four strings: UT number, cam serial, foc serial, filt serial
            ut, cam, foc, filt = ut_dict
            serial_dict[int(ut)] = {'cam': cam, 'foc': foc, 'filt': filt}
        else:
            # This UT has no filter wheel
            ut, cam, foc = ut_dict
            serial_dict[int(ut)] = {'cam': cam, 'foc': foc, 'filt': None}

    return serial_dict


def find_interface_id(hostname):
    """Find what interface should be running on a given host.

    Used to find which interface each should identify as.

    """
    interfaces = []
    for interface_id in params.INTERFACES:
        if params.DAEMONS[interface_id]['HOST'] == hostname:
            interfaces.append(interface_id)
    if len(interfaces) == 0:
        raise ValueError('Host {} does not have an associated interface'.format(hostname))
    elif len(interfaces) == 1:
        return interfaces[0]
    else:
        # return the first one that's not running
        for interface_id in sorted(interfaces):
            if not daemon_is_running(interface_id):
                return interface_id
        raise ValueError('All defined interfaces on {} are running'.format(hostname))


if __name__ == '__main__':
    serial_dict = parse_args()
    interface_id = find_interface_id(params.LOCAL_HOST)
    with misc.make_pid_file(interface_id):
        UTInterfaceDaemon(interface_id, serial_dict)._run()
