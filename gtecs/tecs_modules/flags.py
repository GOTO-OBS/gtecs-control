#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                               flags.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#     G-TeCS module containing classes to read external flag files     #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from astropy.time import Time
import json
import time
import copy
from six import iteritems
# TeCS modules
from . import params
from ..controls.power_control import APCUPS


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
        '''
        return True if any power supplies have failed and UPS has kicked in
        '''
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
        for key, value in iteritems(conditions_dict):
            if key != 'dark':
                self._summary += value

        # and store a seperate summary of critical flags
        self._crit_sum = 0
        for key, value in iteritems(conditions_dict):
            if key in ['diskspace', 'hatch', 'low_battery']:
                self._crit_sum += value

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
            - dome hatch is open
            - UPSs are below critical charge
        """
        return self._crit_sum


class Overrides:
    def __init__(self):
        data = load_json(params.CONFIG_PATH + 'overrides_flags')
        self.__dict__ = copy.copy(data)

    def __repr__(self):
        class_name = type(self).__name__
        repr_str = ', '.join(['='.join((k, str(v))) for k, v in self.__dict__.items()])
        return '{}({})'.format(class_name, repr_str)
