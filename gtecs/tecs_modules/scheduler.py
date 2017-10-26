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
import json

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
prob_weight = 0.01
airmass_weight = 0.001
tts_weight = 0.00001

# set debug level
debug = 1

# survey tile pointing rank (should be in params)
SURVEY_RANK = 999
INVALID_PRIORITY = 1000

HARD_ALT_LIM = 10
HARD_HA_LIM = 8

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
                 minalt, mintime, maxmoon, start, stop, current, survey):
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
        self.survey = bool(survey)

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
                       minalt, mintime, moon, start, stop, False, False)
        return pointing

    @classmethod
    def from_database(cls, dbPointing):
        # not every pointing has an assosiated GW tile probability
        if dbPointing.eventTile:
            tileprob = dbPointing.eventTile.probability
        else:
            tileprob = 0
        # survey tiles can be told apart by being linked to a Survey
        if dbPointing.surveyID is not None:
            survey = True
        else:
            survey = False
        # the current pointing has running status
        if dbPointing.status == 'running':
            current = True
        else:
            current = False

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
                       current   = current,
                       survey    = survey)
        return pointing


class PointingQueue:
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

        # create pointing data arrays
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

        # convert to numpy arrays so we can mask them
        self.ra_arr = np.array(self.ra_arr)
        self.dec_arr = np.array(self.dec_arr)
        self.target_arr = np.array(self.target_arr)
        self.mintime_arr = np.array(self.mintime_arr)
        self.start_arr = np.array(self.start_arr)
        self.stop_arr = np.array(self.stop_arr)
        self.maxsunalt_arr = np.array(self.maxsunalt_arr)
        self.minalt_arr = np.array(self.minalt_arr)
        self.maxmoon_arr = np.array(self.maxmoon_arr)
        self.priority_arr = np.array(self.priority_arr)
        self.tileprob_arr = np.array(self.tileprob_arr)

        # Create normal constraints
        # Apply to every pointing
        self.targets = coord.SkyCoord(self.ra_arr, self.dec_arr,
                                      unit=u.deg, frame='icrs')
        self.mintimes = u.Quantity(self.mintime_arr, unit=u.s)
        self.maxsunalts = u.Quantity(self.maxsunalt_arr, unit=u.deg)
        self.minalts = u.Quantity(self.minalt_arr, unit=u.deg)

        self.constraints = [AtNightConstraint(self.maxsunalts),
                            AltitudeConstraint(self.minalts, None),
                            ArtificialHorizonConstraint(),
                            MoonIlluminationConstraint(None, self.maxmoon_arr),
                            MoonSeparationConstraint(moondist_limit, None)]
        self.constraint_names = ['SunAlt',
                                 'MinAlt',
                                 'ArtHoriz',
                                 'Moon',
                                 'MoonSep']

        # Create time constraints
        # Apply to every pointing EXCEPT the current pointing
        self.time_mask = np.array([not p.current for p in self.pointings])

        if np.all(self.time_mask):
            # no current pointing, don't bother making a new SkyCoord
            self.targets_t = self.targets
        else:
            self.targets_t = coord.SkyCoord(self.ra_arr[self.time_mask],
                                            self.dec_arr[self.time_mask],
                                            unit=u.deg, frame='icrs')

        self.starts_t = Time(self.start_arr[self.time_mask],
                             scale='utc', format='datetime')
        self.stops_t = Time(self.stop_arr[self.time_mask],
                            scale='utc', format='datetime')

        self.time_constraint = [TimeConstraint(self.starts_t, self.stops_t)]
        self.time_constraint_name = ['Time']

        # Create mintime constraints
        # Apply to non-survey, non-current pointings
        self.mintime_mask = np.array([not(p.current or p.survey)
                                      for p in self.pointings])

        if np.all(self.mintime_mask):
            # no current pointing, no survey tiles => use existing objects
            self.targets_m = self.targets
            self.mintimes_m = self.mintimes
            self.maxsunalts_m = self.maxsunalts
            self.minalts_m = self.minalts
        else:
            self.targets_m = coord.SkyCoord(self.ra_arr[self.mintime_mask],
                                            self.dec_arr[self.mintime_mask],
                                            unit=u.deg, frame='icrs')
            self.mintimes_m = u.Quantity(self.mintime_arr[self.mintime_mask],
                                         unit=u.s)
            self.maxsunalts_m = u.Quantity(self.maxsunalt_arr[self.mintime_mask],
                                           unit=u.deg)
            self.minalts_m = u.Quantity(self.minalt_arr[self.mintime_mask],
                                        unit=u.deg)

        self.mintime_constraints = [AtNightConstraint(self.maxsunalts_m),
                                    AltitudeConstraint(self.minalts_m, None),
                                    ArtificialHorizonConstraint()]
        self.mintime_constraint_names = ['SunAlt_mintime',
                                         'MinAlt_mintime',
                                         'ArtHoriz_mintime']

        # Save all names
        self.all_constraint_names = (self.constraint_names +
                                     self.time_constraint_name +
                                     self.mintime_constraint_names)

    def check_validities(self, now, observer):
        ''' Check if the pointings are valid, both now and after mintimes'''

        # apply normal constraints
        cons_valid_arr = apply_constraints(self.constraints,
                                           observer,
                                           self.targets,
                                           now)

        # apply time constraint to filtered targets
        if np.any(self.time_mask):
            time_cons_valid_arr = apply_constraints(self.time_constraint,
                                                    observer,
                                                    self.targets_t,
                                                    now)
        else:
            time_cons_valid_arr = np.array([])

        # apply mintime constraints to filtered targets
        if any(self.mintime_mask):
            later_arr = now + self.mintimes_m
            min_cons_valid_arr = apply_constraints(self.mintime_constraints,
                                                   observer,
                                                   self.targets_m,
                                                   later_arr)
        else:
            min_cons_valid_arr = np.array([])

        # save the results on the pointings
        for i, pointing in enumerate(self.pointings):
            pointing.constraint_names = list(self.constraint_names)
            pointing.valid_arr = cons_valid_arr[i]

        for i, pointing in enumerate(self.pointings[self.time_mask]):
            pointing.constraint_names += self.time_constraint_name
            pointing.valid_arr = np.concatenate((pointing.valid_arr,
                                                 time_cons_valid_arr[i]))

        for i, pointing in enumerate(self.pointings[self.mintime_mask]):
            pointing.constraint_names += self.mintime_constraint_names
            pointing.valid_arr = np.concatenate((pointing.valid_arr,
                                                 min_cons_valid_arr[i]))

        # finally find out if each pointing is valid or not
        for pointing in self.pointings:
            pointing.all_constraint_names = self.all_constraint_names
            pointing.valid = np.all(pointing.valid_arr)
            pointing.valid_time = now

    def calculate_priorities(self, time, observer):
        '''Calculate priorities at a given time for each pointing.'''

        ## Find base priority based on rank
        priorities = np.array([p.priority for p in self.pointings])

        ## Find ToO values (0 or 1)
        too_mask = np.array([p.too for p in self.pointings])
        too_arr = np.array(np.invert(too_mask), dtype = float)

        ## Find probability values (0 to 1)
        prob_arr = np.array([1-prob if prob != 0 else 0
                             for prob in self.tileprob_arr])
        bad_prob_mask = np.logical_or(prob_arr < 0, prob_arr > 1)
        prob_arr[bad_prob_mask] = 1

        ## Find airmass values (0 to 1)
        # airmass at start
        altaz_now = _get_altaz(time, observer, self.targets)['altaz']
        secz_now = altaz_now.secz

        # airmass at mintime (NB all targets)
        later_arr = time + self.mintimes
        altaz_later = _get_altaz(later_arr, observer, self.targets)['altaz']
        secz_later = altaz_later.secz

        # take average
        secz_arr = (secz_now + secz_later)/2.
        airmass_arr = secz_arr.value/10.
        bad_airmass_mask = np.logical_or(airmass_arr < 0, airmass_arr > 1)
        airmass_arr[bad_airmass_mask] = 1

        ## Find time to set values (0 to 1)
        tts_arr = time_to_set(observer, self.targets, time).to(u.hour).value
        tts_arr = tts_arr/24.
        bad_tts_mask = np.logical_or(tts_arr < 0, tts_arr > 1)
        tts_arr[bad_tts_mask] = 1

        ## Construct the probability based on weightings
        priorities_now = priorities.copy()

        priorities_now += too_arr * too_weight
        priorities_now += prob_arr * prob_weight
        priorities_now += airmass_arr * airmass_weight
        priorities_now += tts_arr * tts_weight

        # check validities, add INVALID_PRIORITY to invalid pointings
        self.check_validities(time, observer)
        valid_mask = np.array([p.valid for p in self.pointings])
        invalid_mask = np.invert(valid_mask)
        priorities_now[invalid_mask] += INVALID_PRIORITY

        # if the current pointing is invalid it must be complete
        # therefore add INVALID_PRIORITY to prevent it coming up again
        current_mask = np.array([p.current for p in self.pointings])
        current_complete_mask = np.logical_and(current_mask, invalid_mask)
        priorities_now[current_complete_mask] += INVALID_PRIORITY

        # save priority_now to pointing
        for pointing, priority_now in zip(self.pointings, priorities_now):
            pointing.priority_now = priority_now

    def get_highest_priority_pointing(self, time, observer):
        '''Return the pointing with the highest priority.'''

        if len(self.pointings) == 0:
            return None

        self.calculate_priorities(time, observer)

        pointing_list = list(self.pointings) # a copy
        pointing_list.sort(key=lambda p: p.priority_now)
        return pointing_list[0]


    def write_to_file(self, time, observer, filename):
        '''Write any time-dependent pointing infomation to a file.'''

        # The queue should already have priorities calculated
        try:
            p = self.pointings[0].priority_now
        except AttributeError:
            message = "Queue has not yet had priorities calculated"
            raise ValueError(message)

        # make copy of pointings
        pointing_list = list(self.pointings)

        # find altaz (should be cached)
        altaz_now = _get_altaz(time, observer, self.targets)['altaz']
        altaz_now_list = list(zip(altaz_now.alt.value, altaz_now.az.value))

        later_arr = time + self.mintimes
        altaz_later = _get_altaz(later_arr, observer, self.targets)['altaz']
        altaz_later_list = list(zip(altaz_later.alt.value, altaz_later.az.value))

        # combine pointings and altaz
        combined = list(zip(pointing_list, altaz_now_list, altaz_later_list))
        combined.sort(key=lambda x: x[0].priority_now)

        # now save as json file
        with open(filename, 'w') as f:
            json.dump(str(time),f)
            f.write('\n')
            json.dump(self.all_constraint_names,f)
            f.write('\n')
            found_highest_survey = False
            for pointing, altaz_now, altaz_later in combined:
                highest_survey = False
                if pointing.survey and not found_highest_survey: # already sorted
                    highest_survey = True
                    found_highest_survey = True
                if (not pointing.survey) or highest_survey:
                    # don't write out all survey tiles, only the highest priority
                    valid_nonbool = [int(b) for b in pointing.valid_arr]
                    con_list = list(zip(pointing.constraint_names, valid_nonbool))
                    json.dump([pointing.id, pointing.priority_now,
                               altaz_now, altaz_later, con_list],f)
                    f.write('\n')


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
    pointings : list of `Pointing`
        An list containing Pointings from the queue_folder.
    """
    files = os.listdir(queue_folder)
    pointings = []

    if files is not None:
        for pointing_file in files:
            path = os.path.join(queue_folder, pointing_file)
            pointings.append(Pointing.from_file(path))

    return pointings


def import_pointings_from_database(time, observer):
    """
    Creates a list of `Pointing` objects from the GOTO database.

    Parameters
    ----------
    time : `~astropy.time.Time`
        The time to fetch the queue for.

    Returns
    -------
    pointings : list of `Pointing`
        An list containing Pointings from the database.
    """
    pointings = []

    with db.open_session() as session:
        current_dbpointing, pending_dbpointings = db.get_filtered_queue(session,
                                                                        time=time,
                                                                        location=observer.location,
                                                                        altitude_limit=HARD_ALT_LIM,
                                                                        hourangle_limit=HARD_HA_LIM)
        for dbpointing in pending_dbpointings:
            pointings.append(Pointing.from_database(dbpointing))
        if current_dbpointing is not None:
            pointings.append(Pointing.from_database(current_dbpointing))

    return np.array(pointings)


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
        if highest_pointing.priority_now < INVALID_PRIORITY:
            return highest_pointing
        else:
            return current_pointing
    elif highest_pointing is None:
        if current_pointing.priority_now > INVALID_PRIORITY:
            return None
        else:
            return current_pointing

    if current_pointing == highest_pointing:
        if current_pointing.priority_now >= INVALID_PRIORITY or highest_pointing.priority_now >= INVALID_PRIORITY:
            return None  # it's either finished or is now illegal
        else:
            return highest_pointing

    if current_pointing.priority_now >= INVALID_PRIORITY:  # current pointing is illegal (finished)
        if highest_pointing.priority_now < INVALID_PRIORITY:  # new pointing is legal
            return highest_pointing
        else:
            return None
    else:  # telescope is observing legally
        if highest_pointing.priority_now >= INVALID_PRIORITY:  # no legal pointings
            return None
        else:  # both are legal
            if current_pointing.survey:  # a survey tile (filler), always slew
                return highest_pointing
            elif highest_pointing.too:  # slew to a ToO, unless now is also a ToO
                if current_pointing.too:
                    return current_pointing
                else:
                    return highest_pointing
            else:  # stay for normal pointings
                return current_pointing


def check_queue(time=None, write_html=False):
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
        Default is `astropy.time.Time.now()`.

    write_html : Bool
        Should the scheduler write the HTML queue webpage?
        Default is False.

    Returns
    -------
    new_pointing : `Pointing`
        The pointing to send to the pilot.
        Could be a new pointing, the current pointing or 'None' (park).
    """

    if time is None:
        time = Time.now()

    GOTO = Observer(astronomy.observatory_location())

    pointings = import_pointings_from_database(time, GOTO)

    if len(pointings) == 0:
        return None

    queue = PointingQueue(pointings)

    highest_pointing = queue.get_highest_priority_pointing(time, GOTO)
    current_pointing = queue.get_current_pointing()

    queue.write_to_file(time, GOTO, queue_file)

    if write_html:
        # since it's now independent, this could be run from elsewhere
        # that would save the scheduler doing it
        html.write_queue_page()

    new_pointing = what_to_do_next(current_pointing, highest_pointing)

    if new_pointing is not None:
        return new_pointing
    else:
        return None
