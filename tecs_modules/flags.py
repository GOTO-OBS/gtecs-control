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
import time, calendar
# TeCS modules
from . import params

class Conditions:
    def __init__(self):
        f = open(params.CONFIG_PATH + 'conditions_flags','r')
        data = f.readlines()
        f.close()

        ut = time.strptime(data[0], '%Y-%m-%d %H:%M:%S UTC\n')
        self.update_time = calendar.timegm(ut)

        for x in data[1:]:
            exec(x)
        self.dark = dark
        self.dry = dry
        self.wind = wind
        self.humidity = humidity
        self.temperature = temperature
        self.summary = dry + wind + humidity + temperature #exclude dark

    def age(self):
        return time.time() - self.update_time

class Overrides:
    def __init__(self):
        f = open(params.CONFIG_PATH + 'overrides_flags','r')
        data = f.readlines()
        f.close()

        for x in data:
            exec(x)
        self.robotic = robotic
        self.dome_auto = dome_auto


