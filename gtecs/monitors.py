"""Daemon monitor classes for the pilot."""

import logging
import time
from abc import ABC, abstractmethod

from . import params
from .daemons import daemon_is_running, daemon_proxy
from .errors import RecoveryError
from .misc import execute_command

# Daemon statuses
DAEMON_RUNNING = 'running'
DAEMON_ERROR_STATUS = 'status_error'
DAEMON_ERROR_RUNNING = 'running_error'
DAEMON_ERROR_DEPENDENCY = 'dependency_error'
DAEMON_ERROR_HARDWARE = 'hardware_error'
DAEMON_ERROR_PING = 'ping_error'

# Hardware statuses
STATUS_UNKNOWN = 'unknown'
STATUS_ACTIVE = 'active'
STATUS_DOME_LOCKDOWN = 'in_lockdown'
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
STATUS_MNT_CONNECTION_ERROR = 'connection_error'
STATUS_CAM_COOL = 'cool'
STATUS_CAM_WARM = 'warm'
STATUS_FILT_UNHOMED = 'unhomed'
STATUS_CONDITIONS_INTERNAL_ERROR = 'internal_error'

# Hardware modes
MODE_ACTIVE = 'active'
MODE_DOME_CLOSED = 'closed'
MODE_DOME_OPEN = 'open'
MODE_MNT_PARKED = 'parked'
MODE_MNT_TRACKING = 'tracking'
MODE_CAM_COOL = 'cool'
MODE_CAM_WARM = 'warm'

# Hardware errors
ERROR_RUNNING = 'NOT_RUNNING'
ERROR_PING = 'PING_FAILED'
ERROR_INFO = 'INFO_FAILED'
ERROR_HARDWARE = 'HARDWARE_FAILED'
ERROR_DEPENDENCY = 'DEPEND_FAILED'
ERROR_STATUS = 'STATUS_FAILED'
ERROR_UNKNOWN = 'UNKNOWN'
ERROR_DOME_MOVETIMEOUT = 'DOME:MOVING_TIMEOUT'
ERROR_DOME_PARTOPENTIMEOUT = 'DOME:PARTOPEN_TIMEOUT'
ERROR_DOME_NOTFULLOPEN = 'DOME:NOT_FULLOPEN'
ERROR_DOME_NOTCLOSED = 'DOME:NOT_CLOSED'
ERROR_MNT_MOVETIMEOUT = 'MNT:MOVING_TIMEOUT'
ERROR_MNT_NOTONTARGET = 'MNT:NOT_ONTARGET'
ERROR_MNT_STOPPED = 'MNT:NOT_TRACKING'
ERROR_MNT_PARKED = 'MNT:PARKED'
ERROR_MNT_NOTPARKED = 'MNT:NOT_PARKED'
ERROR_MNT_INBLINKY = 'MNT:IN_BLINKY'
ERROR_MNT_CONNECTION = 'MNT:LOST_CONNECTION'
ERROR_CAM_WARM = 'CAM:NOT_COOL'
ERROR_FILT_UNHOMED = 'FLIT:NOT_HOMED'
ERROR_CONDITIONS_INTERNAL = 'CONDITIONS:INTERNAL_ERROR'


class BaseMonitor(ABC):
    """Generic monitor class, inherited by specific classes for each daemon.

    This is an abstract class and must be subtyped.
    Needed methods to implement:
        - get_hardware_status()
        - _check_hardware()
        - _recovery_procedure()

    Parameters
    ----------
    log: `logging.Logger`
        log object to direct output to

    """

    def __init__(self, daemon_id, log=None):
        self.daemon_id = daemon_id
        self.monitor_id = self.__class__.__name__

        if log:
            self.log = log
        else:
            logging.basicConfig(level=logging.DEBUG)
            self.log = logging.getLogger(self.monitor_id)

        self.info = None
        self.info_timeout = params.PYRO_TIMEOUT
        self.hardware_status = STATUS_UNKNOWN

        self.successful_check_time = 0

        self.pending_errors = dict()

        self.errors = set()
        self.bad_dependencies = set()
        self.bad_hardware = set()

        self.active_error = None
        self.recovery_level = 0
        self.recovery_command_time = 0

        self.get_hardware_status()

    # Status functions
    def is_running(self):
        """Check if the daemon is running."""
        try:
            return daemon_is_running(self.daemon_id)
        except Exception:
            return False

    def get_daemon_status(self):
        """Get the current status of the daemon (not to be confused with the hardware status)."""
        try:
            with daemon_proxy(self.daemon_id) as daemon:
                status = daemon.get_status()
            try:
                status, args = status.split(':')
                return status, args.split(',')
            except ValueError:
                # no arguments
                return status, None
        except Exception:
            return DAEMON_ERROR_STATUS, None

    def get_info(self):
        """Get the daemon hardware info dict."""
        if self.daemon_id is None:
            return None
        try:
            with daemon_proxy(self.daemon_id, timeout=self.info_timeout) as daemon:
                # Force an update if we're currently fixing an error,
                # otherwise it's not as important so don't force to save time
                if len(self.errors) > 0:
                    info = daemon.get_info(force_update=True)
                else:
                    info = daemon.get_info(force_update=False)
            assert isinstance(info, dict)
        except Exception:
            info = None
        if info is not None:
            self.info = info
        return info

    @abstractmethod
    def get_hardware_status(self):
        """Get the current status of the hardware.

        This abstract method must be implemented by all hardware to add hardware-specific checks.
        """
        self.hardware_status = STATUS_UNKNOWN
        return STATUS_UNKNOWN

    # System mode
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

    # System checks
    def add_error(self, error, delay=0, critical=False):
        """Add the error to self.errors if it's not already there.

        If a delay if given only add the error after thant many seconds.

        If critical=True it will overwrite self.errors with just this error.
        """
        if error not in self.errors:
            if not delay:
                # Sometimes we don't want to wait
                self.log.debug('Adding error "{}"'.format(error))
                if not critical:
                    self.errors.add(error)
                else:
                    self.errors = set([error])
                return

            if error not in self.pending_errors:
                self.log.debug('"{}" timer started'.format(error))
                self.pending_errors[error] = time.time()
                return
            else:
                error_time = time.time() - self.pending_errors[error]
                self.log.debug('"{}" timer: {:.0f}/{:.0f}s'.format(error, error_time, delay))
                if error_time > delay:
                    self.log.debug('Adding error "{}" after {:.0f}s'.format(error, delay))
                    del self.pending_errors[error]
                    if not critical:
                        self.errors.add(error)
                    else:
                        self.errors = set([error])
                    return

    def clear_error(self, error):
        """Remove the error from self.errors if it's there."""
        if error in self.pending_errors:
            self.log.debug('Resetting "{}" timer'.format(error))
            del self.pending_errors[error]
        if error in self.errors:
            self.log.debug('Clearing error "{}"'.format(error))
            self.errors.remove(error)

    def _check_systems(self):
        """Check critical functions common to all daemons.

        Note these overwrite self.errors (instead of adding to it) and then return immediately.
        """
        # Check if the daemon is running
        is_running = self.is_running()

        # ERROR_RUNNING
        # Set the error if the daemon isn't running
        if not is_running:
            self.add_error(ERROR_RUNNING, critical=True)
            return 1
        # Clear the error if we are running
        if is_running:
            self.clear_error(ERROR_RUNNING)

        # Get the daemon status
        daemon_status, args = self.get_daemon_status()

        # ERROR_PING
        # Set the error if the daemon returns a bad status
        if daemon_status in [DAEMON_ERROR_STATUS, DAEMON_ERROR_RUNNING, DAEMON_ERROR_PING]:
            self.add_error(ERROR_PING, critical=True)
            return 1
        # Clear the error if the status isn't one of the above
        if daemon_status not in [DAEMON_ERROR_STATUS, DAEMON_ERROR_RUNNING, DAEMON_ERROR_PING]:
            self.clear_error(ERROR_PING)

        # ERROR_DEPENDENCY
        # Set the error if the daemon reports a dependency error status
        self.bad_dependencies.clear()
        if daemon_status == DAEMON_ERROR_DEPENDENCY:
            # store the bad dependencies
            for dependency in args:
                self.bad_dependencies.add(dependency)
            self.add_error(ERROR_DEPENDENCY, critical=True)
            return 1
        # Clear the error if the dependency error is cleared
        if daemon_status != DAEMON_ERROR_DEPENDENCY:
            self.clear_error(ERROR_DEPENDENCY)

        # ERROR_HARDWARE
        # Set the error if the daemon reports a hardware error status
        self.bad_hardware.clear()
        if daemon_status == DAEMON_ERROR_HARDWARE:
            # store the bad hardware
            for hardware in args:
                self.bad_hardware.add(hardware)
            self.add_error(ERROR_HARDWARE, critical=True)
            return 1
        # Clear the error if the hardware error is cleared
        if daemon_status != DAEMON_ERROR_HARDWARE:
            self.clear_error(ERROR_HARDWARE)

        # ERROR_PING
        # Set the error if the daemon still reports any status other than running
        if daemon_status != DAEMON_RUNNING:
            self.add_error(ERROR_PING, critical=True)
            return 1
        # Clear the error if the hardware error is cleared
        if daemon_status == DAEMON_RUNNING:
            self.clear_error(ERROR_PING)

        # Get the daemon info
        info = self.get_info()

        # ERROR_INFO
        # Set the error if the daemon doesn't return any info dict
        if info is None or not isinstance(info, dict):
            self.add_error(ERROR_INFO, critical=True)
            return 1
        # Clear the error if the daemon returns info
        if isinstance(info, dict):
            self.clear_error(ERROR_INFO)

        # Get the daemon hardware status
        hardware_status = self.get_hardware_status()

        # ERROR_STATUS
        # Set the error if the daemon doesn't return any info dict
        if hardware_status == STATUS_UNKNOWN:
            self.add_error(ERROR_STATUS, critical=True)
            return 1
        # Clear the error if the daemon returns info
        if hardware_status != STATUS_UNKNOWN:
            self.clear_error(ERROR_STATUS)

    @abstractmethod
    def _check_hardware(self):
        """Check the hardware status and add any errors to self.errors.

        This abstract method must be implemented by all hardware to add hardware-specific checks.
        """
        return

    def check(self):
        """Check if hardware is OK.

        Returns
        -------
        num_errors : int
            0 for OK, >0 for errors
        errors : set of strings
            details of errors found

        """
        # First run common systems checks
        found_error = self._check_systems()

        # Then run custom hardware checks, unless there's already a systems error
        if not found_error:
            self._check_hardware()

        # The above two will have populated self.errors
        if len(self.errors) > 0:
            # If there are errors log them
            msg = '{} ({}) '.format(self.monitor_id, self.hardware_status)
            msg += 'reports {} error{}: {}'.format(len(self.errors),
                                                   's' if len(self.errors) > 1 else '',
                                                   ', '.join(self.errors))
            self.log.warning(msg)
        else:
            # If there are no errors record the time
            self.successful_check_time = time.time()
            self.recovery_command_time = 0
            self.recovery_level = 0

        return len(self.errors), self.errors

    # System recovery
    @abstractmethod
    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode.

        This abstract method must be implemented by all hardware to add hardware-specific commands.
        """
        return {}

    def recover(self):
        """Run recovery commands for the current situation.

        Checks whether enough time has elapsed to progress to next stage of recovery.
        """
        if not self.errors:
            # nothing to recover from!
            return

        # Get the recovery commands for the highest priority error from the daemon's custom method.
        active_error, recovery_procedure = self._recovery_procedure()

        if self.active_error != active_error:
            # We're working on a new procedure, reset the counter.
            self.active_error = active_error
            self.recovery_level = 0

        if self.recovery_level != 0:
            # Each command has a time to wait until progressing to the next level
            time_since_last_command = time.time() - self.recovery_command_time
            delay = recovery_procedure[self.recovery_level][1]
            if time_since_last_command < delay:
                return

        next_level = self.recovery_level + 1
        if next_level not in recovery_procedure:
            msg = '{} has run out of recovery steps '.format(self.monitor_id)
            msg += 'with {:.0f} error(s): {!r} '.format(len(self.errors), self.errors)
            msg += '(mode={}, status={}'.format(self.mode, self.hardware_status)
            if ERROR_HARDWARE in self.errors:
                msg += ', bad_hardware={})'.format(self.bad_hardware)
            elif ERROR_DEPENDENCY in self.errors:
                msg += ', bad_dependencies={})'.format(self.bad_dependencies)
            else:
                msg += ')'
            self.log.error(msg)
            raise RecoveryError(msg)

        command = recovery_procedure[next_level][0]
        msg = '{} attempting recovery '.format(self.monitor_id)
        msg += 'level {:.0f}: {}'.format(next_level, command)
        self.log.warning(msg)
        try:
            execute_command(command)
        except Exception:
            self.log.error('Error executing recovery command {}'.format(command))
            self.log.debug('', exc_info=True)

        self.recovery_command_time = time.time()
        self.recovery_level += 1


class DomeMonitor(BaseMonitor):
    """Hardware monitor for the dome daemon."""

    def __init__(self, log=None):
        super().__init__('dome', log)

        # Define modes and starting mode
        self.available_modes = [MODE_DOME_CLOSED, MODE_DOME_OPEN]
        self.mode = MODE_DOME_CLOSED

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        north = info['north']
        south = info['south']
        lockdown = info['lockdown']

        if lockdown:
            hardware_status = STATUS_DOME_LOCKDOWN
        elif north == 'closed' and south == 'closed':
            hardware_status = STATUS_DOME_CLOSED
        elif north == 'full_open' and south == 'full_open':
            hardware_status = STATUS_DOME_FULLOPEN
        elif north in ['opening', 'closing'] or south in ['opening', 'closing']:
            hardware_status = STATUS_DOME_MOVING
        elif north in ['part_open', 'full_open'] or south in ['part_open', 'full_open']:
            hardware_status = STATUS_DOME_PARTOPEN
        else:
            hardware_status = STATUS_UNKNOWN

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # ERROR_DOME_MOVETIMEOUT
        # Set the error if the dome has been moving for too long
        if self.hardware_status == STATUS_DOME_MOVING:
            self.add_error(ERROR_DOME_MOVETIMEOUT, delay=60)
        # Clear the error if the dome is not moving
        if self.hardware_status != STATUS_DOME_MOVING:
            self.clear_error(ERROR_DOME_MOVETIMEOUT)

        # ERROR_DOME_PARTOPENTIMEOUT
        # Set the error if the dome has been partially open for too long
        if self.hardware_status == STATUS_DOME_PARTOPEN:
            self.add_error(ERROR_DOME_PARTOPENTIMEOUT, delay=60)
        # Clear the error if the dome is where it's supposed to be
        # Note this keeps the error set while it's moving
        # Also note we clear the error if the dome's in lockdown, because we can't move anyway
        if ((self.mode == MODE_DOME_OPEN and self.hardware_status == STATUS_DOME_FULLOPEN) or
            (self.mode == MODE_DOME_CLOSED and self.hardware_status == STATUS_DOME_CLOSED) or
                (self.hardware_status == STATUS_DOME_LOCKDOWN)):
            self.clear_error(ERROR_DOME_PARTOPENTIMEOUT)

        # ERROR_DOME_NOTFULLOPEN
        # Set the error if the dome should be open and it's not
        # Note the dome's allowed to be moving, that has its own error above
        # Also note that part_open is delt with above
        if self.mode == MODE_DOME_OPEN and self.hardware_status not in [STATUS_DOME_FULLOPEN,
                                                                        STATUS_DOME_PARTOPEN,
                                                                        STATUS_DOME_MOVING]:
            self.add_error(ERROR_DOME_NOTFULLOPEN, delay=30)
        # Clear the error if the dome's fully open, or it shouldn't be any more
        # Note this keeps the error set while the dome's moving
        # Also note we clear the error if the dome's in lockdown, because we can't move anyway
        if self.mode != MODE_DOME_OPEN or self.hardware_status in [STATUS_DOME_FULLOPEN,
                                                                   STATUS_DOME_LOCKDOWN]:
            self.clear_error(ERROR_DOME_NOTFULLOPEN)

        # ERROR_DOME_NOTCLOSED
        # Set the error if the dome should be closed and it's not
        # Notethe dome's allowed to be moving, that has its own error above
        # Also note that part_open is delt with above
        if self.mode == MODE_DOME_CLOSED and self.hardware_status not in [STATUS_DOME_CLOSED,
                                                                          STATUS_DOME_PARTOPEN,
                                                                          STATUS_DOME_MOVING,
                                                                          STATUS_DOME_LOCKDOWN]:
            self.add_error(ERROR_DOME_NOTCLOSED, delay=30)
        # Clear the error if the dome's fully closed, or it shouldn't be any more
        # Note this keeps the error set while the dome's moving
        # Also note we clear the error if the dome's in lockdown, because we can't move anyway
        if self.mode != MODE_DOME_CLOSED or self.hardware_status in [STATUS_DOME_CLOSED,
                                                                     STATUS_DOME_LOCKDOWN]:
            self.clear_error(ERROR_DOME_NOTCLOSED)

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # PROBLEM: We've lost connection to the dome or the dehumidifier.
            #          The dome is obviously the higher priority to try and fix.
            recovery_procedure = {}
            if 'dome' in self.bad_hardware:
                # SOLUTION 1: Try rebooting the dome power.
                recovery_procedure[1] = ['power reboot dome', 60]
                # OUT OF SOLUTIONS: We can't contact the dome, panic! Send out the alert.
                return ERROR_HARDWARE + 'dome', recovery_procedure
            elif 'dehumidifer' in self.bad_hardware:
                # SOLUTION 1: Try rebooting the dehumidifier power.
                recovery_procedure[1] = ['power reboot dehumid', 60]
                # OUT OF SOLUTIONS: Not much else we can do, must be a hardware problem.
                return ERROR_HARDWARE + 'dehumidifer', recovery_procedure
            else:
                # OUT OF SOLUTIONS: We don't know where the hardware error is from?
                return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The dome daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['dome start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['dome restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['dome kill', 10]
            recovery_procedure[4] = ['dome start', 30]
            # SOLUTION 4: Maybe there's a problem with the dome.
            recovery_procedure[3] = ['dome kill', 10]
            recovery_procedure[4] = ['power off dome', 60]
            recovery_procedure[5] = ['power on dome', 120]
            recovery_procedure[4] = ['dome start', 60]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        elif ERROR_DOME_MOVETIMEOUT in self.errors:
            # PROBLEM: The dome has been moving for too long.
            recovery_procedure = {}
            # SOLUTION 1: Stop immediately!
            recovery_procedure[1] = ['dome halt', 30]
            # SOLUTION 2: Still moving? Okay, kill the dome daemon.
            recovery_procedure[2] = ['dome kill', 30]
            # OUT OF SOLUTIONS: How can it still be moving??
            return ERROR_DOME_MOVETIMEOUT, recovery_procedure

        elif ERROR_DOME_NOTCLOSED in self.errors:
            # PROBLEM: The dome's not closed when it should be. That's bad.
            recovery_procedure = {}
            # SOLUTION 1: Try closing again.
            recovery_procedure[1] = ['dome close', 90]
            # OUT OF SOLUTIONS: We can't close, panic! Send out the alert.
            return ERROR_DOME_NOTCLOSED, recovery_procedure

        elif ERROR_DOME_PARTOPENTIMEOUT in self.errors:
            # PROBLEM: The dome has been partially open for too long.
            #          Note the dome can naturally stick partially open in the middle of moving
            #          for a while (i.e. when it's sounding the siren to move the second side).
            #          This is for when it's been too long like that, such as when the Honeywell
            #          switches fail to catch.
            recovery_procedure = {}
            # The recovery procudure depends on if it should be open or closed:
            if self.mode == 'open':
                # SOLUTION 1: Try to open again.
                recovery_procedure[1] = ['dome open', 90]
                # SOLUTION 2: Close a little, then open again.
                recovery_procedure[2] = ['dome close both 0.1', 30]
                recovery_procedure[3] = ['dome open', 90]
                # SOLUTION 3: Try again, it's stuck twice in a row before.
                recovery_procedure[4] = ['dome close both 0.1', 30]
                recovery_procedure[5] = ['dome open', 30]
                # OUT OF SOLUTIONS: The dome must be stuck somehow.
                return ERROR_DOME_PARTOPENTIMEOUT, recovery_procedure
            elif self.mode == 'closed':
                # SOLUTION 1: Try to close again.
                recovery_procedure[1] = ['dome close', 90]
                # OUT OF SOLUTIONS: We can't close, panic! Send out the alert.
                return ERROR_DOME_PARTOPENTIMEOUT, recovery_procedure

        elif ERROR_DOME_NOTFULLOPEN in self.errors:
            # PROBLEM: The dome should be open, but it's closed (part_open is caught above).
            recovery_procedure = {}
            # SOLUTION 1: Try opening a few times.
            recovery_procedure[1] = ['dome open', 90]
            recovery_procedure[2] = ['dome open', 90]
            recovery_procedure[3] = ['dome open', 90]
            # OUT OF SOLUTIONS: It's not opening, either it's stuck or it's in lockdown and the
            #                   pilot hasn't realised yet. At least it's safe.
            return ERROR_DOME_NOTFULLOPEN, recovery_procedure

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class MntMonitor(BaseMonitor):
    """Hardware monitor for the mount daemon."""

    def __init__(self, log=None):
        super().__init__('mnt', log)

        # Define modes and starting mode
        self.available_modes = [MODE_MNT_PARKED, MODE_MNT_TRACKING]
        self.mode = MODE_MNT_PARKED

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        mount = info['status']
        target_dist = info['target_dist']

        if mount == 'Tracking':
            if not target_dist:
                hardware_status = STATUS_MNT_TRACKING
            elif float(target_dist) < 0.01:
                hardware_status = STATUS_MNT_TRACKING
            else:
                hardware_status = STATUS_MNT_OFFTARGET
        elif mount in ['Slewing', 'Parking']:
            hardware_status = STATUS_MNT_MOVING
        elif mount == 'Parked':
            hardware_status = STATUS_MNT_PARKED
        elif mount == 'Stopped':
            hardware_status = STATUS_MNT_STOPPED
        elif mount == 'IN BLINKY MODE':
            hardware_status = STATUS_MNT_BLINKY
        elif mount == 'CONNECTION ERROR':
            hardware_status = STATUS_MNT_CONNECTION_ERROR
        else:
            hardware_status = STATUS_UNKNOWN

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # ERROR_MNT_MOVETIMEOUT
        # Set the error if the mount has been moving for too long
        if self.hardware_status == STATUS_MNT_MOVING:
            self.add_error(ERROR_MNT_MOVETIMEOUT, delay=120)
        # Clear the error if the mount is not moving
        if self.hardware_status != STATUS_MNT_MOVING:
            self.clear_error(ERROR_MNT_MOVETIMEOUT)

        # ERROR_MNT_NOTONTARGET
        # Set the error if the mount has been off target for too long
        if self.hardware_status == STATUS_MNT_OFFTARGET:
            self.add_error(ERROR_MNT_NOTONTARGET, delay=60)
        # Clear the error if the mount is on target (or it doesn't have a target, like parking)
        if self.hardware_status != STATUS_MNT_OFFTARGET:
            self.clear_error(ERROR_MNT_NOTONTARGET)

        # ERROR_MNT_INBLINKY
        # Set the error if the mount is in blinky mode
        if self.hardware_status == STATUS_MNT_BLINKY:
            self.add_error(ERROR_MNT_INBLINKY)
        # Clear the error if blinky is off
        if self.hardware_status != STATUS_MNT_BLINKY:
            self.clear_error(ERROR_MNT_INBLINKY)

        # ERROR_MNT_CONNECTION
        # Set the error if SiTech has lost connection to the mount
        if self.hardware_status == STATUS_MNT_CONNECTION_ERROR:
            self.add_error(ERROR_MNT_CONNECTION)
        # Clear the error if SiTech has restored connection
        if self.hardware_status != STATUS_MNT_CONNECTION_ERROR:
            self.clear_error(ERROR_MNT_CONNECTION)

        # ERROR_MNT_STOPPED
        # Set the error if the mount is not moving and it should be tracking
        if self.mode == MODE_MNT_TRACKING and self.hardware_status == STATUS_MNT_STOPPED:
            self.add_error(ERROR_MNT_STOPPED, delay=30)
        # Clear the error if the mount is tracking or it shouldn't be any more
        if self.mode != MODE_MNT_TRACKING or self.hardware_status != STATUS_MNT_STOPPED:
            self.clear_error(ERROR_MNT_STOPPED)

        # ERROR_MNT_PARKED
        # Set the error if the mount is parked and it should be tracking
        if self.mode == MODE_MNT_TRACKING and self.hardware_status == STATUS_MNT_PARKED:
            self.add_error(ERROR_MNT_PARKED, delay=30)
        # Clear the error if the mount is no longer parked or it should be
        if self.mode != MODE_MNT_TRACKING or self.hardware_status != STATUS_MNT_PARKED:
            self.clear_error(ERROR_MNT_PARKED)

        # ERROR_MNT_NOTPARKED
        # Set the error if the mount isn't parked (or moving (parking)) and it should be
        if self.mode == MODE_MNT_PARKED and self.hardware_status not in [STATUS_MNT_PARKED,
                                                                         STATUS_MNT_MOVING]:
            self.add_error(ERROR_MNT_NOTPARKED, delay=30)
        # Clear the error if the mount is parked or it shouldn't be any more
        # Note this keeps the error set while the mount is moving
        if self.mode != MODE_MNT_PARKED or self.hardware_status in STATUS_MNT_PARKED:
            self.clear_error(ERROR_MNT_NOTPARKED)

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # PROBLEM: We've lost connection to SiTechEXE.
            recovery_procedure = {}
            if 'sitech' in self.bad_hardware:
                # SOLUTION 1: Try rebooting the mount NUC.
                #             Note we need to wait for ages for Windows to restart.
                recovery_procedure[1] = ['power off mount_nuc', 10]
                recovery_procedure[2] = ['power on mount_nuc', 180]
                # OUT OF SOLUTIONS: SiTechEXE must not have started correctly.
                return ERROR_HARDWARE + 'sitech', recovery_procedure
            else:
                # OUT OF SOLUTIONS: We don't know where the hardware error is from?
                return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The mount daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['mnt start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['mnt restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['mnt kill', 10]
            recovery_procedure[4] = ['mnt start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        elif ERROR_MNT_CONNECTION in self.errors:
            # PROBLEM: The SiTechEXE has lost connection to the mount controller.
            #          Maybe it's been powered off.
            recovery_procedure = {}
            # SOLUTION 1: Try turning on the sitech box.
            recovery_procedure[1] = ['power on sitech', 60]
            # SOLUTION 2: Still an error? Try restarting it.
            recovery_procedure[2] = ['power off sitech', 10]
            recovery_procedure[3] = ['power on sitech', 60]
            # OUT OF SOLUTIONS: It still can't connect, sounds like a hardware issue.
            return ERROR_MNT_CONNECTION, recovery_procedure

        elif ERROR_MNT_INBLINKY in self.errors:
            # PROBLEM: The mount is in blinky mode.
            #          Maybe it's been tracking for too long and reached the limit,
            #          or there's been some voltage problem.
            recovery_procedure = {}
            # SOLUTION 1: Try turning blinky mode off.
            recovery_procedure[1] = ['mnt blinky off', 60]
            # SOLUTION 2: Maybe there's a problem with the mount.
            recovery_procedure[2] = ['power off mount_nuc', 10]
            recovery_procedure[3] = ['power off sitech', 10]
            recovery_procedure[4] = ['power on sitech', 60]
            recovery_procedure[5] = ['power on mount_nuc', 180]
            # SOLUTION 3: Restart the daemon.
            recovery_procedure[6] = ['mnt restart', 10]
            # OUT OF SOLUTIONS: It's still in blinky mode, sounds like a hardware issue.
            return ERROR_MNT_INBLINKY, recovery_procedure

        elif ERROR_MNT_MOVETIMEOUT in self.errors:
            # PROBLEM: The mount has reported it's been moving for too long.
            recovery_procedure = {}
            # SOLUTION 1: Stop immediately!
            recovery_procedure[1] = ['mnt stop', 30]
            # SOLUTION 2: Still moving? Okay, kill the mnt daemon.
            recovery_procedure[2] = ['mnt kill', 30]
            # OUT OF SOLUTIONS: How can it still be moving??
            return ERROR_MNT_MOVETIMEOUT, recovery_procedure

        elif ERROR_MNT_NOTONTARGET in self.errors:
            # PROBLEM: The mount is in tracking mode and has a target, but it's not on target.
            recovery_procedure = {}
            # SOLUTION 1: Try slewing to the target, this should start tracking too.
            recovery_procedure[1] = ['mnt slew', 60]
            # SOLUTION 2: Maybe we're parked?
            recovery_procedure[2] = ['mnt unpark', 60]
            recovery_procedure[3] = ['mnt slew', 60]
            # SOLUTION 4: It should start tracking when it reaches the target, but just in case.
            recovery_procedure[4] = ['mnt track', 30]
            # OUT OF SOLUTIONS: It can't reach the target for some reason.
            return ERROR_MNT_NOTONTARGET, recovery_procedure

        elif ERROR_MNT_STOPPED in self.errors:
            # PROBLEM: The mount is in tracking mode but it's not tracking.
            recovery_procedure = {}
            # SOLUTION 1: Try tracking.
            recovery_procedure[1] = ['mnt track', 30]
            # SOLUTION 2: Try again.
            recovery_procedure[2] = ['mnt stop', 30]
            recovery_procedure[3] = ['mnt track', 60]
            # SOLUTION 3: It might not be tracking because it's below the horizon.
            #             If this is the error then it doesn't have a target set, so we've probably
            #             only just unparked.
            #             Try slewing to the neutral position.
            recovery_procedure[4] = ['mnt altaz 50 0', 60]
            # OUT OF SOLUTIONS: There must be a problem that's not letting it track.
            return ERROR_MNT_STOPPED, recovery_procedure

        elif ERROR_MNT_PARKED in self.errors:
            # PROBLEM: The mount is in tracking mode but it's parked.
            recovery_procedure = {}
            # SOLUTION 1: Try unparking.
            recovery_procedure[1] = ['mnt unpark', 30]
            # SOLUTION 2: Try again.
            recovery_procedure[2] = ['mnt unpark', 60]
            # OUT OF SOLUTIONS: There must be a problem and it's stuck parked.
            return ERROR_MNT_PARKED, recovery_procedure

        elif ERROR_MNT_NOTPARKED in self.errors:
            # PROBLEM: The mount is in parked mode but it isn't parked.
            recovery_procedure = {}
            # SOLUTION 1: Try parking.
            recovery_procedure[1] = ['mnt park', 120]
            # SOLUTION 2: Try again.
            recovery_procedure[2] = ['mnt unpark', 30]
            recovery_procedure[3] = ['mnt park', 120]
            # OUT OF SOLUTIONS: There must be a problem, maybe the park position isn't defined.
            return ERROR_MNT_NOTPARKED, recovery_procedure

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class PowerMonitor(BaseMonitor):
    """Hardware monitor for the power daemon."""

    def __init__(self, log=None):
        super().__init__('power', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        hardware_status = STATUS_ACTIVE

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # PROBLEM: We've lost connection to a power unit.
            #          Need to go through one-by-one.
            recovery_procedure = {}
            for unit_name in params.POWER_UNITS:
                if unit_name in self.bad_hardware:
                    # OUT OF SOLUTIONS: We don't currently can't reboot power units remotely.
                    #                   TODO: Add that.
                    return ERROR_HARDWARE + unit_name, {}
            # OUT OF SOLUTIONS: We don't know where the hardware error is from?
            return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The power daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['power start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['power restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['power kill', 10]
            recovery_procedure[4] = ['power start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class CamMonitor(BaseMonitor):
    """Hardware monitor for the camera daemon."""

    def __init__(self, log=None):
        super().__init__('cam', log)

        # Define modes and starting mode
        self.available_modes = [MODE_CAM_COOL, MODE_CAM_WARM]
        self.mode = MODE_CAM_COOL

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None or any([info[tel] is None for tel in params.TEL_DICT]):
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        all_cool = all([info[tel]['ccd_temp'] < params.CCD_TEMP + 1 for tel in params.TEL_DICT])
        if not all_cool:
            hardware_status = STATUS_CAM_WARM
        else:
            hardware_status = STATUS_CAM_COOL

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # ERROR_CAM_WARM
        # Set the error if the cameras should be cool and they're not
        if self.mode == MODE_CAM_COOL and self.hardware_status == STATUS_CAM_WARM:
            self.add_error(ERROR_CAM_WARM, delay=30)
        # Clear the error if the cameras are cool or they shouldn't be
        if self.mode != MODE_CAM_COOL or self.hardware_status != STATUS_CAM_WARM:
            self.clear_error(ERROR_CAM_WARM)

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # The cam daemon doesn't directly talk to hardware, so this really shouldn't happen...
            return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The cam daemon depends on the FLI interfaces.
            for daemon_id in params.FLI_INTERFACES:
                if daemon_id in self.bad_dependencies:
                    # PROBLEM: The FLI interfaces aren't responding.
                    recovery_procedure = {}
                    # SOLUTION 1: Make sure the interfaces are started.
                    recovery_procedure[1] = ['fli start', 30]
                    # SOLUTION 2: Try restarting them.
                    recovery_procedure[2] = ['fli restart', 30]
                    # SOLUTION 3: Kill them, then start them again.
                    recovery_procedure[3] = ['fli kill', 10]
                    recovery_procedure[4] = ['fli start', 30]
                    # SOLUTION 4: Maybe the FLI hardware isn't powered on.
                    recovery_procedure[5] = ['power on cams,focs,filts', 30]
                    recovery_procedure[6] = ['fli kill', 10]
                    recovery_procedure[7] = ['fli start', 30]
                    # OUT OF SOLUTIONS: It might be the hardware isn't connected, e.g. USB failure.
                    return ERROR_DEPENDENCY + 'fli', recovery_procedure
            # OUT OF SOLUTIONS: We don't know where the dependency error is from?
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['cam start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['cam restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['cam kill', 10]
            recovery_procedure[4] = ['cam start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        elif ERROR_CAM_WARM in self.errors:
            # PROBLEM: The cameras aren't cool.
            recovery_procedure = {}
            # SOLUTION 1: Try setting the target temperature.
            #             Note we need to wait for a long time, assuming they're at room temp.
            recovery_procedure[1] = ['cam temp {}'.format(params.CCD_TEMP), 600]
            # OUT OF SOLUTIONS: Having trouble getting down to temperature,
            #                   Either it's a hardware issue or it's just too warm.
            return ERROR_CAM_WARM, recovery_procedure

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class FiltMonitor(BaseMonitor):
    """Hardware monitor for the filter wheel daemon."""

    def __init__(self, log=None):
        super().__init__('filt', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None or any([info[tel] is None for tel in params.TEL_DICT]):
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        all_homed = all([info[tel]['homed'] for tel in params.TEL_DICT])
        if not all_homed:
            hardware_status = STATUS_FILT_UNHOMED
        else:
            hardware_status = STATUS_ACTIVE

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # ERROR_FILT_UNHOMED
        # Set the error if the filter wheels aren't homed
        if self.hardware_status == STATUS_FILT_UNHOMED:
            self.add_error(ERROR_FILT_UNHOMED)
        # Clear the error if the filter wheels have been homed
        if self.hardware_status != STATUS_FILT_UNHOMED:
            self.clear_error(ERROR_FILT_UNHOMED)

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # The filt daemon doesn't directly talk to hardware, so this really shouldn't happen...
            return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The filt daemon depends on the FLI interfaces.
            for daemon_id in params.FLI_INTERFACES:
                if daemon_id in self.bad_dependencies:
                    # PROBLEM: The FLI interfaces aren't responding.
                    recovery_procedure = {}
                    # SOLUTION 1: Make sure the interfaces are started.
                    recovery_procedure[1] = ['fli start', 30]
                    # SOLUTION 2: Try restarting them.
                    recovery_procedure[2] = ['fli restart', 30]
                    # SOLUTION 3: Kill them, then start them again.
                    recovery_procedure[3] = ['fli kill', 10]
                    recovery_procedure[4] = ['fli start', 30]
                    # SOLUTION 4: Maybe the FLI hardware isn't powered on.
                    recovery_procedure[5] = ['power on cams,focs,filts', 30]
                    recovery_procedure[6] = ['fli kill', 10]
                    recovery_procedure[7] = ['fli start', 30]
                    # OUT OF SOLUTIONS: It might be the hardware isn't connected, e.g. USB failure.
                    return ERROR_DEPENDENCY + 'fli', recovery_procedure
            # OUT OF SOLUTIONS: We don't know where the dependency error is from?
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['filt start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['filt restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['filt kill', 10]
            recovery_procedure[4] = ['filt start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        elif ERROR_FILT_UNHOMED in self.errors:
            # PROBLEM: The filter wheels aren't homed.
            recovery_procedure = {}
            # SOLUTION 1: Try homing them.
            recovery_procedure[1] = ['filt home', 60]
            # SOLUTION 2: Still not homed? Try again.
            recovery_procedure[2] = ['filt home', 120]
            # OUT OF SOLUTIONS: Sounds like a hardware issue.
            return ERROR_FILT_UNHOMED, recovery_procedure

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class FocMonitor(BaseMonitor):
    """Hardware monitor for the focuser daemon."""

    def __init__(self, log=None):
        super().__init__('foc', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None or any([info[tel] is None for tel in params.TEL_DICT]):
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        hardware_status = STATUS_ACTIVE

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # The foc daemon doesn't directly talk to hardware, so this really shouldn't happen...
            return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The foc daemon depends on the FLI interfaces.
            for daemon_id in params.FLI_INTERFACES:
                if daemon_id in self.bad_dependencies:
                    # PROBLEM: The FLI interfaces aren't responding.
                    recovery_procedure = {}
                    # SOLUTION 1: Make sure the interfaces are started.
                    recovery_procedure[1] = ['fli start', 30]
                    # SOLUTION 2: Try restarting them.
                    recovery_procedure[2] = ['fli restart', 30]
                    # SOLUTION 3: Kill them, then start them again.
                    recovery_procedure[3] = ['fli kill', 10]
                    recovery_procedure[4] = ['fli start', 30]
                    # SOLUTION 4: Maybe the FLI hardware isn't powered on.
                    recovery_procedure[5] = ['power on cams,focs,filts', 30]
                    recovery_procedure[6] = ['fli kill', 10]
                    recovery_procedure[7] = ['fli start', 30]
                    # OUT OF SOLUTIONS: It might be the hardware isn't connected, e.g. USB failure.
                    return ERROR_DEPENDENCY + 'fli', recovery_procedure
            # OUT OF SOLUTIONS: We don't know where the dependency error is from?
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['foc start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['foc restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['foc kill', 10]
            recovery_procedure[4] = ['foc start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class ExqMonitor(BaseMonitor):
    """Hardware monitor for the exposure queue daemon."""

    def __init__(self, log=None):
        super().__init__('exq', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        hardware_status = STATUS_ACTIVE

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # The exq daemon doesn't directly talk to hardware, so this really shouldn't happen...
            return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The exq daemon depends on the FLI interfaces, cam and filt daemons.
            # Note that all being well the CamMonitor and FiltMonitor will be trying to fix
            # themselves too, but ideally the ExqMonitor should be standalone in case one of them
            # fails.
            for daemon_id in params.FLI_INTERFACES:
                if daemon_id in self.bad_dependencies:
                    # PROBLEM: The FLI interfaces aren't responding.
                    recovery_procedure = {}
                    # SOLUTION 1: Make sure the interfaces are started.
                    recovery_procedure[1] = ['fli start', 30]
                    # SOLUTION 2: Try restarting them.
                    recovery_procedure[2] = ['fli restart', 30]
                    # SOLUTION 3: Kill them, then start them again.
                    recovery_procedure[3] = ['fli kill', 10]
                    recovery_procedure[4] = ['fli start', 30]
                    # SOLUTION 4: Maybe the FLI hardware isn't powered on.
                    recovery_procedure[5] = ['power on cams,focs,filts', 30]
                    recovery_procedure[6] = ['fli kill', 10]
                    recovery_procedure[7] = ['fli start', 30]
                    # OUT OF SOLUTIONS: It might be the hardware isn't connected, e.g. USB failure.
                    return ERROR_DEPENDENCY + 'fli', recovery_procedure
            if 'cam' in self.bad_dependencies:
                # PROBLEM: Cam daemon is not responding or not returning info.
                recovery_procedure = {}
                # SOLUTION 1: Make sure it's started.
                recovery_procedure[1] = ['cam start', 30]
                # SOLUTION 2: Try restarting it.
                recovery_procedure[2] = ['cam restart', 30]
                # SOLUTION 3: Kill it, then start it again.
                recovery_procedure[3] = ['cam kill', 10]
                recovery_procedure[4] = ['cam start', 30]
                # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
                return ERROR_DEPENDENCY + 'cam', recovery_procedure
            elif 'filt' in self.bad_dependencies:
                # PROBLEM: Filt daemon is not responding or not returning info.
                recovery_procedure = {}
                # SOLUTION 1: Make sure it's started.
                recovery_procedure[1] = ['filt start', 30]
                # SOLUTION 2: Try restarting it.
                recovery_procedure[2] = ['filt restart', 30]
                # SOLUTION 3: Kill it, then start it again.
                recovery_procedure[3] = ['filt kill', 10]
                recovery_procedure[4] = ['filt start', 30]
                # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
                return ERROR_DEPENDENCY + 'filt', recovery_procedure
            # OUT OF SOLUTIONS: We don't know where the dependency error is from?
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['exq start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['exq restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['exq kill', 10]
            recovery_procedure[4] = ['exq start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class ConditionsMonitor(BaseMonitor):
    """Hardware monitor for the conditions daemon."""

    def __init__(self, log=None):
        super().__init__('conditions', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

        # Set a longer info timeout than default, as checks can take a while
        self.info_timeout = 30

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        internal_error = info['flags']['internal'] == 2
        if internal_error:
            hardware_status = STATUS_CONDITIONS_INTERNAL_ERROR
        else:
            hardware_status = STATUS_ACTIVE

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # ERROR_CONDITIONS_INTERNAL
        # Set the error if the internal flag is reporting status 2 (ERROR)
        if self.hardware_status == STATUS_CONDITIONS_INTERNAL_ERROR:
            self.add_error(ERROR_CONDITIONS_INTERNAL)
        # Clear the error if the flag is back to not ERROR (either good or bad)
        if self.hardware_status != STATUS_CONDITIONS_INTERNAL_ERROR:
            self.clear_error(ERROR_CONDITIONS_INTERNAL)

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # The conditions daemon doesn't raise hardware errors.
            return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The conditions daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['conditions start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['conditions restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['conditions kill', 10]
            recovery_procedure[4] = ['conditions start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        elif ERROR_CONDITIONS_INTERNAL in self.errors:
            # PROBLEM: The internal flag has been set to ERROR.
            recovery_procedure = {}
            # SOLUTION 1: Try rebooting the RoomAlert, through the PoE switch.
            recovery_procedure[1] = ['power reboot poe', 120]
            # SOLUTION 2: Try powering off for longer.
            recovery_procedure[2] = ['power off poe', 60]
            recovery_procedure[3] = ['power on poe', 120]
            # OUT OF SOLUTIONS: Maybe it's not the RoomAlert's fault.
            return ERROR_FILT_UNHOMED, recovery_procedure

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class SchedulerMonitor(BaseMonitor):
    """Hardware monitor for the scheduler daemon."""

    def __init__(self, log=None):
        super().__init__('scheduler', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

        # Set a longer info timeout than default, as checks can take a while
        self.info_timeout = 30

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        hardware_status = STATUS_ACTIVE

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # The scheduler daemon doesn't raise hardware errors.
            return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The scheduler daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['scheduler start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['scheduler restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['scheduler kill', 10]
            recovery_procedure[4] = ['scheduler start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}


class SentinelMonitor(BaseMonitor):
    """Hardware monitor for the sentinel daemon."""

    def __init__(self, log=None):
        super().__init__('sentinel', log)

        # Define modes and starting mode
        self.available_modes = [MODE_ACTIVE]
        self.mode = MODE_ACTIVE

    def get_hardware_status(self):
        """Get the current status of the hardware."""
        info = self.get_info()
        if info is None:
            self.hardware_status = STATUS_UNKNOWN
            return STATUS_UNKNOWN

        # no custom statuses
        hardware_status = STATUS_ACTIVE

        self.hardware_status = hardware_status
        return hardware_status

    def _check_hardware(self):
        """Check the hardware and report any detected errors."""
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, thank you. How are you?
            return None, {}

        elif ERROR_HARDWARE in self.errors:
            # The scheduler daemon doesn't raise hardware errors.
            return ERROR_HARDWARE, {}

        elif ERROR_DEPENDENCY in self.errors:
            # The scheduler daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        elif ERROR_RUNNING in self.errors or ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not running, or it is and it's not responding or returning info.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['sentinel start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['sentinel restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['sentinel kill', 10]
            recovery_procedure[4] = ['sentinel start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        elif ERROR_STATUS in self.errors:
            # PROBLEM: Daemon is in an unknown state.
            # OUT OF SOLUTIONS: We don't know what to do.
            return ERROR_STATUS, {}

        else:
            # Some unexpected error.
            return ERROR_UNKNOWN, {}
