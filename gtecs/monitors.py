"""Hardware wrappers for the pilot."""

import abc
import time

from . import params
from .daemons import daemon_function, daemon_info
from .misc import execute_command


class HardwareMonitor(object, metaclass=abc.ABCMeta):
    """Generic hardware monitor class.

    Inherited by specific classes for all actual hardware types. This is an abstract
    class and must be subtyped, implementing the `_check` method.

    Parameters
    ----------
    log: `logging.Logger`
        log object to direct output to

    """

    def __init__(self, log):
        self.log = log
        self.info = None
        self.last_successful_check = 0.
        self.recovery_level = 0
        self.mode = None
        self.available_modes = [None]
        self.recovery_procedure = {}
        self.daemon_id = None

    def get_info(self):
        """Get the daemon info dict."""
        info = None
        if self.daemon_id is not None:
            try:
                info = daemon_info(self.daemon_id)
                assert isinstance(info, dict)
            except Exception:
                info = None
        if info is not None:
            self.info = info
        return info

    def ping_daemon(self):
        """Ping a daemon - return 0 for alive and 1 for (maybe) dead."""
        if self.daemon_id is not None:
            try:
                ping = daemon_function(self.daemon_id, 'ping')
                assert ping == 'ping'
                return 0
            except Exception:
                return 1
        else:
            return 0

    def check(self, mode=None):
        """Check if hardware is OK.

        Parameters
        ----------
        mode : string
            allows different hardware states to be OK depending on observing mode

        Returns
        -------
        num_errors : int
            0 for OK, >0 for errors
        errors : list of string
            details of errors found

        """
        self.errors = []
        if self.ping_daemon() > 0:
            self.errors.append('Ping failed')

        inf = self.get_info()
        if inf is None:
            return 1, ['Get info failed']

        if mode is None:
            mode = self.mode
        self._check(mode)
        if len(self.errors) < 1:
            self.last_successful_check = time.time()
            self.recovery_level = 0
        return len(self.errors), self.errors

    @abc.abstractmethod
    def _check(self, mode=None):
        """Check the hardware by running through the recovery steps.

        This abstract method must be implemented by all hardware to add
        hardware specific checks.

        Parameters
        ----------
        mode : string, optional
            allows different hardware states to be OK depending on observing mode

        """
        return

    def recover(self):
        """Run recovery commands.

        Checks whether enough time has elapsed to progress to next stage of recovery.
        """
        downtime = time.time() - self.last_successful_check
        next_level = self.recovery_level + 1
        if next_level in self.recovery_procedure:
            if downtime > self.recovery_procedure[next_level][0]:
                for cmd in self.recovery_procedure[next_level][1:]:
                    self.log.info('Attempting recovery level %d: %s' % (next_level, cmd))
                    execute_command(cmd)
                self.recovery_level += 1
        else:
            return

    def set_mode(self, mode):
        """Set the hardware mode."""
        if mode in self.available_modes:
            self.mode = mode
            return 0
        else:
            return 1


class DomeMonitor(HardwareMonitor):
    """Hardware monitor for the dome daemon."""

    def __init__(self, log):
        # call parent init function
        super(DomeMonitor, self).__init__(log)
        self.daemon_id = 'dome'
        self.available_modes.extend(['open'])
        self.move_start_time = 0
        self.currently_moving = False

    def _check(self, mode=None):

        if mode == 'open':
            dome_fully_open = all(item == 'full_open' for item in (
                self.info['north'], self.info['south']
            ))
            if not dome_fully_open:
                if 'ing' in self.info['north'] or 'ing' in self.info['south']:
                    if not self.currently_moving:
                        self.currently_moving = True
                        self.move_start_time = time.time()
                    else:
                        if time.time() - self.move_start_time > 60:
                            self.errors.append('Opening taking too long')
                else:
                    self.errors.append('Dome not fully open')
            else:
                self.currently_moving = False
                self.move_start_time = 0

        elif mode is None:
            dome_fully_closed = self.info['dome'] == 'closed'
            if not dome_fully_closed:
                if 'ing' in self.info['north'] or 'ing' in self.info['south']:
                    if not self.currently_moving:
                        self.currently_moving = True
                        self.move_start_time = time.time()
                    else:
                        if time.time() - self.move_start_time > 60:
                            self.errors.append('Closing taking too long')
                else:
                    self.errors.append('Dome not closed')
            else:
                self.currently_moving = False
                self.move_start_time = 0

    def set_mode(self, mode):
        """Set the hardware mode."""
        val = super(DomeMonitor, self).set_mode(mode)
        if mode == 'open':
            # dome open commands may need repeating if cond change hasnt propogated
            self.recovery_procedure[1] = [30., 'dome open']
            self.recovery_procedure[2] = [120., 'dome close both 0.1']
            self.recovery_procedure[3] = [120., 'dome open']
            self.recovery_procedure[4] = [180., 'dome open']
            self.recovery_procedure[4] = [240., 'dome close']
            self.recovery_procedure[4] = [360., 'dome open']
        else:
            self.recovery_procedure = {}
        return val


class MountMonitor(HardwareMonitor):
    """Hardware monitor for the mount daemon."""

    def __init__(self, log):
        super(MountMonitor, self).__init__(log)
        self.daemon_id = 'mnt'
        self.available_modes.extend(['parked', 'tracking'])
        self.slew_start_time = 0
        self.currently_slewing = False
        self.off_target_start_time = 0
        self.currently_off_target = False

    def _check(self, mode=None):
        if mode == 'tracking':
            not_on_target = (self.info['target_dist'] is not None and
                             (float(self.info['target_dist']) > 0.003 or
                              self.info['status'] != 'Tracking'))
            if not_on_target:
                if self.info['status'] == 'Slewing':
                    if not self.currently_slewing:
                        self.currently_slewing = True
                        self.slew_start_time = time.time()
                    else:
                        if time.time() - self.slew_start_time > 100:
                            self.errors.append('Slew taking too long')
                else:
                    if not self.currently_off_target:
                        self.currently_off_target = True
                        self.off_target_start_time = time.time()
                    else:
                        if time.time() - self.off_target_start_time > 30:
                            self.errors.append('Not on target')
            else:
                self.currently_slewing = False
                self.slew_start_time = 0
                self.currently_off_target = False
                self.off_target_start_time = 0

        elif mode == 'parked' and params.FREEZE_DEC:
            if self.info['status'] != 'Stopped':
                self.errors.append('Not parked')

        elif mode == 'parked':
            if self.info['status'] not in ['Parked', 'Parking']:
                self.errors.append('Not parked')
        if self.info['status'] == 'Unknown':
            self.errors.append('Mount in error state')

    def set_mode(self, mode):
        """Set the hardware mode."""
        val = super(MountMonitor, self).set_mode(mode)
        if mode == 'tracking':
            self.recovery_procedure = {}
            self.recovery_procedure[1] = [60., 'mnt track']
            self.recovery_procedure[2] = [120., 'mnt slew']
            self.recovery_procedure[3] = [240., 'mnt track']
            self.recovery_procedure[4] = [270., 'mnt unpark']
            self.recovery_procedure[5] = [290., 'mnt track']
            self.recovery_procedure[6] = [320., 'mnt slew']
            self.recovery_procedure[7] = [320., 'mnt slew']
            self.recovery_procedure[8] = [360., 'mnt track']
        elif mode == 'parked':
            self.recovery_procedure = {}
            self.recovery_procedure[1] = [60., 'mnt stop']
            self.recovery_procedure[2] = [120., 'mnt park']
            self.recovery_procedure[3] = [180., 'mnt unpark']
            self.recovery_procedure[4] = [240., 'mnt stop']
            self.recovery_procedure[4] = [360., 'mnt park']
        else:
            self.recovery_procedure = {}
            self.recovery_procedure[1] = [60., 'mnt stop']
            self.recovery_procedure[2] = [120., 'mnt stop']
            self.recovery_procedure[3] = [180., 'mnt stop']
            self.recovery_procedure[4] = [360., 'mnt stop']
        return val


class CameraMonitor(HardwareMonitor):
    """Hardware monitor for the camera daemon."""

    def __init__(self, log):
        super(CameraMonitor, self).__init__(log)
        self.daemon_id = 'cam'
        self.available_modes.extend(['science'])
        self.recovery_procedure[1] = [60., 'cam start']
        self.recovery_procedure[2] = [120., 'cam kill']
        self.recovery_procedure[3] = [130., 'cam start']

    def _check(self, mode=None):
        # no custom checks as yet
        return


class FilterWheelMonitor(HardwareMonitor):
    """Hardware monitor for the filter wheel daemon."""

    def __init__(self, log):
        super(FilterWheelMonitor, self).__init__(log)
        self.daemon_id = 'filt'
        self.available_modes.extend(['science'])
        self.recovery_procedure[1] = [60., 'filt start']
        self.recovery_procedure[2] = [120., 'filt kill']
        self.recovery_procedure[3] = [130., 'filt start']

    def _check(self, mode=None):
        # no custom checks as yet
        return


class ExposureQueueMonitor(HardwareMonitor):
    """Hardware monitor for the exposure queue daemon."""

    def __init__(self, log):
        super(ExposureQueueMonitor, self).__init__(log)
        self.daemon_id = 'exq'
        self.available_modes.extend(['science'])
        self.recovery_procedure[1] = [60., 'exq start']
        self.recovery_procedure[2] = [120., 'exq kill']
        self.recovery_procedure[3] = [130., 'exq start']

    def _check(self, mode=None):
        # no custom checks as yet
        return


class FocuserMonitor(HardwareMonitor):
    """Hardware monitor for the focuser daemon."""

    def __init__(self, log):
        super(FocuserMonitor, self).__init__(log)
        self.daemon_id = 'foc'
        self.available_modes.extend(['science'])
        self.recovery_procedure[1] = [60., 'foc start']
        self.recovery_procedure[2] = [120., 'foc kill']
        self.recovery_procedure[3] = [130., 'foc start']

    def _check(self, mode=None):
        # no custom checks as yet
        return
