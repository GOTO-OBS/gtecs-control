#!/usr/bin/env python
"""Interface to access FLI hardware."""

from concurrent.futures import ThreadPoolExecutor

from gtecs import misc
from gtecs import params
from gtecs.daemons import HardwareDaemon, daemon_is_running
from gtecs.hardware.fli import Camera, FilterWheel, Focuser
from gtecs.hardware.fli import FakeCamera, FakeFilterWheel, FakeFocuser


class FLIDaemon(HardwareDaemon):
    """FLI interface daemon class."""

    def __init__(self, intf):
        super().__init__(intf)
        self.intf = intf

        self._connect()

    # Connect to hardware
    def _connect(self):
        self.cams = []
        self.focs = []
        self.filts = []
        missing_hardware = []
        for hw in range(len(params.FLI_INTERFACES[self.intf]['TELS'])):
            # cameras
            serial = params.FLI_INTERFACES[self.intf]['SERIALS']['cam'][hw]
            cam = Camera.locate_device(serial)
            if cam is None and params.USE_FAKE_FLI:
                cam = FakeCamera('fake', 'Fake-Cam')
            if cam is not None:
                self.cams.append(cam)
                self.log.info('Connected to Camera {}: serial "{}"'.format(hw, serial))
            else:
                missing_hardware.append('Camera {}: {}'.format(hw, serial))

            # focusers
            serial = params.FLI_INTERFACES[self.intf]['SERIALS']['foc'][hw]
            foc = Focuser.locate_device(serial)
            if foc is None and params.USE_FAKE_FLI:
                foc = FakeFocuser('fake', 'Fake-Foc')
            if foc is not None:
                self.focs.append(foc)
                self.log.info('Connected to Focuser {}: serial "{}"'.format(hw, serial))
            else:
                missing_hardware.append('Focuser {}: {}'.format(hw, serial))

            # filter wheels
            serial = params.FLI_INTERFACES[self.intf]['SERIALS']['filt'][hw]
            filt = FilterWheel.locate_device(serial)
            if filt is None and params.USE_FAKE_FLI:
                filt = FakeFilterWheel('fake', 'Fake-Filt')
            if filt is not None:
                self.filts.append(filt)
                self.log.info('Connected to Filter Wheel {}: serial "{}"'.format(hw, serial))
            else:
                missing_hardware.append('Filter Wheel {}: {}'.format(hw, serial))

        if len(missing_hardware) > 0:
            # can't run if the hardware isn't found (and we won't make fake ones)
            self.log.error('FLI hardware not found: {!r}'.format(missing_hardware))
            self.shutdown()

    # Focuser control functions
    def step_focuser_motor(self, steps, hw):
        """Move focuser by given number of steps."""
        self.log.info('Moving Focuser {} by {}'.format(hw, steps))
        self.focs[int(hw)].step_motor(steps, blocking=False)

    def home_focuser(self, hw):
        """Move focuser to the home position."""
        self.log.info('Homing Focuser {}'.format(hw))
        self.focs[int(hw)].home_focuser()

    def get_focuser_limit(self, hw):
        """Return focuser motor limit."""
        return self.focs[int(hw)].max_extent

    def get_focuser_position(self, hw):
        """Return focuser position."""
        return self.focs[int(hw)].stepper_position

    def get_focuser_steps_remaining(self, hw):
        """Return focuser motor limit."""
        return self.focs[int(hw)].get_steps_remaining()

    def get_focuser_temp(self, temp_type, hw):
        """Return focuser internal/external temperature."""
        return self.focs[int(hw)].read_temperature(temp_type)

    def get_focuser_serial_number(self, hw):
        """Return focuser unique serial number."""
        return self.focs[int(hw)].serial_number

    # Filter wheel control functions
    def set_filter_pos(self, new_filter, hw):
        """Move filter wheel to position."""
        self.log.info('Moving filter wheel {} to position {}'.format(hw, new_filter))
        pool = ThreadPoolExecutor(max_workers=4)
        pool.submit(self.filts[int(hw)].set_filter_pos, new_filter)

    def home_filter(self, hw):
        """Move filter wheel to home position."""
        self.log.info('Homing filter wheel {}'.format(hw))
        self.filts[int(hw)].home()

    def get_filter_number(self, hw):
        """Return current filter number."""
        return self.filts[int(hw)].get_filter_pos()

    def get_filter_position(self, hw):
        """Return filter wheel position."""
        return self.filts[int(hw)].stepper_position

    def get_filter_steps_remaining(self, hw):
        """Return filter wheel steps remaining."""
        return self.filts[int(hw)].get_steps_remaining()

    def get_filter_homed(self, hw):
        """Return if filter wheel has been homed."""
        return self.filts[int(hw)].homed

    def get_filter_serial_number(self, hw):
        """Return filter wheel unique serial number."""
        return self.filts[int(hw)].serial_number

    # Camera control functions
    def set_exposure(self, exptime_ms, frametype, hw):
        """Set exposure time and frametype."""
        expstr = '{}s {} exposure'.format(str(exptime_ms / 1000), frametype)
        self.log.info('Camera {} setting {}'.format(hw, expstr))
        self.cams[int(hw)].set_exposure(exptime_ms, frametype)

    def start_exposure(self, hw):
        """Begin exposure."""
        self.log.info('Camera {} starting exposure'.format(hw))
        self.cams[int(hw)].start_exposure()

    def exposure_ready(self, hw):
        """Check if an exposure is ready."""
        return self.cams[int(hw)].image_ready

    def fetch_exposure(self, hw):
        """Fetch the image."""
        self.log.info('Camera {} fetching image'.format(hw))
        return self.cams[int(hw)].fetch_image()

    def abort_exposure(self, hw):
        """Abort current exposure."""
        self.log.info('Camera {} aborting exposure'.format(hw))
        self.cams[int(hw)].cancel_exposure()

    def clear_exposure_queue(self, hw):
        """Clear exposure queue."""
        self.log.info('Camera {} clearing exposure queue'.format(hw))
        self.cams[int(hw)].image_queue.clear()

    def set_camera_temp(self, target_temp, hw):
        """Set the camera's temperature."""
        self.log.info('Camera {} setting temperature to {}'.format(hw, target_temp))
        self.cams[int(hw)].set_temperature(target_temp)

    def set_camera_flushes(self, target_flushes, hw):
        """Set the number of times to flush the CCD before an exposure."""
        self.log.info('Camera {} setting flushes to {}'.format(hw, target_flushes))
        self.cams[int(hw)].set_flushes(target_flushes)

    def set_camera_binning(self, hbin, vbin, hw):
        """Set the image binning."""
        self.log.info('Camera {} setting binning factor to ({},{})'.format(hw, hbin, vbin))
        self.cams[int(hw)].set_image_binning(hbin, vbin)

    def set_camera_area(self, ul_x, ul_y, lr_x, lr_y, hw):
        """Set the active image area."""
        areastr = '({},{},{},{})'.format(ul_x, ul_y, lr_x, lr_y)
        self.log.info('Camera {} setting active area to {}'.format(hw, areastr))
        self.cams[int(hw)].set_image_size(ul_x, ul_y, lr_x, lr_y)

    def get_camera_info(self, hw):
        """Return camera infomation dictionary."""
        return self.cams[int(hw)].get_info()

    def get_camera_state(self, hw):
        """Return camera state string."""
        return self.cams[int(hw)].state

    def get_camera_data_state(self, hw):
        """Return True if data is available."""
        return self.cams[int(hw)].dataAvailable

    def get_camera_time_remaining(self, hw):
        """Return exposure time remaining."""
        return self.cams[int(hw)].get_exposure_timeleft() / 1000.

    def get_camera_temp(self, temp_type, hw):
        """Return camera CCD/base temperature."""
        return self.cams[int(hw)].get_temperature(temp_type)

    def get_camera_cooler_power(self, hw):
        """Return peltier cooler power."""
        return self.cams[int(hw)].get_cooler_power()

    def get_camera_serial_number(self, hw):
        """Return camera unique serial number."""
        return self.cams[int(hw)].serial_number


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
