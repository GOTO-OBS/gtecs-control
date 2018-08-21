#!/usr/bin/env python
"""Interface to access FLI hardware."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon, daemon_is_running
from gtecs.hardware.fli import Camera, FilterWheel, Focuser
from gtecs.hardware.fli import FakeCamera, FakeFilterWheel, FakeFocuser


class FLIDaemon(BaseDaemon):
    """FLI interface daemon class."""

    def __init__(self, daemon_id):
        super().__init__(daemon_id)

        # hardware
        self.tels = range(len(params.FLI_INTERFACES[self.daemon_id]['TELS']))
        self.cameras = {hw: None for hw in self.tels}
        self.focusers = {hw: None for hw in self.tels}
        self.filterwheels = {hw: None for hw in self.tels}

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
        for hw in self.cameras:
            if not self.cameras[hw]:
                hw_name = 'camera_' + str(hw)
                try:
                    serial = params.FLI_INTERFACES[self.daemon_id]['SERIALS']['cam'][hw]
                    camera = Camera.locate_device(serial)
                    if camera is None and params.USE_FAKE_FLI:
                        camera = FakeCamera('fake', 'FakeCamera')
                    if camera is not None:
                        self.cameras[hw] = camera
                        self.log.info('Connected to Camera {} ("{}")'.format(hw, serial))
                        if hw_name in self.bad_hardware:
                            self.bad_hardware.remove(hw_name)
                    else:
                        raise Exception('Camera not found')
                except Exception:
                    self.cameras[hw] = None
                    self.log.error('Failed to connect to Camera {} ("{}")'.format(hw, serial))
                    if hw_name not in self.bad_hardware:
                        self.bad_hardware.add(hw_name)

        # Connect to focusers
        for hw in self.focusers:
            if not self.focusers[hw]:
                hw_name = 'focuser_' + str(hw)
                try:
                    serial = params.FLI_INTERFACES[self.daemon_id]['SERIALS']['foc'][hw]
                    focuser = Focuser.locate_device(serial)
                    if focuser is None and params.USE_FAKE_FLI:
                        focuser = FakeFocuser('fake', 'FakeFocuser')
                    if focuser is not None:
                        self.focusers[hw] = focuser
                        self.log.info('Connected to Focuser {} ("{}")'.format(hw, serial))
                        if hw_name in self.bad_hardware:
                            self.bad_hardware.remove(hw_name)
                    else:
                        raise Exception('Focuser not found')
                except Exception:
                    self.focusers[hw] = None
                    self.log.error('Failed to connect to Focuser {} ("{}")'.format(hw, serial))
                    if hw_name not in self.bad_hardware:
                        self.bad_hardware.add(hw_name)

        # Connect to filter wheels
        for hw in self.filterwheels:
            if not self.filterwheels[hw]:
                hw_name = 'filterwheel_' + str(hw)
                try:
                    serial = params.FLI_INTERFACES[self.daemon_id]['SERIALS']['foc'][hw]
                    filterwheel = FilterWheel.locate_device(serial)
                    if filterwheel is None and params.USE_FAKE_FLI:
                        filterwheel = FakeFilterWheel('fake', 'FakeFilterWheel')
                    if filterwheel is not None:
                        self.filterwheels[hw] = filterwheel
                        self.log.info('Connected to Filter Wheel {} ("{}")'.format(hw, serial))
                        if hw_name in self.bad_hardware:
                            self.bad_hardware.remove(hw_name)
                    else:
                        raise Exception('Filter Wheel not found')
                except Exception:
                    self.filterwheels[hw] = None
                    self.log.error('Failed to connect to Filter Wheel {} ("{}")'.format(hw, serial))
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

        for hw in self.cameras:
            # Get info from each camera
            hw_name = 'camera_' + str(hw)
            try:
                camera = self.cameras[hw]
                temp_info[hw_name] = camera.serial_number
            except Exception:
                self.log.error('Failed to get Camera {:.0f} info'.format(hw))
                self.log.debug('', exc_info=True)
                temp_info[hw_name] = None
                # Report the connection as failed
                self.cameras[hw] = None
                if hw_name not in self.bad_hardware:
                    self.bad_hardware.add(hw_name)

        for hw in self.focusers:
            # Get info from each focuser
            hw_name = 'focuser_' + str(hw)
            try:
                focuser = self.focusers[hw]
                temp_info[hw_name] = focuser.serial_number
            except Exception:
                self.log.error('Failed to get Focuser {:.0f} info'.format(hw))
                self.log.debug('', exc_info=True)
                temp_info[hw_name] = None
                # Report the connection as failed
                self.focusers[hw] = None
                if hw_name not in self.bad_hardware:
                    self.bad_hardware.add(hw_name)

        for hw in self.filterwheels:
            # Get info from each filterwheel
            hw_name = 'filterwheel_' + str(hw)
            try:
                filterwheel = self.filterwheels[hw]
                temp_info[hw_name] = filterwheel.serial_number
            except Exception:
                self.log.error('Failed to get Filter Wheel {:.0f} info'.format(hw))
                self.log.debug('', exc_info=True)
                temp_info[hw_name] = None
                # Report the connection as failed
                self.filterwheels[hw] = None
                if hw_name not in self.bad_hardware:
                    self.bad_hardware.add(hw_name)

        # Get other internal info
        temp_info['tels'] = list(self.tels)

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    # Focuser control functions
    def step_focuser_motor(self, steps, hw):
        """Move focuser by given number of steps."""
        self.log.info('Moving Focuser {} by {}'.format(hw, steps))
        self.focusers[int(hw)].step_motor(steps, blocking=False)

    def home_focuser(self, hw):
        """Move focuser to the home position."""
        self.log.info('Homing Focuser {}'.format(hw))
        self.focusers[int(hw)].home_focuser()

    def get_focuser_limit(self, hw):
        """Return focuser motor limit."""
        return self.focusers[int(hw)].max_extent

    def get_focuser_position(self, hw):
        """Return focuser position."""
        return self.focusers[int(hw)].stepper_position

    def get_focuser_steps_remaining(self, hw):
        """Return focuser motor limit."""
        return self.focusers[int(hw)].get_steps_remaining()

    def get_focuser_temp(self, temp_type, hw):
        """Return focuser internal/external temperature."""
        return self.focusers[int(hw)].read_temperature(temp_type)

    def get_focuser_serial_number(self, hw):
        """Return focuser unique serial number."""
        return self.focusers[int(hw)].serial_number

    # Filter wheel control functions
    def set_filter_pos(self, new_filter, hw):
        """Move filter wheel to position."""
        self.log.info('Moving filter wheel {} to position {}'.format(hw, new_filter))
        pool = ThreadPoolExecutor(max_workers=4)
        pool.submit(self.filterwheels[int(hw)].set_filter_pos, new_filter)

    def home_filter(self, hw):
        """Move filter wheel to home position."""
        self.log.info('Homing filter wheel {}'.format(hw))
        self.filterwheels[int(hw)].home()

    def get_filter_number(self, hw):
        """Return current filter number."""
        return self.filterwheels[int(hw)].get_filter_pos()

    def get_filter_position(self, hw):
        """Return filter wheel position."""
        return self.filterwheels[int(hw)].stepper_position

    def get_filter_steps_remaining(self, hw):
        """Return filter wheel steps remaining."""
        return self.filterwheels[int(hw)].get_steps_remaining()

    def get_filter_homed(self, hw):
        """Return if filter wheel has been homed."""
        return self.filterwheels[int(hw)].homed

    def get_filter_serial_number(self, hw):
        """Return filter wheel unique serial number."""
        return self.filterwheels[int(hw)].serial_number

    # Camera control functions
    def set_exposure(self, exptime_ms, frametype, hw):
        """Set exposure time and frametype."""
        expstr = '{}s {} exposure'.format(str(exptime_ms / 1000), frametype)
        self.log.info('Camera {} setting {}'.format(hw, expstr))
        self.cameras[int(hw)].set_exposure(exptime_ms, frametype)

    def start_exposure(self, hw):
        """Begin exposure."""
        self.log.info('Camera {} starting exposure'.format(hw))
        self.cameras[int(hw)].start_exposure()

    def exposure_ready(self, hw):
        """Check if an exposure is ready."""
        return self.cameras[int(hw)].image_ready

    def fetch_exposure(self, hw):
        """Fetch the image."""
        self.log.info('Camera {} fetching image'.format(hw))
        return self.cameras[int(hw)].fetch_image()

    def abort_exposure(self, hw):
        """Abort current exposure."""
        self.log.info('Camera {} aborting exposure'.format(hw))
        self.cameras[int(hw)].cancel_exposure()

    def clear_exposure_queue(self, hw):
        """Clear exposure queue."""
        self.log.info('Camera {} clearing exposure queue'.format(hw))
        self.cameras[int(hw)].image_queue.clear()

    def set_camera_temp(self, target_temp, hw):
        """Set the camera's temperature."""
        self.log.info('Camera {} setting temperature to {}'.format(hw, target_temp))
        self.cameras[int(hw)].set_temperature(target_temp)

    def set_camera_flushes(self, target_flushes, hw):
        """Set the number of times to flush the CCD before an exposure."""
        self.log.info('Camera {} setting flushes to {}'.format(hw, target_flushes))
        self.cameras[int(hw)].set_flushes(target_flushes)

    def set_camera_binning(self, hbin, vbin, hw):
        """Set the image binning."""
        self.log.info('Camera {} setting binning factor to ({},{})'.format(hw, hbin, vbin))
        self.cameras[int(hw)].set_image_binning(hbin, vbin)

    def set_camera_area(self, ul_x, ul_y, lr_x, lr_y, hw):
        """Set the active image area."""
        areastr = '({},{},{},{})'.format(ul_x, ul_y, lr_x, lr_y)
        self.log.info('Camera {} setting active area to {}'.format(hw, areastr))
        self.cameras[int(hw)].set_image_size(ul_x, ul_y, lr_x, lr_y)

    def get_camera_info(self, hw):
        """Return camera infomation dictionary."""
        return self.cameras[int(hw)].get_info()

    def get_camera_state(self, hw):
        """Return camera state string."""
        return self.cameras[int(hw)].state

    def get_camera_data_state(self, hw):
        """Return True if data is available."""
        return self.cameras[int(hw)].dataAvailable

    def get_camera_time_remaining(self, hw):
        """Return exposure time remaining."""
        return self.cameras[int(hw)].get_exposure_timeleft() / 1000.

    def get_camera_temp(self, temp_type, hw):
        """Return camera CCD/base temperature."""
        return self.cameras[int(hw)].get_temperature(temp_type)

    def get_camera_cooler_power(self, hw):
        """Return peltier cooler power."""
        return self.cameras[int(hw)].get_cooler_power()

    def get_camera_serial_number(self, hw):
        """Return camera unique serial number."""
        return self.cameras[int(hw)].serial_number


def find_interface_id(hostname):
    """Find what interface should be running on a given host.

    Used by the FLI interfaces to find which interface it should identify as.

    """
    intfs = []
    for intf in params.FLI_INTERFACES:
        if params.DAEMONS[intf]['HOST'] == hostname:
            intfs.append(intf)
    if len(intfs) == 0:
        raise ValueError('Host {} does not have an associated interface'.format(hostname))
    elif len(intfs) == 1:
        return intfs[0]
    else:
        # return the first one that's not running
        for intf in sorted(intfs):
            if not daemon_is_running(intf):
                return intf
        raise ValueError('All defined interfaces on {} are running'.format(hostname))


if __name__ == "__main__":
    daemon_id = find_interface_id(params.LOCAL_HOST)
    with misc.make_pid_file(daemon_id):
        FLIDaemon(daemon_id)._run()
