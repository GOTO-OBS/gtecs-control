"""Daemon monitor classes for the pilot."""

import time
from abc import ABC, abstractmethod

from . import params
from .daemons import daemon_info, daemon_is_alive
from .misc import execute_command


class BaseMonitor(ABC):
    """Generic monitor class, inherited by specific classes for each daemon.

    This is an abstract class and must be subtyped, implementing the _check_hardware` and
    `_recovery_procedure` methods.

    Parameters
    ----------
    log: `logging.Logger`
        log object to direct output to

    """

    def __init__(self, daemon_id, log=None):
        self.daemon_id = daemon_id
        self.log = log

        self.info = None

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

    def _check_hardware(self):
        """Check the hardware by running through the recovery steps."""
        if self.mode == 'open':
            dome_fully_open = all(item == 'full_open' for item in (
                self.info['north'], self.info['south']
            ))
            if not dome_fully_open:
                if 'ing' in self.info['north'] or 'ing' in self.info['south']:
                    if not self._currently_moving:
                        self._currently_moving = True
                        self._move_start_time = time.time()
                    else:
                        if time.time() - self._move_start_time > 60:
                            self.errors.append('Opening taking too long')
                else:
                    self.errors.append('Dome not fully open')
            else:
                self._currently_moving = False
                self._move_start_time = 0

        elif self.mode == 'closed':
            dome_fully_closed = self.info['dome'] == 'closed'
            if not dome_fully_closed:
                if 'ing' in self.info['north'] or 'ing' in self.info['south']:
                    if not self._currently_moving:
                        self._currently_moving = True
                        self._move_start_time = time.time()
                    else:
                        if time.time() - self._move_start_time > 60:
                            self.errors.append('Closing taking too long')
                else:
                    self.errors.append('Dome not closed')
            else:
                self._currently_moving = False
                self._move_start_time = 0

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
        self._slew_start_time = 0
        self._currently_slewing = False
        self._off_target_start_time = 0
        self._currently_off_target = False

    def _check_hardware(self):
        """Check the hardware by running through the recovery steps."""
        if self.mode == 'tracking':
            not_on_target = (self.info['target_dist'] is not None and
                             (float(self.info['target_dist']) > 0.003 or
                              self.info['status'] != 'Tracking'))
            if not_on_target:
                if self.info['status'] == 'Slewing':
                    if not self._currently_slewing:
                        self._currently_slewing = True
                        self._slew_start_time = time.time()
                    else:
                        if time.time() - self._slew_start_time > 100:
                            self.errors.append('Slew taking too long')
                else:
                    if not self._currently_off_target:
                        self._currently_off_target = True
                        self._off_target_start_time = time.time()
                    else:
                        if time.time() - self._off_target_start_time > 30:
                            self.errors.append('Not on target')
            else:
                self._currently_slewing = False
                self._slew_start_time = 0
                self._currently_off_target = False
                self._off_target_start_time = 0

        elif self.mode == 'parked' and params.FREEZE_DEC:
            if self.info['status'] != 'Stopped':
                self.errors.append('Not parked')

        elif self.mode == 'parked':
            if self.info['status'] not in ['Parked', 'Parking']:
                self.errors.append('Not parked')

        if self.info['status'] == 'Unknown':
            self.errors.append('Mount in error state')

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
        self.available_modes = ['running']
        self.set_mode('running')

    def _check_hardware(self):
        """Check the hardware by running through the recovery steps."""
        # no custom checks as yet
        return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'running':
            recovery_procedure[1] = [60., 'power start']
            recovery_procedure[2] = [120., 'power kill']
            recovery_procedure[3] = [130., 'power start']

        return recovery_procedure


class CamMonitor(BaseMonitor):
    """Hardware monitor for the camera daemon."""

    def __init__(self, log=None):
        super().__init__('cam', log)

        # Define modes and starting mode
        self.available_modes = ['science']
        self.set_mode('science')

    def _check_hardware(self):
        """Check the hardware by running through the recovery steps."""
        # no custom checks as yet
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
        self.available_modes = ['science']
        self.set_mode('science')

    def _check_hardware(self):
        """Check the hardware by running through the recovery steps."""
        # no custom checks as yet
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
        self.available_modes = ['science']
        self.set_mode('science')

    def _check_hardware(self):
        """Check the hardware by running through the recovery steps."""
        # no custom checks as yet
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
        self.available_modes = ['science']
        self.set_mode('science')

    def _check_hardware(self):
        """Check the hardware by running through the recovery steps."""
        # no custom checks as yet
        return

    def _recovery_procedure(self):
        """Get the recovery commands based on the current observing mode."""
        recovery_procedure = {}

        if self.mode == 'science':
            recovery_procedure[1] = [60., 'exq start']
            recovery_procedure[2] = [120., 'exq kill']
            recovery_procedure[3] = [130., 'exq start']

        return recovery_procedure
