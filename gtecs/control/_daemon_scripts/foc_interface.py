#!/usr/bin/env python3
"""Interface to access focuser hardware remotely."""

import argparse
import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, get_daemon_host
from gtecs.control.hardware.fli import FLIFocuser, FakeFocuser
from gtecs.control.hardware.ota import FakeH400, H400


class FocInterfaceDaemon(BaseDaemon):
    """Focuser interface daemon class."""

    def __init__(self, ut):
        super().__init__(f'foc{ut}')

        # params
        self.ut = ut
        self.params = params.UT_DICT[ut]['FOCUSER']

        # hardware
        self.focuser = None

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
        return

    # Internal functions
    def _connect(self):
        """Connect to hardware."""
        # Connect to focuser
        if self.focuser is None:
            if 'focuser' not in self.bad_hardware:
                self.log.info('Connecting to Focuser')
            try:
                if 'CLASS' not in self.params:
                    raise ValueError('Missing class')

                # Connect to appropriate hardware class
                if self.params['CLASS'] == 'FLI':
                    # FLI USB Focuser, needs a serial number
                    if 'SERIAL' not in self.params:
                        raise ValueError('Missing serial number')
                    focuser = FLIFocuser.locate_device(self.params['SERIAL'])
                    if focuser is None and params.FAKE_INTF:
                        self.log.info('Creating a fake Focuser')
                        focuser = FakeFocuser('/dev/fake', 'FakeCamera')
                        focuser.serial_number = self.params['SERIAL']
                        focuser.connected = True
                    if focuser is None:
                        raise ValueError('Could not locate hardware')

                elif self.params['CLASS'] == 'ASA':
                    # ASA H400 in-built Focuser, needs a port and a serial number
                    if 'PORT' not in self.params:
                        raise ValueError('Missing serial port')
                    if 'SERIAL' not in self.params:
                        raise ValueError('Missing serial number')
                    focuser = H400.locate_device(self.params['PORT'], self.params['SERIAL'])
                    if focuser is None and params.FAKE_INTF:
                        self.log.info('Creating a fake Focuser')
                        focuser = FakeH400('/dev/fake', self.params['SERIAL'])
                    if focuser is None:
                        raise ValueError('Could not locate hardware')

                else:
                    raise ValueError('Unknown class: {}'.format(self.params['CLASS']))

                if not focuser.connected:
                    raise ValueError('Could not connect to hardware')

                self.log.info('Connected to {}'.format(focuser.serial_number))
                self.focuser = focuser
                if 'focuser' in self.bad_hardware:
                    self.bad_hardware.remove('focuser')

            except Exception:
                self.focuser = None
                self.log.debug('', exc_info=True)
                if 'focuser' not in self.bad_hardware:
                    self.log.error('Failed to connect to hardware')
                    self.bad_hardware.add('focuser')

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

        temp_info['ut'] = self.ut

        # Get Focuser info
        temp_info['params'] = self.params
        try:
            if not self.focuser.connected:
                raise ValueError('Focuser not connected')
            temp_info['serial'] = self.focuser.serial_number
        except Exception:
            self.log.error('Failed to get Focuser info')
            self.log.debug('', exc_info=True)
            temp_info['serial'] = None
            # Report the connection as failed
            self.focuser = None
            if 'focuser' not in self.bad_hardware:
                self.bad_hardware.add('focuser')

        # Write debug log line
        # NONE, nothing really changes

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    # Control functions
    def move_focuser(self, steps):
        """Move focuser by given number of steps."""
        self.log.info('Moving by {} steps'.format(steps))
        if isinstance(self.focuser, (FakeH400, H400)):
            self.focuser.move_focuser(steps, blocking=False)
        else:
            self.focuser.step_motor(steps, blocking=False)

    def set_focuser(self, position):
        """Move focuser to given position."""
        if isinstance(self.focuser, (FLIFocuser, FakeFocuser)):
            raise NotImplementedError("Focuser doesn't have a set function")
        self.log.info('Moving to position {}'.format(position))
        self.focuser.set_focuser(position, blocking=False)

    def can_set(self):
        """Check if the focuser has a set command."""
        if isinstance(self.focuser, (FakeH400, H400)):
            return True
        return False

    def home_focuser(self):
        """Move focuser to the home position."""
        self.log.info('Moving to home position')
        self.focuser.home_focuser()

    def stop_focuser(self):
        """Stop the focuser from moving."""
        if isinstance(self.focuser, (FLIFocuser, FakeFocuser)):
            raise NotImplementedError("Focuser doesn't have a stop function")
        return self.focuser.stop_focuser()

    def can_stop(self):
        """Check if the focuser has a stop command."""
        if isinstance(self.focuser, (FLIFocuser, FakeFocuser)):
            return False
        return True

    def sync_focuser(self, position):
        """Set the current motor position to the given value."""
        raise NotImplementedError("Focuser doesn't have a sync function")

    def can_sync(self):
        """Check if the focuser has a sync command."""
        return False

    def get_limit(self):
        """Return focuser motor limit."""
        return self.focuser.max_extent

    def get_position(self):
        """Return focuser position."""
        return self.focuser.stepper_position

    def get_focuser_status(self):  # Can't be get_status as that's a BaseDaemon function
        """Return focuser status."""
        if isinstance(self.focuser, (FLIFocuser, FakeFocuser)):
            raise NotImplementedError("Focuser doesn't have a status")
        return self.focuser.get_status()

    def get_steps_remaining(self):
        """Return focuser motor limit."""
        if isinstance(self.focuser, (H400, FakeH400)):
            raise NotImplementedError("Focuser doesn't store steps remaining")
        return self.focuser.get_steps_remaining()

    def get_temp(self, temp_type):
        """Return focuser internal/external temperature."""
        if isinstance(self.focuser, (H400, FakeH400)):
            raise NotImplementedError("Focuser doesn't have temperature sensors")
        return self.focuser.read_temperature(temp_type)

    def get_serial_number(self):
        """Return focuser unique serial number."""
        return self.focuser.serial_number

    def get_class(self):
        """Return focuser hardware class."""
        return self.params['CLASS']

    # Mirror cover control functions (part of the ASA H400 class)
    def open_cover(self):
        """Open the mirror cover."""
        if not isinstance(self.focuser, (H400, FakeH400)):
            raise NotImplementedError('OTA does not have a mirror cover')
        self.log.info('Opening mirror cover')
        return self.focuser.open_cover()

    def close_cover(self):
        """Close the mirror cover."""
        if not isinstance(self.focuser, (H400, FakeH400)):
            raise NotImplementedError('OTA does not have a mirror cover')
        self.log.info('Closing mirror cover')
        return self.focuser.close_cover()

    def stop_cover(self):
        """Stop the mirror cover from moving."""
        if not isinstance(self.focuser, (H400, FakeH400)):
            raise NotImplementedError('OTA does not have a mirror cover')
        self.log.info('Stopping mirror cover')
        return self.focuser.stop_cover()

    def get_cover_position(self):
        """Return mirror cover position."""
        if not isinstance(self.focuser, (H400, FakeH400)):
            raise NotImplementedError('OTA does not have a mirror cover')
        return self.focuser.get_cover_position()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ut', type=int)
    args = parser.parse_args()

    ut = args.ut
    daemon = FocInterfaceDaemon(ut)
    with make_pid_file(daemon.daemon_id):
        host, port = get_daemon_host(daemon.daemon_id)
        daemon._run(host, port, timeout=params.PYRO_TIMEOUT)
