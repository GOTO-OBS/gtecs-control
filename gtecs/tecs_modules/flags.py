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
import copy
from six import iteritems
# TeCS modules
from . import params

class Conditions:
    def __init__(self):
        with open(params.CONFIG_PATH + 'conditions_flags', 'r') as fh:
            # we will use JSON to store conditions, since it's easier
            # to write and parse than the pt5m format
            conditions_dict = json.load(fh)

        # store update time and remove from dictionary
        update_time = int(Time(conditions_dict['update_time']).unix)
        del conditions_dict['update_time']

        # set Condtions properties (which are stored in __dict__)
        # to the values in the dictionary
        self.__dict__ = copy.copy(conditions_dict)

        # add the update time
        self.update_time = update_time

        # and the summary
        self.summary = 0 # sum of flags, excluding dark
        for key, value in iteritems(conditions_dict):
            if key != 'dark':
                self.summary += value

    def age(self):
        return int(Time.now().unix - self.update_time)

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
        return self.summary + tooOld

class Overrides:
    def __init__(self):
        with open(params.CONFIG_PATH + 'overrides_flags','r') as fh:
            data = json.load(fh)
        self.__dict__ = copy.copy(data)

