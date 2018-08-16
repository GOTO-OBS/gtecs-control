#!/usr/bin/env python
"""Classes to control the exposure queue."""

import os
import time
from collections import MutableSequence

from .. import misc
from .. import params


class Exposure(object):
    """A class to represent a single exposure.

    Contains 3 functions:
    - from_line(str)
    - as_line()
    - info()

    Exposures contain the folowing infomation:
    - tel_list    [lst]  -- REQUIRED --
    - exptime     [int]  -- REQUIRED --
    - filt        [str]  <default = None>
    - binning     [int]  <default = 1>
    - frametype   [str]  <default = 'normal'>
    - target      [str]  <default = 'NA'>
    - imgtype     [str]  <default = 'SCIENCE'>
    - glance      [bool] <default = False>
    - set_pos     [int]  <default = 1>
    - set_total   [int]  <default = 1>
    - db_id       [int]  <default = None>

    """

    def __init__(self, tel_list, exptime,
                 filt=None, binning=1, frametype='normal',
                 target='NA', imgtype='SCIENCE', glance=False,
                 set_pos=1, set_total=1, db_id=None):
        self.creation_time = time.gmtime()
        self.tel_list = tel_list
        self.tel_mask = misc.ut_list_to_mask(tel_list)
        self.tel_string = misc.ut_mask_to_string(self.tel_mask)
        self.exptime = exptime
        self.filt = filt
        self.binning = binning
        self.frametype = frametype
        self.target = target
        self.imgtype = imgtype
        self.glance = glance
        self.set_pos = set_pos
        self.set_total = set_total
        if db_id:
            self.db_id = db_id
        else:
            self.db_id = 0

    def __str__(self):
        return self.info()

    @classmethod
    def from_line(cls, line):
        """Create an Exposure object from a formatted string."""
        # eg '1011;20;R;2;normal;NA;SCIENCE;0;1;3;126598'
        ls = line.split(';')
        tel_list = misc.ut_string_to_list(ls[0])
        exptime = float(ls[1])
        filt = ls[2] if ls[2] != 'X' else None
        binning = int(ls[3])
        frametype = ls[4]
        target = ls[5]
        imgtype = ls[6]
        glance = bool(ls[7])
        set_pos = int(ls[8])
        set_total = int(ls[9])
        db_id = int(ls[10])
        exp = cls(tel_list, exptime, filt,
                  binning, frametype, target, imgtype, glance,
                  set_pos, set_total, db_id)
        return exp

    def as_line(self):
        """Give the line representation of this Exposure."""
        line = '%s;%.1f;%s;%i;%s;%s;%s;%i;%i;%i;%i\n'\
               % (self.tel_string, self.exptime, self.filt if self.filt is not None else 'X',
                  self.binning, self.frametype, self.target, self.imgtype,
                  self.glance, self.set_pos, self.set_total, self.db_id)
        return line

    def info(self):
        """Return a readable string of summary infomation about this Exposure."""
        s = 'EXPOSURE \n'
        s += '  ' + time.strftime('%Y-%m-%d %H:%M:%S UT', self.creation_time) + '\n'
        s += '  Unit telescope(s): %s\n' % self.tel_list
        s += '  Exposure time: %is\n' % self.exptime
        s += '  Filter: %s\n' % self.filt
        s += '  Binning: %i\n' % self.binning
        s += '  Frame type: %s\n' % self.frametype
        s += '  Target: %s\n' % self.target
        s += '  Image type: %s\n' % self.imgtype
        s += '  Glance: %s\n' % self.glance
        s += '  Position in set: %i\n' % self.set_pos
        s += '  Total in set: %i\n' % self.set_total
        s += '  ExposureSet database ID (if any): %i\n' % self.db_id
        return s


class ExposureQueue(MutableSequence):
    """A queue sequence to hold Exposures.

    Contains 4 functions:
    - write_to_file()
    - insert(index,value)
    - clear()
    - get()

    """

    def __init__(self):
        self.data = []
        self.queue_file = os.path.join(params.CONFIG_PATH, 'exposure_queue')

        if not os.path.exists(self.queue_file):
            f = open(self.queue_file, 'w')
            f.write('#\n')
            f.close()

        with open(self.queue_file) as f:
            lines = f.read().splitlines()
            for line in lines:
                if not line.startswith('#'):
                    exposure = Exposure.from_line(line)
                    self.data.append(exposure)

    def write_to_file(self):
        """Write the current queue to the queue file."""
        with open(self.queue_file, 'w') as f:
            for exposure in self.data:
                f.write(exposure.as_line())

    def __getitem__(self, index):
        return self.data[index]

    def __setitem__(self, index, value):
        self.data[index] = value
        self.write_to_file()

    def __delitem__(self, index):
        del self.data[index]
        self.write_to_file()

    def __len__(self):
        return len(self.data)

    def insert(self, index, value):
        """Add an item to the queue at a specified position."""
        self.data.insert(index, value)
        self.write_to_file()

    def clear(self):
        """Empty the current queue and queue file."""
        self.data = []
        self.write_to_file()

    def get(self):
        """Return info() for all exposures in the queue."""
        s = '{} items in queue:\n'.format(len(self.data))
        for n, exposure in enumerate(self.data):
            s += '{:0>3.0f}: {}'.format(n + 1, exposure.info())
        return s.rstrip()

    def get_simple(self):
        """Return string for all exposures in the queue."""
        s = '{} items in queue:\n'.format(len(self.data))
        for n, exposure in enumerate(self.data):
            s += '{:0>3.0f}: {}'.format(n + 1, exposure.as_line())
        return s.rstrip()
