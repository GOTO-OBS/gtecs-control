#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                             scheduler.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#               G-TeCS robotic queue scheduler functions               #
#                     Martin Dyer, Sheffield, 2016                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

from __future__ import absolute_import
from __future__ import print_function

import os
import signal
from collections import namedtuple

import numpy as np
from scipy import interpolate

from astropy import coordinates as coord, units as u
from astropy.time import Time
from astropy._erfa import ErfaWarning

from astroplan import Observer
from astroplan.constraints import _get_altaz

from astroplan import (Constraint, TimeConstraint,
                       AltitudeConstraint, AtNightConstraint,
                       MoonSeparationConstraint, MoonIlluminationConstraint)

import warnings
warnings.simplefilter("ignore", ErfaWarning)

# TeCS modules
from . import params
from . import misc
from . import html
from . import astronomy
from .. import database as db
from . import astropy_speedups

## Setup
# define paths to directories
queue_folder = params.QUEUE_PATH  + 'todo/'
queue_file   = params.QUEUE_PATH  + 'queue_info'
horizon_file = params.CONFIG_PATH + 'horizon'

# priority settings
too_weight = 0.1
prob_dp = 3
prob_weight = 0.1
airmass_dp = 5
airmass_weight = 0.00001
tts_dp = 5
tts_weight = 0.00001

# set debug level
debug = 1

# catch ctrl-c
signal.signal(signal.SIGINT, misc.signal_handler)

def apply_constraints(constraints, observer, targets, times):
    """
    Determines if the targets are observable at the given times for a
    particular observer, given the constraints.
    Similar to Astroplan's ``is_observable``, but returns full constraint
    results in a 3D array.

    Parameters
    ----------
    constraints : list or `~astroplan.constraints.Constraint`
        Observational constraint(s)

    observer : `~astroplan.Observer`
        The observer who has constraints ``constraints``

    targets : {list, `~astropy.coordinates.SkyCoord`, `~astroplan.FixedTarget`}
        Target or list of targets

    times : `~astropy.time.Time`
        Array of times on which to test the constraint

    Returns
    -------
    constraint_array : 3D array
        first dimension is given by the number of times
        second dimention is given by the number of targets
        third dimention is given by the number of constraints
    """
    if not hasattr(constraints, '__len__'):
        constraints = [constraints]

    applied_constraints = np.array([constraint(observer, targets, times)
                                    for constraint in constraints])
    return np.transpose(applied_constraints)


class ArtificialHorizonConstraint(Constraint):
    """Ensure altitude is above pt5m artificial horizon"""
    def __init__(self):
        az, alt = np.loadtxt(horizon_file, usecols=(0, 1)).T
        self.alt = interpolate.interp1d(az, alt, bounds_error=False,
                                        fill_value=12.0)

    def compute_constraint(self, times, observer, targets):
        altaz = _get_altaz(times, observer, targets)['altaz']
        artificial_horizon_alt = self.alt(altaz.az)*u.deg
        return altaz.alt > artificial_horizon_alt


def time_to_set(observer, targets, now):
    """
    Time until ``target``s next set below the horizon
    """

    # Create grid of times
    set_times = astronomy._rise_set_trig(now, targets, observer.location,
                                         'next', 'setting')
    seconds_until_set = (set_times - now).to(u.s)
    return seconds_until_set


class Pointing:
    def __init__(self, id, ra, dec, priority, tileprob, too, maxsunalt,
                 minalt, mintime, maxmoon, start, stop, current):
        self.id = int(id)
        self.ra = float(ra)
        self.dec = float(dec)
        self.priority = float(priority)
        self.tileprob = float(tileprob)
        self.too = bool(int(too))
        self.maxsunalt = float(maxsunalt)
        self.minalt = float(minalt)
        self.mintime = float(mintime)
        self.maxmoon = maxmoon
        self.start = start
        self.stop = stop
        self.current = bool(current)

    def __eq__(self, other):
        try:
            return self.id == other.id
        except AttributeError:
            return False

    def __ne__(self, other):
        return not self == other

    @classmethod
    def from_file(cls, fname):
        lines = []
        with open(fname) as f:
            for line in f.readlines():
                if not line.startswith('#'):
                    lines.append(line)
        # first line is the pointing
        (id, ra, dec, priority, tileprob, too, sunalt, minalt,
         mintime, moon, start, stop) = lines[0].split()
        pointing = cls(id, ra, dec, priority, tileprob, too, sunalt,
                       minalt, mintime, moon, start, stop, False)
        return pointing

    @classmethod
    def from_database(cls, dbPointing):
        # not every pointing has an assosiated GW tile probability
        if dbPointing.ligoTile:
            tileprob = dbPointing.ligoTile.probability
        else:
            tileprob = 0
        # create pointing object
        pointing = cls(id        = dbPointing.pointingID,
                       ra        = dbPointing.ra,
                       dec       = dbPointing.decl,
                       priority  = dbPointing.rank,
                       tileprob  = tileprob,
                       too       = dbPointing.ToO,
                       maxsunalt = dbPointing.maxSunAlt,
                       minalt    = dbPointing.minAlt,
                       mintime   = dbPointing.minTime,
                       maxmoon   = dbPointing.maxMoon,
                       start     = dbPointing.startUTC,
                       stop      = dbPointing.stopUTC,
                       current   = False)
        return pointing


class Queue:
    def __init__(self, pointings=None):
        if pointings is None:
            pointings = []
        self.pointings = pointings
        self.ra_arr = []
        self.dec_arr = []
        self.target_arr = []
        self.mintime_arr = []
        self.start_arr = []
        self.stop_arr = []
        self.maxsunalt_arr = []
        self.minalt_arr = []
        self.maxmoon_arr = []
        self.priority_arr = []
        self.tileprob_arr = []
        if len(self.pointings) > 0:
            self.initialise()

    def __len__(self):
        return len(self.pointings)

    def get_current_pointing(self):
        '''Return the current pointing from the queue'''
        for p in self.pointings:
            if p.current:
                return p

    def initialise(self):
        '''Setup the queue and constraints when initialised.'''
        limits = {'B': 1.0, 'G': 0.65, 'D': 0.25}
        moondist_limit = params.MOONDIST_LIMIT * u.deg
        for pointing in self.pointings:
            self.ra_arr.append(pointing.ra)
            self.dec_arr.append(pointing.dec)
            self.mintime_arr.append(pointing.mintime)
            self.start_arr.append(pointing.start)
            self.stop_arr.append(pointing.stop)
            self.maxsunalt_arr.append(pointing.maxsunalt)
            self.minalt_arr.append(pointing.minalt)
            self.maxmoon_arr.append(limits[pointing.maxmoon])
            self.priority_arr.append(pointing.priority)
            self.tileprob_arr.append(pointing.tileprob)

        self.mintime_arr = u.Quantity(self.mintime_arr, unit=u.s)
        self.maxsunalt_arr = u.Quantity(self.maxsunalt_arr, unit=u.deg)
        self.minalt_arr = u.Quantity(self.minalt_arr, unit=u.deg)

        self.start_arr = Time(self.start_arr, scale='utc', format='datetime')
        self.stop_arr = Time(self.stop_arr, scale='utc', format='datetime')

        self.target_arr = coord.SkyCoord(self.ra_arr, self.dec_arr,
                                         unit=u.deg, frame='icrs')

        self.constraints = [AtNightConstraint(self.maxsunalt_arr),
                            AltitudeConstraint(self.minalt_arr, None),
                            ArtificialHorizonConstraint(),
                            MoonIlluminationConstraint(None, self.maxmoon_arr),
                            MoonSeparationConstraint(moondist_limit, None)]
        self.constraint_names = ['SunAlt',
                                 'MinAlt',
                                 'ArtHoriz',
                                 'Moon',
                                 'MoonSep']

        self.time_constraint = [TimeConstraint(self.start_arr, self.stop_arr)]
        self.time_constraint_name = ['Time']

        self.mintime_constraints = [AtNightConstraint(self.maxsunalt_arr),
                                    AltitudeConstraint(self.minalt_arr, None),
                                    ArtificialHorizonConstraint()]
        self.mintime_constraint_names = ['SunAlt_mintime',
                                         'MinAlt_mintime',
                                         'ArtHoriz_mintime']

        self.all_constraint_names = (self.constraint_names +
                                     self.time_constraint_name +
                                     self.mintime_constraint_names)

    def check_validities(self, now, observer):
        ''' Check if the pointings are valid, both now and after mintimes'''

        # apply normal constraints
        cons_valid_arr = apply_constraints(self.constraints,
                                           observer, self.target_arr,
                                           now)

        # apply time constraint
        time_cons_valid_arr = apply_constraints(self.time_constraint,
                                                observer, self.target_arr,
                                                now)

        # apply mintime constraints
        later_arr = now + self.mintime_arr
        min_cons_valid_arr = apply_constraints(self.mintime_constraints,
                                               observer, self.target_arr,
                                               later_arr)

        for i in range(len(self.pointings)):
            pointing = self.pointings[i]
            pointing.valid_time = now

            # save constraints to the pointing objects
            pointing.all_constraint_names = self.all_constraint_names
            pointing.constraint_names = list(self.constraint_names)
            pointing.valid_arr = cons_valid_arr[i]
            # the current pointing doesn't apply the time constraint
            if pointing.current != True:
                pointing.constraint_names += self.time_constraint_name
                pointing.valid_arr = np.concatenate((pointing.valid_arr,
                                                     time_cons_valid_arr[i]))
            # current pointing and queue fillers don't apply mintime cons
            if pointing.priority < 5 and pointing.current != True:
                pointing.constraint_names += self.mintime_constraint_names
                pointing.valid_arr = np.concatenate((pointing.valid_arr,
                                                     min_cons_valid_arr[i]))

            # finally find out if it's valid or not
            pointing.valid = np.all(pointing.valid_arr)

    def calculate_priorities(self, time, observer):
        '''Calculate priorities at a given time for each pointing.'''

        ## Find base priority based on rank
        priorities = np.array([p.priority for p in self.pointings])

        ## Find ToO values (0 or 1)
        too_mask = np.array([p.too for p in self.pointings])
        nottoo_mask = np.invert(too_mask)
        too_arr = np.array(nottoo_mask, dtype = float)

        ## Find probability values (0.00.. to 0.99..)
        prob_arr = np.array([1-prob if prob != 0
                             else 0
                             for prob in self.tileprob_arr])
        prob_arr = np.around(prob_arr, decimals = prob_dp)
        bad_prob_mask = np.logical_or(prob_arr < 0, prob_arr >= 1)
        prob_arr[bad_prob_mask] = float('0.' + '9' * prob_dp)

        ## Find airmass values (0.00.. to 0.99..)
        # airmass at start
        altaz_now = _get_altaz(time, observer, self.target_arr)['altaz']
        secz_now = altaz_now.secz

        # airmass at mintime
        later_arr = time + self.mintime_arr
        altaz_later = _get_altaz(later_arr, observer, self.target_arr)['altaz']
        secz_later = altaz_later.secz

        # take average
        secz_arr = (secz_now + secz_later)/2.
        airmass_arr = np.around(secz_arr/10., decimals = airmass_dp)
        bad_airmass_mask = np.logical_or(airmass_arr < 0, airmass_arr >= 1)
        airmass_arr[bad_airmass_mask] = float('0.' + '9' * airmass_dp)

        ## Find time to set values (0.00.. to 0.99..)
        tts_sec_arr = time_to_set(observer, self.target_arr, time)
        tts_hr_arr = tts_sec_arr.to(u.hour)
        tts_arr = np.around(tts_hr_arr.value/24., decimals = tts_dp)
        bad_tts_mask = np.logical_or(tts_arr < 0, tts_arr >= 1)
        tts_arr[bad_tts_mask] = float('0.' + '9' * tts_dp)

        ## Construct the probability based on weightings
        priorities_now = priorities.copy()

        priorities_now += too_arr * too_weight
        priorities_now += prob_arr * prob_weight
        priorities_now += airmass_arr * airmass_weight
        priorities_now += tts_arr * tts_weight

        # check validities, add 10 to invalid pointings
        self.check_validities(time, observer)
        valid_mask = np.array([p.valid for p in self.pointings])
        invalid_mask = np.invert(valid_mask)
        priorities_now[invalid_mask] += 100

        # if the current pointing is invalid it must be complete
        # therefore add 10 to prevent it coming up again
        current_mask = np.array([p.current for p in self.pointings])
        current_complete_mask = np.logical_and(current_mask, priorities_now > 100)
        priorities_now[current_complete_mask] += 100

        # save priority_now to pointing
        for pointing, priority_now in zip(self.pointings, priorities_now):
            pointing.priority_now = priority_now


def import_pointings_from_folder(queue_folder):
    """
    Creates a list of `Pointing` objects from a folder
    containing pointing files.

    Parameters
    ----------
    queue_folder : str
        The location of the pointing files.

    Returns
    -------
    queue : `Queue`
        An Queue containing Pointings from the queue_folder.
    """
    queue = Queue()
    queue_files = os.listdir(queue_folder)

    if queue_files is not None:
        for pointing_file in queue_files:
            path = os.path.join(queue_folder, pointing_file)
            queue.pointings.append(Pointing.from_file(path))
    queue.initialise()
    return queue


def import_pointings_from_database(time):
    """
    Creates a list of `Pointing` objects from the GOTO database.

    Parameters
    ----------
    time : `~astropy.time.Time`
        The time to fetch the queue for.

    Returns
    -------
    queue : `Queue`
        An Queue containing Pointings from the database.
    """
    queue = Queue()
    with db.open_session() as session:
        current_dbPointing, pending_pointings = db.get_queue(session, time)
        if len(pending_pointings) == 0:
            # current queue empty
            return None
        else:
            for dbPointing in pending_pointings:
                queue.pointings.append(Pointing.from_database(dbPointing))
        if current_dbPointing is not None:
            current_pointing = Pointing.from_database(current_dbPointing)
            current_pointing.current = True
            queue.pointings.append(current_pointing)
    queue.initialise()
    return queue


def write_queue_file(queue, time, observer):
    """
    Write any time-dependent pointing infomation to a file
    """

    # The queue should already have priorities calculated
    try:
        p = queue.pointings[0].priority_now
    except AttributeError:
        message = "{} has not yet had priorities calculated".format(queue)
        raise ValueError(message)

    pointinglist = list(queue.pointings)

    # save altaz too
    altaz_now = _get_altaz(time, observer, queue.target_arr)['altaz']
    altaz_now_str = altaz_now.altaz.to_string()
    altaz_now_list = [[float(i) for i in s.split()] for s in altaz_now_str]

    later_arr = time + queue.mintime_arr
    altaz_later = _get_altaz(later_arr, observer, queue.target_arr)['altaz']
    altaz_later_str = altaz_later.altaz.to_string()
    altaz_later_list = [[float(i) for i in s.split()] for s in altaz_later_str]

    combined = list(zip(pointinglist, altaz_now_list, altaz_later_list))
    combined.sort(key=lambda x: x[0].priority_now)

    # now save as json file
    import json
    with open(queue_file, 'w') as f:
        json.dump(str(time),f)
        f.write('\n')
        json.dump(queue.all_constraint_names,f)
        f.write('\n')
        for pointing, altaznow, altazlater in combined:
            valid_nonbool = [int(b) for b in pointing.valid_arr]
            con_list = list(zip(pointing.constraint_names, valid_nonbool))
            json.dump([pointing.id, pointing.priority_now,
                       altaznow, altazlater, con_list],f)
            f.write('\n')


def find_highest_priority(queue, time, observer):
    """
    Calculate priorities for pointings in a queue at a given time
    and return the pointing with the highest priority.

    Parameters
    ----------
    queue : `Queue`
        An Queue containing Pointings to consider.

    time : `~astropy.time.Time`
        The time to calculate the priorities at.

    write_html : bool
        A flag to enable writing of html files.

    Returns
    -------
    highest_pointing : `Pointing`
        Pointing with the highest calculated priority at the time given.

    pointinglist : list of `Pointing` objects
        A list of Pointings sorted by priority (for html queue page).
    """

    queue.calculate_priorities(time, observer)

    pointinglist = list(queue.pointings)
    pointinglist.sort(key=lambda x: x.priority_now)
    highest_pointing = pointinglist[0]

    return highest_pointing


def what_to_do_next(current_pointing, highest_pointing):
    """
    Decide whether to slew to a new target, remain on the current target
    or park the telescope.
    NOTE currently based on pt5m logic, will need revision for GOTO

    Parameters
    ----------
    current_pointing : `Pointing`
        The current pointing.
        `None` if the telescope is idle.

    highest_pointing : `Pointing`
        The current highest priority pointing from the queue.
        `None` if the queue is empty.

    Returns
    -------
    new_pointing : `Pointing`
        The new pointing to send to the pilot
        Could be either:
          current_pointing (remain on current target),
          highest_pointing (slew to new target) or
          `None`           (nothing to do, park scope).
    """

    # Deal with either being missing (telescope is idle or queue is empty)
    if current_pointing is None and highest_pointing is None:
        return current_pointing
    elif current_pointing is None:
        if highest_pointing.priority_now < 100:
            return highest_pointing
        else:
            return current_pointing
    elif highest_pointing is None:
        if current_pointing.priority_now > 100:
            return None
        else:
            return current_pointing

    if current_pointing == highest_pointing:
        if current_pointing.priority_now >= 100 or highest_pointing.priority_now >= 100:
            return None  # it's either finished or is now illegal
        else:
            return highest_pointing

    if current_pointing.priority_now >= 100:  # current pointing is illegal (finished)
        if highest_pointing.priority_now < 100:  # new pointing is legal
            return highest_pointing
        else:
            return None
    else:  # telescope is observing legally
        if highest_pointing.priority_now >= 100:  # no legal pointings
            return None
        else:  # both are legal
            if current_pointing.priority_now > 5:  # a filler, always slew
                return highest_pointing
            elif highest_pointing.too:  # slew to a ToO, unless now is also a ToO
                if current_pointing.too:
                    return current_pointing
                else:
                    return highest_pointing
            else:  # stay for normal pointings
                return current_pointing


def check_queue(time, write_html=False):
    """
    Check the current pointings in the queue, find the highest priority at
    the given time and decide whether to slew to it, stay on the current target
    or park the telescope.

    Parameters
    ----------
    current_pointing : `Pointing`
        The current pointing.
        `None` if the telescope is idle.

    time : `~astropy.time.Time`
        The time to calculate the priorities at.

    Returns
    -------
    new_pointing : `Pointing`
        The pointing to send to the pilot.
        Could be a new pointing, the current pointing or 'None' (park).
    """

    GOTO = Observer.at_site('lapalma')

    queue = import_pointings_from_database(time)
    if queue is None:
        return None, None, None

    if len(queue) > 0:
        highest_pointing = find_highest_priority(queue, time, GOTO)
    else:
        highest_pointing = None
    current_pointing = queue.get_current_pointing()

    write_queue_file(queue, time, GOTO)
    if write_html:
        # since it's now independent, this could be run from elsewhere
        # that would save the scheduler doing it
        html.write_queue_page()

    new_pointing = what_to_do_next(current_pointing, highest_pointing)

    if new_pointing is not None:
        return new_pointing.id, new_pointing.priority_now, new_pointing.mintime
    else:
        return None, None, None
