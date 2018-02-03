"""
Classes to read external flag files
"""

import os
import time
import json
import copy

from astropy.time import Time

from . import params
from .slack import send_slack_msg
from .controls.power_control import APCUPS


def load_json(fname):
    attemps_remaining = 3
    while attemps_remaining:
        try:
            with open(fname, 'r') as fh:
                data_dict = json.load(fh)
            assert(len(data_dict) != 0)
            break
        except:
            time.sleep(0.001)
            attemps_remaining -= 1
            pass
    if attemps_remaining:
        return data_dict
    else:
        raise IOError('cannot read {}'.format(fname))


class Power:
    def __init__(self):
        """
        Class to monitor the UPS status
        """
        self.ups_units = {}
        for unit_name in params.POWER_UNITS:
            unit_class = params.POWER_UNITS[unit_name]['CLASS']
            unit_ip = params.POWER_UNITS[unit_name]['IP']
            if unit_class == 'APCUPS':
                self.ups_units[unit_name] = APCUPS(unit_ip)

    @property
    def failed(self):
        """
        return True if any power supplies have failed and UPS has kicked in
        """
        acceptable_status_vals = ['Normal', 'onBatteryTest']
        return any([self.ups_units[ukey].status() not in acceptable_status_vals
                    for ukey in self.ups_units])

    def __repr__(self):
        class_name = type(self).__name__
        repr_str = ', '.join(['='.join((ukey, self.ups_units[ukey].status())) for ukey in self.ups_units])
        return '{}({})'.format(class_name, repr_str)


class Conditions:
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
            if key in ['diskspace', 'low_battery', 'ice']:
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
        return int(Time.now().unix - self.update_time)

    def __repr__(self):
        class_name = type(self).__name__
        repr_str = ', '.join(['='.join((k, str(v))) for k, v in self.__dict__.items()])
        return '{}({})'.format(class_name, repr_str)

    @property
    def bad(self):
        """
        A convenient property to quickly check if these conditions are bad.

        Uses summary of conditions and age check
        """
        if self.age() > params.MAX_CONDITIONS_AGE:
            tooOld = 1
        else:
            tooOld = 0
        return self._summary + tooOld

    @property
    def critical(self):
        """
        A property to check if any of the critical flags are bad.

        Critical flags are ones that won't just change themselves (like weather)
            and will need human intervention to fix.
        These are currently:
            - low diskspace remaining on image path
            - dome hatch is open (only in robotic mode)
            - UPSs are below critical charge
        """
        return self._crit_sum


class Status:
    def __init__(self):
        self.flags_file = params.CONFIG_PATH + 'status_flags'
        self.emergency_file = params.EMERGENCY_FILE
        self.valid_modes = ['robotic', 'manual']
        self._load()

    def _load(self):
        data = load_json(self.flags_file)
        if data['mode'].lower() not in self.valid_modes:
            raise ValueError('Invalid mode: "{}"'.format(data['mode']))
        self._mode = data['mode'].lower()
        self._observer = str(data['observer'])
        self._autoclose = bool(data['autoclose'])
        self.emergency_shutdown = os.path.isfile(self.emergency_file)
        if self.emergency_shutdown:
            with open(self.emergency_file, 'r') as f:
                reason = f.readlines()
                if len(reason):
                    self.emergency_shutdown_time = reason[0].strip()
                    self.emergency_shutdown_reason = reason[1].strip()
                else:
                    self.emergency_shutdown_time = 'unknown'
                    self.emergency_shutdown_reason = 'unknown'
        else:
            self.emergency_shutdown_time = None
            self.emergency_shutdown_reason = None

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
        repr_str += "emergency_shutdown={}".format(self.emergency_shutdown)
        return "Status({})".format(repr_str)

    @property
    def mode(self):
        self._load()
        return self._mode

    @mode.setter
    def mode(self, value):
        if value.lower() not in self.valid_modes:
            raise ValueError('Invalid mode: "{}"'.format(value))
        self._update_flags('mode', value)
        if value.lower() == 'robotic':
            self._update_flags('autoclose', 1)
            self._update_flags('observer', params.ROBOTIC_OBSERVER)

    @property
    def observer(self):
        self._load()
        return self._observer

    @observer.setter
    def observer(self, value):
        self._update_flags('observer', value)

    @property
    def autoclose(self):
        self._load()
        return self._autoclose

    @autoclose.setter
    def autoclose(self, value):
        self._update_flags('autoclose', int(bool(value)))

    def create_shutdown_file(self, why='no reason given'):
        """Create the emergency shutdown file"""
        self._load()
        if not self.emergency_shutdown:
            send_slack_msg('GOTO has triggered emergency shutdown: {}'.format(why))
        cmd = 'touch ' + self.emergency_file
        os.system(cmd)
        with open(self.emergency_file, 'w') as f:
            now = Time.now()
            now.precision = 0
            f.write(now.iso + '\n')
            f.write(why + '\n')
        self._load()
