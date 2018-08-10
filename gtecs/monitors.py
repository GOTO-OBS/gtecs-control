"""Daemon monitor classes for the pilot."""

import time
from abc import ABC, abstractmethod

from .daemons import daemon_info, daemon_is_alive
from .misc import execute_command


class BaseMonitor(ABC):
    """Generic monitor class, inherited by specific classes for each daemon.

    This is an abstract class and must be subtyped.
    Needed methods to implement:
        - get_status()
        - _check_hardware()
        - _recovery_procedure()

    Parameters
    ----------
    log: `logging.Logger`
        log object to direct output to

    """

    def __init__(self, daemon_id, log=None):
        self.daemon_id = daemon_id
        self.log = log

        self.info = None
        self.status = 'unknown'

        self.mode = None
        self.available_modes = []

        self.errors = []
        self.last_successful_check = 0.
        self.recovery_level = 0

    def is_alive(self):
        """Ping a daemon - return True if it is alive and False for dead or not responding."""
        if self.daemon_id is None:
            return True
        try:
            alive = daemon_is_alive(self.daemon_id)
            return alive
        except Exception:
            return False

    def get_info(self):
        """Get the daemon info dict."""
        if self.daemon_id is None:
            return None
        try:
            info = daemon_info(self.daemon_id)
            assert isinstance(info, dict)
        except Exception:
            info = None
        if info is not None:
            self.info = info
        return info

    @abstractmethod
    def get_status(self):
        """Get the current status of the hardware.

        This abstract method must be implemented by all hardware to add hardware-specific checks.
        """
        self.status = 'unknown'
        return 'unknown'

    def set_mode(self, mode):
        """Set the observing mode."""
        if mode in self.available_modes:
            self.mode = mode
        else:
            raise ValueError('Invalid mode: {} not in {!r}'.format(mode,
                                                                   self.available_modes))

    @abstractmethod
    def _check_hardware(self):
        """Check the hardware by running through the recovery steps.

        This abstract method must be implemented by all hardware to add hardware-specific checks.
        """
        return

    def check(self):
        """Check if hardware is OK.

        Returns
        -------
        num_errors : int
            0 for OK, >0 for errors
        errors : list of string
            details of errors found

        """
        self.errors = []

        if not self.is_alive:
            return 1, ['Ping failed']

        info = self.get_info()
        if info is None:
            return 1, ['Get info failed']

        status = self.get_status()
        if status is 'unknown':
            return 1, ['Hardware in unknown state']

        self._check_hardware()  # Will fill self.errors if it finds any

        if len(self.errors) < 1:
            self.last_successful_check = time.time()
            self.recovery_level = 0

        return len(self.errors), self.errors

    @abstractmethod
    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode.

        This abstract method must be implemented by all hardware to add hardware-specific commands.
        """
        return {}

    def recover(self):
        """Run recovery commands.

        Checks whether enough time has elapsed to progress to next stage of recovery.
        """
        downtime = time.time() - self.last_successful_check

        recovery_procedure = self._recovery_procedure()
        next_level = self.recovery_level + 1
        if next_level in recovery_procedure:
            if downtime > recovery_procedure[next_level][0]:
                for cmd in recovery_procedure[next_level][1:]:
                    msg = 'Attempting recovery level {:.0f}: {}'.format(next_level, cmd)
                    if self.log:
                        self.log.info(msg)
                    else:
                        print(msg)
                    execute_command(cmd)
                self.recovery_level += 1
        else:
            return


class DomeMonitor(BaseMonitor):
    """Hardware monitor for the dome daemon."""

    def __init__(self, log=None):
        super().__init__('dome', log)

        # Define modes and starting mode
        self.available_modes = ['closed', 'open']
        self.set_mode('closed')

        # Dome attributes
        self._move_start_time = 0
        self._currently_moving = False
        self._part_open_start_time = 0
        self._currently_part_open = False

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = 'unknown'
            return 'unknown'

        north = info['north']
        south = info['south']

        if north == 'closed' and south == 'closed':
            status = 'closed'
        elif north == 'full_open' and south == 'full_open':
            status = 'full_open'
        elif north in ['opening', 'closing'] or south in ['opening', 'closing']:
            status = 'moving'
        elif north in ['part_open', 'full_open'] or south in ['part_open', 'full_open']:
            status = 'part_open'
        else:
            status = 'unknown'

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == 'moving':
            # Allow some time to move before raising an error
            if not self._currently_moving:
                self._currently_moving = True
                self._move_start_time = time.time()
            else:
                if time.time() - self._move_start_time > 60:
                    self.errors.append('Moving taking too long')
        else:
            self._currently_moving = False
            self._move_start_time = 0

        if self.status == 'part_open':
            # Allow some time to be partially open (sounding alarm between moving sides)
            if not self._currently_part_open:
                self._currently_part_open = True
                self._part_open_start_time = time.time()
            else:
                if time.time() - self._part_open_start_time > 10:
                    self.errors.append('Stuck partially open for too long')
        else:
            self._currently_part_open = False
            self._part_open_start_time = 0

        if self.mode == 'open' and self.status not in ['full_open']:
            self.errors.append('Dome not fully open')

        if self.mode == 'closed' and self.status not in ['closed']:
            self.errors.append('Dome not closed')

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'open':
            # dome open commands may need repeating if cond change has not propogated
            recovery_procedure[1] = [30., 'dome open']
            recovery_procedure[2] = [120., 'dome close both 0.1']
            recovery_procedure[3] = [120., 'dome open']
            recovery_procedure[4] = [180., 'dome open']
            recovery_procedure[5] = [240., 'dome close']
            recovery_procedure[6] = [360., 'dome open']
        elif self.mode == 'closed':
            recovery_procedure = {}

        return recovery_procedure


class MntMonitor(BaseMonitor):
    """Hardware monitor for the mount daemon."""

    def __init__(self, log=None):
        super().__init__('mnt', log)

        # Define modes and starting mode
        self.available_modes = ['parked', 'tracking']
        self.set_mode('parked')

        # Mount attributes
        self._move_start_time = 0
        self._currently_moving = False
        self._off_target_start_time = 0
        self._currently_off_target = False

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = 'unknown'
            return 'unknown'

        mount = info['status']
        target_dist = info['target_dist']

        if mount == 'Tracking':
            if not target_dist:
                status = 'tracking'
            elif float(self.info['target_dist']) < 0.003:
                status = 'tracking'
            else:
                status = 'off_target'
        elif mount in ['Slewing', 'Parking']:
            status = 'moving'
        elif mount == 'Parked':
            status = 'parked'
        elif mount == 'Stopped':
            status = 'stopped'
        elif mount == 'IN BLINKY MODE':
            status = 'in_blinky'
        else:
            status = 'unknown'

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == 'moving':
            if not self._currently_moving:
                self._currently_moving = True
                self._move_start_time = time.time()
            else:
                if time.time() - self._move_start_time > 120:
                    self.errors.append('Moving taking too long')
        else:
            self._currently_moving = False
            self._move_start_time = 0

        if self.status == 'off_target':
            if not self._currently_off_target:
                self._currently_off_target = True
                self._off_target_start_time = time.time()
            else:
                if time.time() - self._off_target_start_time > 30:
                    self.errors.append('Not on target')

        if self.mode == 'parked' and self.status not in ['parked', 'moving']:
            self.errors.append('Not parked')

        if self.status == 'in_blinky':
            self.errors.append('In blinky mode')

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'tracking':
            recovery_procedure = {}
            recovery_procedure[1] = [60., 'mnt track']
            recovery_procedure[2] = [120., 'mnt slew']
            recovery_procedure[3] = [240., 'mnt track']
            recovery_procedure[4] = [270., 'mnt unpark']
            recovery_procedure[5] = [290., 'mnt track']
            recovery_procedure[6] = [320., 'mnt slew']
            recovery_procedure[7] = [320., 'mnt slew']
            recovery_procedure[8] = [360., 'mnt track']
        elif self.mode == 'parked':
            recovery_procedure = {}
            recovery_procedure[1] = [60., 'mnt stop']
            recovery_procedure[2] = [120., 'mnt park']
            recovery_procedure[3] = [180., 'mnt unpark']
            recovery_procedure[4] = [240., 'mnt stop']
            recovery_procedure[4] = [360., 'mnt park']

        return recovery_procedure


class PowerMonitor(BaseMonitor):
    """Hardware monitor for the power daemon."""

    def __init__(self, log=None):
        super().__init__('power', log)

        # Define modes and starting mode
        self.available_modes = ['active']
        self.set_mode('active')

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = 'unknown'
            return 'unknown'

        # no custom statuses
        status = 'active'

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == 'unknown':
            self.errors.append('Hardware in unknown state')
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'active':
            recovery_procedure[1] = [60., 'power start']
            recovery_procedure[2] = [120., 'power kill']
            recovery_procedure[3] = [130., 'power start']

        return recovery_procedure


class CamMonitor(BaseMonitor):
    """Hardware monitor for the camera daemon."""

    def __init__(self, log=None):
        super().__init__('cam', log)

        # Define modes and starting mode
        self.available_modes = ['active']
        self.set_mode('active')

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = 'unknown'
            return 'unknown'

        # no custom statuses
        status = 'active'

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == 'unknown':
            self.errors.append('Hardware in unknown state')
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'science':
            recovery_procedure[1] = [60., 'cam start']
            recovery_procedure[2] = [120., 'cam kill']
            recovery_procedure[3] = [130., 'cam start']

        return recovery_procedure


class FiltMonitor(BaseMonitor):
    """Hardware monitor for the filter wheel daemon."""

    def __init__(self, log=None):
        super().__init__('filt', log)

        # Define modes and starting mode
        self.available_modes = ['active']
        self.set_mode('active')

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = 'unknown'
            return 'unknown'

        # no custom statuses
        status = 'active'

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == 'unknown':
            self.errors.append('Hardware in unknown state')
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'science':
            recovery_procedure[1] = [60., 'filt start']
            recovery_procedure[2] = [120., 'filt kill']
            recovery_procedure[3] = [130., 'filt start']

        return recovery_procedure


class FocMonitor(BaseMonitor):
    """Hardware monitor for the focuser daemon."""

    def __init__(self, log=None):
        super().__init__('foc', log)

        # Define modes and starting mode
        self.available_modes = ['active']
        self.set_mode('active')

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = 'unknown'
            return 'unknown'

        # no custom statuses
        status = 'active'

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == 'unknown':
            self.errors.append('Hardware in unknown state')
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'science':
            recovery_procedure[1] = [60., 'foc start']
            recovery_procedure[2] = [120., 'foc kill']
            recovery_procedure[3] = [130., 'foc start']

        return recovery_procedure


class ExqMonitor(BaseMonitor):
    """Hardware monitor for the exposure queue daemon."""

    def __init__(self, log=None):
        super().__init__('exq', log)

        # Define modes and starting mode
        self.available_modes = ['active']
        self.set_mode('active')

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = 'unknown'
            return 'unknown'

        # no custom statuses
        status = 'active'

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == 'unknown':
            self.errors.append('Hardware in unknown state')
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'science':
            recovery_procedure[1] = [60., 'exq start']
            recovery_procedure[2] = [120., 'exq kill']
            recovery_procedure[3] = [130., 'exq start']

        return recovery_procedure
