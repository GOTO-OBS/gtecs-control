"""Classes represent individual exposures and the exposure queue."""

import os
import time
from collections import MutableSequence

from . import misc
from . import params


class Exposure:
    """A class to represent a single exposure.

    Parameters
    ----------
    ut_list : list of int
        The UTs to take this exposure with.
    exptime : float
        The time to expose for.

    filt : str or `None`, default=None
        The filter to use for the exposure.
        If a string it should be a valid filter name (by default 'L', 'R', 'G', 'B', 'C').
        If `None` or 'X' then use whatever the current filter is.
    binning : int, default=1
        The binning factor to use for the exposure.
    frametype : str, default='normal'
        Valid frametypes are 'normal' or 'dark'
    target : str, default='NA'
        Exposure target name
    imgtype : str, default='SCIENCE'
        Exposure type
        Usual types include SCIENCE, FOCUS, FLAT, BIAS, DARK
    glance : bool, default=False
        If True then the exposure is a glance

    set_num : int or None, default=None
        Set number (assigned by the exq daemon)
    set_pos : int, default=1
        Position of this exposure in the set
    set_tot : int, default=1
        Total number of exposures in this set

    set_id : int or None, default=None
        The ExposureSet ID, if this exposure comes from the database
    pointing_id : int or None, default=None
        The pointing ID, if this exposure comes from the database

    """

    def __init__(self, ut_list, exptime,
                 filt=None, binning=1, frametype='normal', target='NA', imgtype='SCIENCE',
                 glance=False, set_num=None, set_pos=1, set_tot=1,
                 set_id=None, pointing_id=None):
        # Required arguments
        self.ut_list = ut_list
        self.ut_mask = misc.ut_list_to_mask(ut_list)
        self.ut_string = misc.ut_mask_to_string(self.ut_mask)
        self.exptime = exptime
        self.filt = filt

        # Optional arguments
        self.binning = binning
        self.frametype = frametype
        self.target = target
        self.imgtype = imgtype.upper()
        self.glance = glance

        # Set arguments
        self.set_num = set_num
        self.set_pos = set_pos
        self.set_tot = set_tot

        # Database arguments
        self.set_id = set_id
        self.pointing_id = pointing_id

        # Store creation time
        self.creation_time = time.gmtime()

    def __str__(self):
        return self.info()

    @classmethod
    def from_line(cls, line):
        """Create an Exposure object from a formatted string."""
        # eg '1011;20;R;2;normal;NA;SCIENCE;0;1000;1;3;-1;-1'
        ls = line.split(';')
        ut_list = misc.ut_string_to_list(ls[0])
        exptime = float(ls[1])
        filt = ls[2] if ls[2] != 'X' else None
        binning = int(ls[3])
        frametype = ls[4]
        target = ls[5]
        imgtype = ls[6].upper()
        glance = bool(int(ls[7]))
        set_num = int(ls[8]) if int(ls[8]) != -1 else None
        set_pos = int(ls[9])
        set_tot = int(ls[10])
        set_id = int(ls[11]) if int(ls[11]) != -1 else None
        pointing_id = int(ls[12]) if int(ls[12]) != -1 else None

        exposure = cls(ut_list,
                       exptime,
                       filt,
                       binning,
                       frametype,
                       target,
                       imgtype,
                       glance,
                       set_num,
                       set_pos,
                       set_tot,
                       set_id,
                       pointing_id,
                       )
        return exposure

    def as_line(self):
        """Give the line representation of this Exposure."""
        line = '{};{:.1f};{};{:d};{};{};{};{};{:d};{:d};{:d};{:d};{:d}\n'.format(
            self.ut_string,
            self.exptime,
            self.filt if self.filt is not None else 'X',
            self.binning,
            self.frametype,
            self.target,
            self.imgtype,
            1 if self.glance is True else 0,
            self.set_num if self.set_num is not None else -1,
            self.set_pos,
            self.set_tot,
            self.set_id if self.set_id is not None else -1,
            self.pointing_id if self.pointing_id is not None else -1,
        )
        return line

    def info(self):
        """Return a readable string of summary infomation about this Exposure."""
        s = 'EXPOSURE \n'
        s += '  ' + time.strftime('%Y-%m-%d %H:%M:%S UT', self.creation_time) + '\n'
        s += '  Unit telescope(s): {}\n'.format(self.ut_list)
        s += '  Exposure time: {:.1f}s\n'.format(self.exptime)
        s += '  Filter: {}\n'.format(self.filt)
        s += '  Binning: {:.0f}x{:.0f}\n'.format(self.binning, self.binning)
        s += '  Frame type: {}\n'.format(self.frametype)
        s += '  Target: {}\n'.format(self.target)
        s += '  Image type: {}\n'.format(self.imgtype)
        s += '  Glance: {}\n'.format(self.glance)
        if self.in_set:
            s += '  Set number: {}\n'.format(self.set_num)
            s += '  Position in set: {}/{}\n'.format(self.set_pos, self.set_tot)
        if self.from_database:
            s += '  ExposureSet database ID: {}\n'.format(self.set_id)
            s += '  Pointing database ID: {}\n'.format(self.pointing_id)
        return s

    @property
    def in_set(self):
        """Return True if this exposure is part of a set."""
        return self.set_num is not None

    @property
    def from_database(self):
        """Return True if this exposure is from the database."""
        return self.set_id is not None


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
        self.queue_file = os.path.join(params.FILE_PATH, 'exposure_queue')

        if not os.path.exists(self.queue_file):
            with open(self.queue_file, 'w') as f:
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
