#!/usr/bin/env python3
"""Interface to access hardware connected to the UTs (cameras, focusers, filter wheels)."""

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, get_daemon_host
from gtecs.control.hardware.fli import FLIFilterWheel, FakeFilterWheel


class FiltInterfaceDaemon(BaseDaemon):
    """Filter wheel interface daemon class."""

    def __init__(self, ut):
        super().__init__(f'filt{ut}')

        # hardware
        self.ut = ut
        self.filterwheel = None
        self.params = params.UT_DICT[ut]['FILTERWHEEL']
        self.serial = self.params['SERIAL']
        if 'PORT' in self.params:
            self.port = self.params['PORT']
        else:
            self.port = None

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

                # Try to connect to the hardware
                self._connect()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's successful
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

    # Internal functions
    def _connect(self):
        """Connect to hardware.

        If the connection fails the hardware will be added to the bad_hardware list,
        which will trigger a hardware_error.
        """
        if self.filterwheel is not None:
            # Already connected
            return

        if params.FAKE_INTF:
            self.log.info('Creating Filter Wheel simulator')
            self.filterwheel = FakeFilterWheel('/dev/fake', 'FakeFilterWheel')
            self.filterwheel.serial_number = self.serial
            self.filterwheel.connected = True
            return

        try:
            self.log.info('Connecting to Filter Wheel')
            if self.port is None:
                self.filterwheel = FLIFilterWheel.locate_device(self.serial)
            else:
                # Workaround for non-serialised hardware, use the UDEV port
                self.filterwheel = FLIFilterWheel.locate_device(self.port)
                self.filterwheel.serial_number = self.serial

            # Check if it's connected
            if self.filterwheel is None:
                raise ValueError('Could not locate hardware')
            if not self.filterwheel.connected:
                raise ValueError('Could not connect to hardware')

            # Connection successful
            self.log.info('Connected to {}'.format(self.filterwheel.serial_number))
            if 'filterwheel' in self.bad_hardware:
                self.bad_hardware.remove('filterwheel')

        except Exception:
            # Connection failed
            self.filterwheel = None
            self.log.debug('', exc_info=True)
            if 'filterwheel' not in self.bad_hardware:
                self.log.error('Failed to connect to hardware')
                self.bad_hardware.add('filterwheel')

    def _get_info(self):
        """Get the latest status info from the hardware.

        This function will check if any piece of hardware is not responding and save it to
        the bad_hardware list if so, which will trigger a hardware_error.
        """
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        temp_info['ut'] = self.ut

        # Get Filter Wheel info
        temp_info['params'] = self.params
        try:
            if not self.filterwheel.connected:
                raise ValueError('Filter Wheel not connected')
            temp_info['serial'] = self.filterwheel.serial_number
        except Exception:
            self.log.error('Failed to get Filter Wheel info')
            self.log.debug('', exc_info=True)
            temp_info['serial'] = None
            # Report the connection as failed
            self.filterwheel = None
            if 'filterwheel' not in self.bad_hardware:
                self.bad_hardware.add('filterwheel')

        # Write debug log line
        # NONE, nothing really changes

        # Update the master info dict
        self.info = temp_info

    # Control functions
    def move_filterwheel(self, new_number):
        """Move filter wheel to position."""
        self.log.info('Moving to position {}'.format(new_number))
        pool = ThreadPoolExecutor(max_workers=4)  # Why a pool?
        pool.submit(self.filterwheel.set_filter_pos, new_number)

    def home_filterwheel(self):
        """Move filter wheel to home position."""
        self.log.info('Moving to home position')
        self.filterwheel.home()

    def get_position(self):
        """Return current filter wheel position number."""
        return self.filterwheel.get_filter_pos()

    def get_motor_position(self):
        """Return current motor position."""
        return self.filterwheel.stepper_position

    def get_steps_remaining(self):
        """Return filter wheel steps remaining."""
        return self.filterwheel.get_steps_remaining()

    def get_homed(self):
        """Return if filter wheel has been homed."""
        return self.filterwheel.homed

    def get_serial_number(self):
        """Return filter wheel unique serial number."""
        return self.filterwheel.serial_number

    def get_class(self):
        """Return filter wheel hardware class."""
        return self.params['CLASS']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ut', type=int)
    args = parser.parse_args()

    ut = args.ut
    daemon = FiltInterfaceDaemon(ut)
    with make_pid_file(daemon.daemon_id):
        host, port = get_daemon_host(daemon.daemon_id)
        daemon._run(host, port, timeout=params.PYRO_TIMEOUT)
