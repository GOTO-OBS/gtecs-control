"""Daemon monitor classes for the pilot."""

import time
from abc import ABC, abstractmethod

from .daemons import daemon_info, daemon_is_alive, dependencies_are_alive
from .misc import execute_command
from .slack import send_slack_msg


# Daemon statuses
STATUS_UNKNOWN = 'unknown'
STATUS_ACTIVE = 'active'
STATUS_DOME_CLOSED = 'closed'
STATUS_DOME_FULLOPEN = 'full_open'
STATUS_DOME_PARTOPEN = 'part_open'
STATUS_DOME_MOVING = 'moving'
STATUS_MNT_TRACKING = 'tracking'
STATUS_MNT_OFFTARGET = 'off_target'
STATUS_MNT_MOVING = 'moving'
STATUS_MNT_PARKED = 'parked'
STATUS_MNT_STOPPED = 'stopped'
STATUS_MNT_BLINKY = 'in_blinky'

# Daemon modes
MODE_ACTIVE = 'active'
MODE_DOME_CLOSED = 'closed'
MODE_DOME_OPEN = 'open'
MODE_MNT_PARKED = 'parked'
MODE_MNT_TRACKING = 'tracking'

# Daemon errors
ERROR_DEPENDENCY = 'Dependency ping failed'
ERROR_PING = 'Ping failed'
ERROR_INFO = 'Get info failed'
ERROR_UNKNOWN = 'Hardware in unknown state'
ERROR_DOME_MOVETIMEOUT = 'Moving taking too long'
ERROR_DOME_PARTOPENTIMEOUT = 'Stuck partially open for too long'
ERROR_DOME_NOTFULLOPEN = 'Dome not fully open'
ERROR_DOME_NOTCLOSED = 'Dome not closed'
ERROR_MNT_MOVETIMEOUT = 'Moving taking too long'
ERROR_MNT_NOTONTARGET = 'Mount not on target'
ERROR_MNT_NOTPARKED = 'Mount not parked'
ERROR_MNT_INBLINKY = 'Mount in blinky mode'


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
        self.status = STATUS_UNKNOWN

        self.errors = []
        self.last_successful_check = 0.
        self.recovery_level = 0

        self.get_status()

    def is_alive(self):
        """Ping the daemon and return True if it is running and responding."""
        if self.daemon_id is None:
            return True
        try:
            return daemon_is_alive(self.daemon_id)
        except Exception:
            return False

    def dependencies_are_alive(self):
        """Ping a daemon's dependencies and return True if they are all running and responding."""
        if self.daemon_id is None:
            return True
        try:
            return dependencies_are_alive(self.daemon_id)
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
        self.status = STATUS_UNKNOWN
        return STATUS_UNKNOWN

    @property
    def mode(self):
        """Get the observing mode of the hardware."""
        return self.__mode

    @mode.setter
    def mode(self, mode):
        if mode in self.available_modes:
            self.__mode = mode
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

        # Functional checks
        # Note these overwrite self.errors not append, because they're critical
        if not self.dependencies_are_alive():
            self.errors = [ERROR_DEPENDENCY]
            return len(self.errors), self.errors

        if not self.is_alive:
            self.errors = [ERROR_PING]
            return len(self.errors), self.errors

        info = self.get_info()
        if info is None:
            self.errors = [ERROR_INFO]
            return len(self.errors), self.errors

        status = self.get_status()
        if status is STATUS_UNKNOWN:
            self.errors = [ERROR_UNKNOWN]
            return len(self.errors), self.errors

        # Hardware checks
        # Will fill self.errors if it finds any
        self._check_hardware()

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
        if not self.errors:
            # nothing to recover from!
            return

        downtime = time.time() - self.last_successful_check

        # Get the recovery commands from the daemon's custom method.
        recovery_procedure = self._recovery_procedure()

        next_level = self.recovery_level + 1
        if next_level not in recovery_procedure:
            msg = '{} has run out of recovery steps '.format(self.__class__.__name__)
            msg += 'with {:.0f} error(s): {!r} '.format(len(self.errors), self.errors)
            msg += '(mode={}, status={})'.format(self.mode, self.status)
            if self.log:
                self.log.info(msg)
            else:
                print(msg)
            send_slack_msg(msg)
            return

        delay = recovery_procedure[next_level][0]
        commands = recovery_procedure[next_level][1:]
        if downtime > delay:
            for i, cmd in enumerate(commands):
                msg = '{} attempting recovery '.format(self.__class__.__name__)
                msg += 'level {:.0f}.{:.0f}: {}'.format(next_level, i, cmd)
                if self.log:
                    self.log.info(msg)
                else:
                    print(msg)
                execute_command(cmd)
            self.recovery_level += 1


class DomeMonitor(BaseMonitor):
    """Hardware monitor for the dome daemon."""

    def __init__(self, log=None):
        super().__init__('dome', log)

        # Define modes and starting mode
        self.available_modes = [MODE_DOME_CLOSED, MODE_DOME_OPEN]
        self.mode = MODE_DOME_CLOSED

        # Dome attributes
        self._move_start_time = 0
        self._currently_moving = False
        self._part_open_start_time = 0
        self._currently_part_open = False

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        north = info['north']
        south = info['south']

        if north == 'closed' and south == 'closed':
            status = STATUS_DOME_CLOSED
        elif north == 'full_open' and south == 'full_open':
            status = STATUS_DOME_FULLOPEN
        elif north in ['opening', 'closing'] or south in ['opening', 'closing']:
            status = STATUS_DOME_MOVING
        elif north in ['part_open', 'full_open'] or south in ['part_open', 'full_open']:
            status = STATUS_DOME_PARTOPEN
        else:
            status = STATUS_UNKNOWN

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == STATUS_DOME_MOVING:
            # Allow some time to move before raising an error
            if not self._currently_moving:
                self._currently_moving = True
                self._move_start_time = time.time()
            else:
                if time.time() - self._move_start_time > 60:
                    self.errors.append(ERROR_DOME_MOVETIMEOUT)
        else:
            self._currently_moving = False
            self._move_start_time = 0

        if self.status == STATUS_DOME_PARTOPEN:
            # Allow some time to be partially open (sounding alarm between moving sides)
            if not self._currently_part_open:
                self._currently_part_open = True
                self._part_open_start_time = time.time()
            else:
                if time.time() - self._part_open_start_time > 10:
                    self.errors.append(ERROR_DOME_PARTOPENTIMEOUT)
        else:
            self._currently_part_open = False
            self._part_open_start_time = 0

        if self.mode == MODE_DOME_OPEN and self.status != STATUS_DOME_FULLOPEN:
            self.errors.append(ERROR_DOME_NOTFULLOPEN)

        if self.mode == MODE_DOME_CLOSED and self.status != MODE_DOME_CLOSED:
            self.errors.append(ERROR_DOME_NOTCLOSED)

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == MODE_DOME_OPEN:
            # dome open commands may need repeating if cond change has not propogated
            recovery_procedure[1] = [30., 'dome open']
            recovery_procedure[2] = [120., 'dome close both 0.1']
            recovery_procedure[3] = [120., 'dome open']
            recovery_procedure[4] = [180., 'dome open']
            recovery_procedure[5] = [240., 'dome close']
            recovery_procedure[6] = [360., 'dome open']
        elif self.mode == MODE_DOME_CLOSED:
            recovery_procedure = {}

        return recovery_procedure


class MntMonitor(BaseMonitor):
    """Hardware monitor for the mount daemon."""

    def __init__(self, log=None):
        super().__init__('mnt', log)

        # Define modes and starting mode
        self.available_modes = [MODE_MNT_PARKED, MODE_MNT_TRACKING]
        self.mode = MODE_MNT_PARKED

        # Mount attributes
        self._move_start_time = 0
        self._currently_moving = False
        self._off_target_start_time = 0
        self._currently_off_target = False

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        mount = info['status']
        target_dist = info['target_dist']

        if mount == 'Tracking':
            if not target_dist:
                status = STATUS_MNT_TRACKING
            elif float(target_dist) < 0.003:
                status = STATUS_MNT_TRACKING
            else:
                status = STATUS_MNT_OFFTARGET
        elif mount in ['Slewing', 'Parking']:
            status = STATUS_MNT_MOVING
        elif mount == 'Parked':
            status = STATUS_MNT_PARKED
        elif mount == 'Stopped':
            status = STATUS_MNT_STOPPED
        elif mount == 'IN BLINKY MODE':
            status = STATUS_MNT_BLINKY
        else:
            status = STATUS_UNKNOWN

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == STATUS_MNT_MOVING:
            if not self._currently_moving:
                self._currently_moving = True
                self._move_start_time = time.time()
            else:
                if time.time() - self._move_start_time > 120:
                    self.errors.append(ERROR_MNT_MOVETIMEOUT)
        else:
            self._currently_moving = False
            self._move_start_time = 0

        if self.status == STATUS_MNT_OFFTARGET:
            if not self._currently_off_target:
                self._currently_off_target = True
                self._off_target_start_time = time.time()
            else:
                if time.time() - self._off_target_start_time > 30:
                    self.errors.append(ERROR_MNT_NOTONTARGET)

        if (self.mode == MODE_MNT_PARKED and
                self.status not in [STATUS_MNT_PARKED, STATUS_MNT_MOVING]):
            self.errors.append(ERROR_MNT_NOTPARKED)

        if self.status == STATUS_MNT_BLINKY:
            self.errors.append(ERROR_MNT_INBLINKY)

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == MODE_MNT_TRACKING:
            recovery_procedure = {}
            recovery_procedure[1] = [60., 'mnt track']
            recovery_procedure[2] = [120., 'mnt slew']
            recovery_procedure[3] = [240., 'mnt track']
            recovery_procedure[4] = [270., 'mnt unpark']
            recovery_procedure[5] = [290., 'mnt track']
            recovery_procedure[6] = [320., 'mnt slew']
            recovery_procedure[7] = [320., 'mnt slew']
            recovery_procedure[8] = [360., 'mnt track']
        elif self.mode == MODE_MNT_PARKED:
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
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        status = STATUS_ACTIVE

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == STATUS_UNKNOWN:
            self.errors.append(ERROR_UNKNOWN)
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == MODE_ACTIVE:
            recovery_procedure[1] = [60., 'power start']
            recovery_procedure[2] = [120., 'power kill']
            recovery_procedure[3] = [130., 'power start']

        return recovery_procedure


class CamMonitor(BaseMonitor):
    """Hardware monitor for the camera daemon."""

    def __init__(self, log=None):
        super().__init__('cam', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        status = STATUS_ACTIVE

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == STATUS_UNKNOWN:
            self.errors.append(ERROR_UNKNOWN)
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == MODE_ACTIVE:
            recovery_procedure[1] = [60., 'cam start']
            recovery_procedure[2] = [120., 'cam kill']
            recovery_procedure[3] = [130., 'cam start']

        return recovery_procedure


class FiltMonitor(BaseMonitor):
    """Hardware monitor for the filter wheel daemon."""

    def __init__(self, log=None):
        super().__init__('filt', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        status = STATUS_ACTIVE

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == STATUS_UNKNOWN:
            self.errors.append(ERROR_UNKNOWN)
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == MODE_ACTIVE:
            recovery_procedure[1] = [60., 'filt start']
            recovery_procedure[2] = [120., 'filt kill']
            recovery_procedure[3] = [130., 'filt start']

        return recovery_procedure


class FocMonitor(BaseMonitor):
    """Hardware monitor for the focuser daemon."""

    def __init__(self, log=None):
        super().__init__('foc', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        status = STATUS_ACTIVE

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == STATUS_UNKNOWN:
            self.errors.append(ERROR_UNKNOWN)
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == MODE_ACTIVE:
            recovery_procedure[1] = [60., 'foc start']
            recovery_procedure[2] = [120., 'foc kill']
            recovery_procedure[3] = [130., 'foc start']

        return recovery_procedure


class ExqMonitor(BaseMonitor):
    """Hardware monitor for the exposure queue daemon."""

    def __init__(self, log=None):
        super().__init__('exq', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        status = STATUS_ACTIVE

        self.status = status
        return status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        if self.status == STATUS_UNKNOWN:
            self.errors.append(ERROR_UNKNOWN)
            return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == MODE_ACTIVE:
            recovery_procedure[1] = [60., 'exq start']
            recovery_procedure[2] = [120., 'exq kill']
            recovery_procedure[3] = [130., 'exq start']

        return recovery_procedure
