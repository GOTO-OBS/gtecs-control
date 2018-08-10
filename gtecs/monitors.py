"""Daemon monitor classes for the pilot."""

import time
from abc import ABC, abstractmethod

from .daemons import daemon_info, daemon_is_alive, dependencies_are_alive
from .misc import execute_command
from .slack import send_slack_msg


# Daemon statuses
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

        self.errors = set([])
        self.active_error = None
        self.recovery_level = 0
        self.last_successful_check = 0.
        self.last_recovery_command = 0.

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
        # Note these overwrite self.errors instead of adding to it, because they're critical
        if not self.dependencies_are_alive():
            self.errors = set([ERROR_DEPENDENCY])
            return len(self.errors), self.errors

        if not self.is_alive:
            self.errors = set([ERROR_PING])
            return len(self.errors), self.errors

        info = self.get_info()
        if info is None:
            self.errors = set([ERROR_INFO])
            return len(self.errors), self.errors

        status = self.get_status()
        if status is STATUS_UNKNOWN:
            self.errors = set([ERROR_UNKNOWN])
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

        if self.recovery_level == 0 and 'delay' in recovery_procedure:
            # Sometimes you don't want to start recovery immediately, give it time to fix itself.
            downtime = time.time() - self.last_successful_check
            delay = recovery_procedure['delay']
            if downtime < delay:
                return

        elif self.recovery_level != 0:
            # Each command has a time to wait until progressing to the next level
            waittime = time.time() - self.last_recovery_command
            wait = recovery_procedure[self.recovery_level][1]
            if waittime < wait:
                return

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

        command = recovery_procedure[next_level][0]
        msg = '{} attempting recovery '.format(self.__class__.__name__)
        msg += 'level {:.0f}: {}'.format(next_level, command)
        if self.log:
            self.log.info(msg)
        else:
            print(msg)
        execute_command(command)
        self.last_recovery_command = time.time()
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
        lockdown = info['lockdown']

        if lockdown:
            status = STATUS_DOME_LOCKDOWN
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
                    self.errors.add(ERROR_DOME_MOVETIMEOUT)
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
                    self.errors.add(ERROR_DOME_PARTOPENTIMEOUT)
        else:
            self._currently_part_open = False
            self._part_open_start_time = 0

        if self.mode == MODE_DOME_OPEN and self.status != STATUS_DOME_FULLOPEN:
            self.errors.add(ERROR_DOME_NOTFULLOPEN)

        if self.mode == MODE_DOME_CLOSED and self.status not in [STATUS_DOME_CLOSED,
                                                                 STATUS_DOME_LOCKDOWN]:
            self.errors.add(ERROR_DOME_NOTCLOSED)

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, nothing to do!
            return None, {}

        if ERROR_DEPENDENCY in self.errors:
            # The dome daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        if ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not responding or not returning info.
            recovery_procedure = {'delay': 30}
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

        if ERROR_UNKNOWN in self.errors:
            # We don't know what to do.
            return ERROR_UNKNOWN, {}

        if ERROR_DOME_MOVETIMEOUT in self.errors:
            # PROBLEM: The dome has been moving for too long.
            #          No delay, because this is only raised after a timeout period already.
            recovery_procedure = {}
            # SOLUTION 1: Stop immediately!
            recovery_procedure[1] = ['dome halt', 30]
            # SOLUTION 2: Still moving? Okay, kill the dome daemon.
            recovery_procedure[2] = ['dome kill', 30]
            # OUT OF SOLUTIONS: How can it still be moving??
            return ERROR_DOME_MOVETIMEOUT, recovery_procedure

        if ERROR_DOME_NOTCLOSED in self.errors:
            # PROBLEM: The dome's not closed when it should be. That's bad.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Try closing again.
            recovery_procedure[1] = ['dome close', 90]
            # OUT OF SOLUTIONS: We can't close, panic! Send out the alert.
            return ERROR_DOME_NOTCLOSED, recovery_procedure

        if ERROR_DOME_PARTOPENTIMEOUT in self.errors:
            # PROBLEM: The dome has been partially open for too long.
            #          Note the dome can naturally stick partially open in the middle of moving
            #          for a while (i.e. when it's sounding the siren to move the second side).
            #          This is for when it's been too long like that, such as when the Honeywell
            #          switches fail to catch.
            #          No delay, because this is only raised after a timeout period already.
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

        if ERROR_DOME_NOTFULLOPEN in self.errors:
            # PROBLEM: The dome should be open, but it's closed (part_open is caught above).
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Try opening a few times.
            recovery_procedure[1] = ['dome open', 90]
            recovery_procedure[2] = ['dome open', 90]
            recovery_procedure[3] = ['dome open', 90]
            # OUT OF SOLUTIONS: It's not opening, either it's stuck or it's in lockdown and the
            #                   pilot hasn't realised yet. At least it's safe.
            return ERROR_DOME_NOTFULLOPEN, recovery_procedure


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
                    self.errors.add(ERROR_MNT_MOVETIMEOUT)
        else:
            self._currently_moving = False
            self._move_start_time = 0

        if self.status == STATUS_MNT_OFFTARGET:
            if not self._currently_off_target:
                self._currently_off_target = True
                self._off_target_start_time = time.time()
            else:
                if time.time() - self._off_target_start_time > 30:
                    self.errors.add(ERROR_MNT_NOTONTARGET)

        if self.status == STATUS_MNT_BLINKY:
            self.errors.add(ERROR_MNT_INBLINKY)

        if self.mode == MODE_MNT_TRACKING and self.status == STATUS_MNT_STOPPED:
            self.errors.add(ERROR_MNT_NOTONTARGET)

        if self.mode == MODE_MNT_TRACKING and self.status == STATUS_MNT_PARKED:
            self.errors.add(ERROR_MNT_NOTONTARGET)

        if (self.mode == MODE_MNT_PARKED and
                self.status not in [STATUS_MNT_PARKED, STATUS_MNT_MOVING]):
            self.errors.add(ERROR_MNT_NOTPARKED)

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, nothing to do!
            return None, {}

        if ERROR_DEPENDENCY in self.errors:
            # The mount daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        if ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not responding or not returning info.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['mnt start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['mnt restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['mnt kill', 10]
            recovery_procedure[4] = ['mnt start', 30]
            # SOLUTION 4: Maybe there's a problem with the mount.
            recovery_procedure[5] = ['power off mount_nuc', 10]
            recovery_procedure[6] = ['power off sitech', 10]
            recovery_procedure[7] = ['power on sitech', 60]
            recovery_procedure[8] = ['power on mount_nuc', 180]
            # SOLUTION 5: Try restarting the daemon again.
            recovery_procedure[9] = ['mount kill', 10]
            recovery_procedure[10] = ['mount start', 60]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        if ERROR_UNKNOWN in self.errors:
            # We don't know what to do.
            return ERROR_UNKNOWN, {}

        if ERROR_MNT_INBLINKY in self.errors:
            # PROBLEM: The mount is in blinky mode.
            #          Maybe it's been tracking for too long and reached the limit,
            #          or there's been some voltage problem.
            #          No delay, if it's in blinky mode it's not going to fix itself.
            recovery_procedure = {}
            # SOLUTION 1: Try turning blinky mode off.
            recovery_procedure[1] = ['mnt blinky off', 60]
            # SOLUTION 2: Maybe there's a problem with the mount.
            recovery_procedure[2] = ['power off mount_nuc', 10]
            recovery_procedure[3] = ['power off sitech', 10]
            recovery_procedure[4] = ['power on sitech', 60]
            recovery_procedure[5] = ['power on mount_nuc', 180]
            # SOLUTION 3: Restart the daemon.
            recovery_procedure[6] = ['mount restart', 10]
            # OUT OF SOLUTIONS: It's still in blinky mode, sounds like a hardware issue.
            return ERROR_MNT_INBLINKY, recovery_procedure

        if ERROR_MNT_MOVETIMEOUT in self.errors:
            # PROBLEM: The mount has reported it's been moving for too long.
            #          No delay, because this is only raised after a timeout period already.
            recovery_procedure = {}
            # SOLUTION 1: Stop immediately!
            recovery_procedure[1] = ['mnt stop', 30]
            # SOLUTION 2: Still moving? Okay, kill the mnt daemon.
            recovery_procedure[2] = ['mnt kill', 30]
            # OUT OF SOLUTIONS: How can it still be moving??
            return ERROR_MNT_MOVETIMEOUT, recovery_procedure

        if ERROR_MNT_NOTONTARGET in self.errors:
            # PROBLEM: The mount is in tracking mode and has a target, but it's not on target.
            recovery_procedure['delay'] = 60
            # SOLUTION 1: Try slewing to the target, this should start tracking too.
            recovery_procedure[1] = ['mnt slew', 60]
            # SOLUTION 2: Maybe we're parked?
            recovery_procedure[2] = ['mnt unpark', 60]
            recovery_procedure[3] = ['mnt slew', 60]
            # SOLUTION 4: It should start tracking when it reaches the target, but just in case.
            recovery_procedure[4] = ['mnt track', 30]
            # OUT OF SOLUTIONS: It can't reach the target for some reason.
            return ERROR_MNT_NOTONTARGET, recovery_procedure

        if ERROR_MNT_NOTPARKED in self.errors:
            # PROBLEM: The mount is in parked mode but it isn't parked.
            recovery_procedure['delay'] = 60
            # SOLUTION 1: Try parking.
            recovery_procedure[1] = ['mnt park', 120]
            # SOLUTION 2: Try again.
            recovery_procedure[2] = ['mnt unpark', 30]
            recovery_procedure[3] = ['mnt park', 120]
            # OUT OF SOLUTIONS: There must be a problem, maybe the park position isn't defined.
            return ERROR_MNT_NOTPARKED, recovery_procedure


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
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, nothing to do!
            return None, {}

        if ERROR_DEPENDENCY in self.errors:
            # The power daemon doesn't have dependencies, so this really shouldn't happen...
            return ERROR_DEPENDENCY, {}

        if ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not responding or not returning info.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['power start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['power restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['power kill', 10]
            recovery_procedure[4] = ['power start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        if ERROR_UNKNOWN in self.errors:
            # We don't know what to do.
            return ERROR_UNKNOWN, {}


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
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, nothing to do!
            return None, {}

        if ERROR_DEPENDENCY in self.errors:
            # The cam daemon depends on the FLI interfaces.
            # PROBLEM: The FLI interfaces aren't responding.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Makre sure the interfaces are started.
            recovery_procedure[1] = ['fli start', 30]
            # SOLUTION 2: Try restarting them.
            recovery_procedure[2] = ['fli restart', 30]
            # SOLUTION 3: Kill them, then start them again.
            recovery_procedure[3] = ['fli kill', 10]
            recovery_procedure[4] = ['fli start', 30]
            # SOLUTION 4: Maybe the FLI hardware isn't powered on.
            recovery_procedure[5] = ['power start cams,focs,filts', 30]
            recovery_procedure[6] = ['fli kill', 10]
            recovery_procedure[7] = ['fli start', 30]
            # OUT OF SOLUTIONS: It might be the hardware isn't connected, e.g. USB failure.
            return ERROR_DEPENDENCY, {}

        if ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not responding or not returning info.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['cam start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['cam restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['cam kill', 10]
            recovery_procedure[4] = ['cam start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        if ERROR_UNKNOWN in self.errors:
            # We don't know what to do.
            return ERROR_UNKNOWN, {}


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
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, nothing to do!
            return None, {}

        if ERROR_DEPENDENCY in self.errors:
            # The filt daemon depends on the FLI interfaces.
            # PROBLEM: The FLI interfaces aren't responding.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Makre sure the interfaces are started.
            recovery_procedure[1] = ['fli start', 30]
            # SOLUTION 2: Try restarting them.
            recovery_procedure[2] = ['fli restart', 30]
            # SOLUTION 3: Kill them, then start them again.
            recovery_procedure[3] = ['fli kill', 10]
            recovery_procedure[4] = ['fli start', 30]
            # SOLUTION 4: Maybe the FLI hardware isn't powered on.
            recovery_procedure[5] = ['power start cams,focs,filts', 30]
            recovery_procedure[6] = ['fli kill', 10]
            recovery_procedure[7] = ['fli start', 30]
            # OUT OF SOLUTIONS: It might be the hardware isn't connected, e.g. USB failure.
            return ERROR_DEPENDENCY, {}

        if ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not responding or not returning info.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['filt start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['filt restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['filt kill', 10]
            recovery_procedure[4] = ['filt start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        if ERROR_UNKNOWN in self.errors:
            # We don't know what to do.
            return ERROR_UNKNOWN, {}


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
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, nothing to do!
            return None, {}

        if ERROR_DEPENDENCY in self.errors:
            # The foc daemon depends on the FLI interfaces.
            # PROBLEM: The FLI interfaces aren't responding.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Makre sure the interfaces are started.
            recovery_procedure[1] = ['fli start', 30]
            # SOLUTION 2: Try restarting them.
            recovery_procedure[2] = ['fli restart', 30]
            # SOLUTION 3: Kill them, then start them again.
            recovery_procedure[3] = ['fli kill', 10]
            recovery_procedure[4] = ['fli start', 30]
            # SOLUTION 4: Maybe the FLI hardware isn't powered on.
            recovery_procedure[5] = ['power start cams,focs,filts', 30]
            recovery_procedure[6] = ['fli kill', 10]
            recovery_procedure[7] = ['fli start', 30]
            # OUT OF SOLUTIONS: It might be the hardware isn't connected, e.g. USB failure.
            return ERROR_DEPENDENCY, {}

        if ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not responding or not returning info.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['foc start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['foc restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['foc kill', 10]
            recovery_procedure[4] = ['foc start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        if ERROR_UNKNOWN in self.errors:
            # We don't know what to do.
            return ERROR_UNKNOWN, {}


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
        # no custom errors
        return

    def _recovery_procedure(self):
        """Get the recovery commands for the current error(s), based on hardware status and mode."""
        if not self.errors:
            # Everything's fine, nothing to do!
            return None, {}

        if ERROR_DEPENDENCY in self.errors:
            # The exq daemon depends on the FLI interfaces, cam and filt daemons.
            # Note that all being well the CamMonitor and FiltMonitor will be trying to fix
            # themselves too, but ideally the ExqMonitor should be standalone in case one of them
            # fails.
            # PROBLEM: Some combination of the above aren't responding.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Make sure the interfaces are started.
            recovery_procedure[1] = ['fli start', 30]
            # SOLUTION 2: Make sure the daemons are started.
            recovery_procedure[2] = ['cam start', 10]
            recovery_procedure[3] = ['filt start', 10]
            # SOLUTION 2: Try restarting the interfaces.
            recovery_procedure[4] = ['fli restart', 30]
            # SOLUTION 2: Try restarting the daemons.
            recovery_procedure[5] = ['cam restart', 30]
            recovery_procedure[6] = ['filt restart', 30]
            # SOLUTION 3: Kill them, then start them again.
            recovery_procedure[7] = ['fli kill', 10]
            recovery_procedure[8] = ['fli start', 30]
            recovery_procedure[9] = ['cam kill', 10]
            recovery_procedure[10] = ['cam start', 30]
            recovery_procedure[11] = ['filt kill', 10]
            recovery_procedure[12] = ['filt start', 30]
            # SOLUTION 4: Maybe the FLI hardware isn't powered on.
            recovery_procedure[13] = ['power start cams,focs,filts', 30]
            recovery_procedure[14] = ['fli kill', 10]
            recovery_procedure[15] = ['fli start', 30]
            # OUT OF SOLUTIONS: It might be the hardware isn't connected, e.g. USB failure.
            return ERROR_DEPENDENCY, {}

        if ERROR_PING in self.errors or ERROR_INFO in self.errors:
            # PROBLEM: Daemon is not responding or not returning info.
            recovery_procedure = {'delay': 30}
            # SOLUTION 1: Make sure it's started.
            recovery_procedure[1] = ['cam start', 30]
            # SOLUTION 2: Try restarting it.
            recovery_procedure[2] = ['cam restart', 30]
            # SOLUTION 3: Kill it, then start it again.
            recovery_procedure[3] = ['cam kill', 10]
            recovery_procedure[4] = ['cam start', 30]
            # OUT OF SOLUTIONS: There must be something wrong that we can't fix here.
            return ERROR_PING + ERROR_INFO, recovery_procedure

        if ERROR_UNKNOWN in self.errors:
            # We don't know what to do.
            return ERROR_UNKNOWN, {}
