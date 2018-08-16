"""Classes to read external flag files."""

import copy
import json
import os
import time

from astropy.time import Time

from . import params
from .hardware.power import APCUPS
from .slack import send_slack_msg


def load_json(fname):
    """Attempt to load a JSON file, with multiple tries."""
    attempts_remaining = 3
    while attempts_remaining:
        try:
            with open(fname, 'r') as fh:
                data_dict = json.load(fh)
            assert len(data_dict) != 0
            break
        except Exception:
            time.sleep(0.001)
            attempts_remaining -= 1
            pass
    if attempts_remaining:
        return data_dict
    else:
        raise IOError('cannot read {}'.format(fname))


class Power(object):
    """A class to monitor the UPS status."""

    def __init__(self):
        self.ups_units = {}
        for unit_name in params.POWER_UNITS:
            unit_class = params.POWER_UNITS[unit_name]['CLASS']
            unit_ip = params.POWER_UNITS[unit_name]['IP']
            if unit_class == 'APCUPS':
                self.ups_units[unit_name] = APCUPS(unit_ip)

    @property
    def failed(self):
        """Return True if any power supplies have failed and UPS has kicked in."""
        acceptable_status_vals = ['Normal', 'onBatteryTest']
        return any([self.ups_units[ukey].status() not in acceptable_status_vals
                    for ukey in self.ups_units])

    def __repr__(self):
        class_name = type(self).__name__
        repr_str = ', '.join(['='.join((x, self.ups_units[x].status())) for x in self.ups_units])
        return '{}({})'.format(class_name, repr_str)


class Conditions(object):
    """A class to give easy access to the conditions flags."""

    def __init__(self):
        conditions_dict = load_json(params.CONFIG_PATH + 'conditions_flags')

        # store update time and remove from dictionary
        update_time = int(Time(conditions_dict['update_time']).unix)
        del conditions_dict['update_time']

        # set Condtions properties (which are stored in __dict__)
        # to the values in the dictionary
        self.__dict__ = copy.copy(conditions_dict)

        # add the update time
        self.update_time = update_time

        # store a summary of all flags, excluding dark
        self._summary = 0
        self._bad_flags = []
        for key, value in conditions_dict.items():
            if key != 'dark':
                self._summary += value
                if value:
                    self._bad_flags += [key]
        self.bad_flags = ', '.join(self._bad_flags)

        # and store a separate summary of critical flags
        self._crit_sum = 0
        self._crit_flags = []
        for key, value in conditions_dict.items():
            if key in ['diskspace', 'low_battery', 'internal', 'ice']:
                self._crit_sum += value
                if value:
                    self._crit_flags += [key]
            status = Status()
            if key == 'hatch' and status.mode == 'robotic':
                self._crit_sum += value
                if value:
                    self._crit_flags += [key]
        self.critical_flags = ', '.join(self._crit_flags)

    def age(self):
        """Get the age of the conditions."""
        return int(Time.now().unix - self.update_time)

    def __repr__(self):
        class_name = type(self).__name__
        repr_str = ', '.join(['='.join((k, str(v))) for k, v in self.__dict__.items()])
        return '{}({})'.format(class_name, repr_str)

    @property
    def bad(self):
        """Check if these conditions are bad.

        Uses summary of conditions and age check
        """
        if self.age() > params.MAX_CONDITIONS_AGE:
            tooold = 1
        else:
            tooold = 0
        return self._summary + tooold

    @property
    def critical(self):
        """Check if any of the critical flags are bad.

        Critical flags are ones that won't just change themselves (like weather)
            and will need human intervention to fix.
        These are currently:
            - low diskspace remaining on image path
            - dome hatch is open (only in robotic mode)
            - UPSs are below critical charge
        """
        return self._crit_sum


class Status(object):
    """A class to give easy access to the status flags."""

    def __init__(self):
        self.flags_file = params.CONFIG_PATH + 'status_flags'
        self.emergency_file = params.EMERGENCY_FILE
        self.valid_modes = ['robotic', 'manual']
        self._load()

    def _load(self):
        try:
            data = load_json(self.flags_file)
            if data['mode'].lower() not in self.valid_modes:
                raise ValueError('Invalid mode: "{}"'.format(data['mode']))
            self._mode = data['mode'].lower()
            self._observer = str(data['observer'])
            self._autoclose = bool(data['autoclose'])
            self._alarm = bool(data['alarm'])
        except Exception:
            self._mode = 'robotic'
            self._observer = params.ROBOTIC_OBSERVER
            self._autoclose = True
            self._alarm = True
            with open(self.flags_file, 'w') as f:
                json.dump(self._status_dict, f)

        self.emergency_shutdown = os.path.isfile(self.emergency_file)
        if self.emergency_shutdown:
            mod_time = os.path.getmtime(self.emergency_file)
            self.emergency_shutdown_time = Time(mod_time, format='unix', precision=0).iso
            with open(self.emergency_file, 'r') as f:
                reasons = f.readlines()
                if len(reasons):
                    self.emergency_shutdown_reasons = [r.strip() for r in reasons]
                else:
                    self.emergency_shutdown_reasons = ['unknown']
        else:
            self.emergency_shutdown_time = None
            self.emergency_shutdown_reasons = [None]

    def _update_flags(self, key, value):
        with open(self.flags_file, 'r') as f:
            data = json.load(f)
        if key not in data:
            raise KeyError(key)
        data[key] = value
        with open(self.flags_file, 'w') as f:
            json.dump(data, f)
        self._load()

    def __repr__(self):
        self._load()
        repr_str = "mode='{}', ".format(self._mode)
        repr_str += "observer='{}', ".format(self._observer)
        repr_str += "autoclose={}, ".format(self._autoclose)
        repr_str += "alarm={}, ".format(self._alarm)
        repr_str += "emergency_shutdown={}".format(self.emergency_shutdown)
        return "Status({})".format(repr_str)

    @property
    def _status_dict(self):
        status_dict = {"mode": self._mode,
                       "observer": self._observer,
                       "autoclose": self._autoclose,
                       "alarm": self._alarm}
        return status_dict

    @property
    def mode(self):
        """Get the current system mode."""
        self._load()
        return self._mode

    @mode.setter
    def mode(self, value):
        if value.lower() not in self.valid_modes:
            raise ValueError('Invalid mode: "{}"'.format(value))
        self._update_flags('mode', value)
        if value.lower() == 'robotic':
            self._update_flags('autoclose', 1)
            self._update_flags('alarm', 1)
            self._update_flags('observer', params.ROBOTIC_OBSERVER)

    @property
    def observer(self):
        """Get the current observer."""
        self._load()
        return self._observer

    @observer.setter
    def observer(self, value):
        self._update_flags('observer', value)

    @property
    def autoclose(self):
        """Get if dome autoclose is currently enabled or not."""
        self._load()
        return self._autoclose

    @autoclose.setter
    def autoclose(self, value):
        self._update_flags('autoclose', int(bool(value)))

    @property
    def alarm(self):
        """Get if the dome alarm is currently enabled or not."""
        self._load()
        return self._alarm

    @alarm.setter
    def alarm(self, value):
        if self._mode == 'robotic' and int(bool(value)) == 0:
            raise ValueError('Cannot disable dome alarm in robotic mode')
        self._update_flags('alarm', int(bool(value)))

    def create_shutdown_file(self, reasons=None):
        """Create the emergency shutdown file."""
        self._load()
        cmd = 'touch ' + self.emergency_file
        os.system(cmd)

        if reasons is None:
            reasons = ['no reason given']
        for reason in reasons:
            if reason not in self.emergency_shutdown_reasons:
                send_slack_msg('{} has triggered emergency shutdown: {}'.format(
                               params.TELESCOP, reason))
                with open(self.emergency_file, 'a') as f:
                    f.write(reason + '\n')
            self._load()
