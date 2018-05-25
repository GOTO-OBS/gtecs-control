"""
Hardware wrappers for the pilot
"""

import Pyro4
import time
import abc

from . import params
from .misc import execute_command
from .daemons import daemon_info, daemon_function


class HardwareMonitor:

    __metaclass__ = abc.ABCMeta

    def __init__(self, log):
        """

        Generic hardware monitor class.

        Inherited by specific classes for all actual hardware types. This is an abstract
        class and must be subtyped, implementing the `_check` method.

        Parameters
        ----------
        log: `logging.Logger`
            log object to direct output to

        """
        self.log = log
        self.info = None
        self.lastSuccessfulCheck = 0.
        self.recoveryLevel = 0
        self.mode = None
        self.availableModes = [None]
        self.recoveryProcedure = {}
        self.daemonID = None

    def getInfo(self):
        inf = None
        if self.daemonID is not None:
            try:
                inf = daemon_info(self.daemonID)
                assert isinstance(inf, dict)
            except:
                inf = None
        if inf is not None:
            self.info = inf
        return inf

    def pingDaemon(self):
        """Ping a daemon - return 0 for alive and 1 for (maybe) dead"""
        if self.daemonID is not None:
            try:
                ping = daemon_function(self.daemonID, 'ping')
                assert ping == 'ping'
                return 0
            except:
                return 1
        else:
            return 0

    def check(self, obsMode=None):
        """
        Check if hardware is OK

        Parameters
        ----------
        obsMode : string
            allows different hardware states to be OK depending on observing mode

        Returns
        -------
        num_errors : int
            0 for OK, >0 for errors
        errors : list of string
            details of errors found
        """
        self.errors = []
        if self.pingDaemon() > 0:
            self.errors.append('Ping failed')

        inf = self.getInfo()
        if inf is None:
            return 1, ['Get info failed']

        if obsMode is None:
            obsMode = self.mode
        self._check(obsMode=obsMode)
        if len(self.errors) < 1:
            self.lastSuccessfulCheck = time.time()
            self.recoveryLevel = 0
        return len(self.errors), self.errors

    @abc.abstractmethod
    def _check(self, obsMode=None):
        """
        Custom hardware checks.

        This abstract method must be implemented by all hardware to add
        hardware specific checks.

        Parameters
        ----------
        obsMode : string, optional
            allows different hardware states to be OK depending on observing mode
        """
        return

    def recover(self):
        """
        Recovery procedure.

        Checks whether enough time has elapsed to progress to next stage of recovery.
        """
        downtime = time.time() - self.lastSuccessfulCheck
        nextLevel = self.recoveryLevel + 1
        if nextLevel in self.recoveryProcedure:
            if downtime > self.recoveryProcedure[nextLevel][0]:
                for cmd in self.recoveryProcedure[nextLevel][1:]:
                    self.log.info('Attempting recovery level %d: %s' % (nextLevel, cmd))
                    execute_command(cmd)
                self.recoveryLevel += 1
        else:
            return

    def setMode(self, mode):
        if mode in self.availableModes:
            self.mode = mode
            return 0
        else:
            return 1


class DomeMonitor(HardwareMonitor):

    def __init__(self, log):
        # call parent init function
        super(DomeMonitor, self).__init__(log)
        self.daemonID = 'dome'
        self.availableModes.extend(['open'])
        self.move_start_time = 0
        self.currently_moving = False

    def _check(self, obsMode=None):

        if obsMode == 'open':
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

        elif obsMode is None:
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

    def setMode(self, mode):
        val = super(DomeMonitor, self).setMode(mode)
        if mode == 'open':
            # dome open commands may need repeating if cond change hasnt propogated
            self.recoveryProcedure[1] = [30., 'dome open']
            self.recoveryProcedure[2] = [120., 'dome close both 0.1']
            self.recoveryProcedure[3] = [120., 'dome open']
            self.recoveryProcedure[4] = [180., 'dome open']
            self.recoveryProcedure[4] = [240., 'dome close']
            self.recoveryProcedure[4] = [360., 'dome open']
        else:
            self.recoveryProcedure = {}
        return val


class MountMonitor(HardwareMonitor):

    def __init__(self, log):
        super(MountMonitor, self).__init__(log)
        self.daemonID = 'mnt'
        self.availableModes.extend(['parked', 'tracking'])
        self.slew_start_time = 0
        self.currently_slewing = False
        self.off_target_start_time = 0
        self.currently_off_target = False

    def _check(self, obsMode=None):
        if obsMode == 'tracking':
            not_on_target = (self.info['target_dist'] is not None and
                             (float(self.info['target_dist']) > 0.003 or self.info['status'] != 'Tracking'))
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

        elif obsMode == 'parked' and params.FREEZE_DEC:
            if self.info['status'] != 'Stopped':
                self.errors.append('Not parked')
        elif obsMode == 'parked':
            if self.info['status'] not in ['Parked', 'Parking']:
                self.errors.append('Not parked')
        if self.info['status'] == 'Unknown':
            self.errors.append('Mount in error state')

    def setMode(self, mode):
        val = super(MountMonitor, self).setMode(mode)
        if mode == 'tracking':
            self.recoveryProcedure = {}
            self.recoveryProcedure[1] = [60., 'mnt track']
            self.recoveryProcedure[2] = [120., 'mnt slew']
            self.recoveryProcedure[3] = [240., 'mnt track']
            self.recoveryProcedure[4] = [270., 'mnt unpark']
            self.recoveryProcedure[5] = [290., 'mnt track']
            self.recoveryProcedure[6] = [320., 'mnt slew']
            self.recoveryProcedure[7] = [320., 'mnt slew']
            self.recoveryProcedure[8] = [360., 'mnt track']
        elif mode == 'parked':
            self.recoveryProcedure = {}
            self.recoveryProcedure[1] = [60., 'mnt stop']
            self.recoveryProcedure[2] = [120., 'mnt park']
            self.recoveryProcedure[3] = [180., 'mnt unpark']
            self.recoveryProcedure[4] = [240., 'mnt stop']
            self.recoveryProcedure[4] = [360., 'mnt park']
        else:
            self.recoveryProcedure = {}
            self.recoveryProcedure[1] = [60., 'mnt stop']
            self.recoveryProcedure[2] = [120., 'mnt stop']
            self.recoveryProcedure[3] = [180., 'mnt stop']
            self.recoveryProcedure[4] = [360., 'mnt stop']
        return val


class CameraMonitor(HardwareMonitor):

    def __init__(self, log):
        super(CameraMonitor, self).__init__(log)
        self.daemonID = 'cam'
        self.availableModes.extend(['science'])
        self.recoveryProcedure[1] = [60., 'cam start']
        self.recoveryProcedure[2] = [120., 'cam kill']
        self.recoveryProcedure[3] = [130., 'cam start']

    def _check(self, obsMode=None):
        # no custom checks as yet
        return


class FilterWheelMonitor(HardwareMonitor):

    def __init__(self, log):
        super(FilterWheelMonitor, self).__init__(log)
        self.daemonID = 'filt'
        self.availableModes.extend(['science'])
        self.recoveryProcedure[1] = [60., 'filt start']
        self.recoveryProcedure[2] = [120., 'filt kill']
        self.recoveryProcedure[3] = [130., 'filt start']

    def _check(self, obsMode=None):
        # no custom checks as yet
        return


class ExposureQueueMonitor(HardwareMonitor):

    def __init__(self, log):
        super(ExposureQueueMonitor, self).__init__(log)
        self.daemonID = 'exq'
        self.availableModes.extend(['science'])
        self.recoveryProcedure[1] = [60., 'exq start']
        self.recoveryProcedure[2] = [120., 'exq kill']
        self.recoveryProcedure[3] = [130., 'exq start']

    def _check(self, obsMode=None):
        # no custom checks as yet
        return


class FocuserMonitor(HardwareMonitor):

    def __init__(self, log):
        super(FocuserMonitor, self).__init__(log)
        self.daemonID = 'foc'
        self.availableModes.extend(['science'])
        self.recoveryProcedure[1] = [60., 'foc start']
        self.recoveryProcedure[2] = [120., 'foc kill']
        self.recoveryProcedure[3] = [130., 'foc start']

    def _check(self, obsMode=None):
        # no custom checks as yet
        return
