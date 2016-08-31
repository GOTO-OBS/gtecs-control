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

from astroplan.target import get_icrs_skycoord
import astroplan.constraints as constraints

def _get_altaz(times, observer, targets, force_zero_pressure=False):
    """
    Calculate alt/az for ``target`` at times linearly spaced between
    the two times in ``time_range`` with grid spacing ``time_resolution``
    for ``observer``.

    Cache the result on the ``observer`` object.

    NOTE: New, faster version using get_icrs_skycoord patched in.
          Can be removed if AstroPlan is updated to include it.

    Parameters
    ----------
    times : `~astropy.time.Time`
        Array of times on which to test the constraint

    targets : {list, `~astropy.coordinates.SkyCoord`, `~astroplan.FixedTarget`}
        Target or list of targets

    observer : `~astroplan.Observer`
        The observer who has constraints ``constraints``

    time_resolution : `~astropy.units.Quantity` (optional)
        Set the time resolution in calculations of the altitude/azimuth

    Returns
    -------
    altaz_dict : dict
        Dictionary containing two key-value pairs. (1) 'times' contains the
        times for the alt/az computations, (2) 'altaz' contains the
        corresponding alt/az coordinates at those times.
    """
    if not hasattr(observer, '_altaz_cache'):
        observer._altaz_cache = {}

    targets = get_icrs_skycoord(targets)
    if targets.isscalar:
        targets = coord.SkyCoord([targets])

    # convert times, targets to tuple for hashing
    aakey = (tuple(times.jd), targets)

    if aakey not in observer._altaz_cache:
        try:
            if force_zero_pressure:
                observer_old_pressure = observer.pressure
                observer.pressure = 0

            altaz = targets[:, np.newaxis].transform_to(
                coord.AltAz(obstime=times, location=observer.location)
                )
            observer._altaz_cache[aakey] = dict(times=times,
                                                altaz=altaz)
        finally:
            if force_zero_pressure:
                observer.pressure = observer_old_pressure

    return observer._altaz_cache[aakey]

constraints._get_altaz = _get_altaz # overwrite before importing

from astroplan import (FixedTarget, Constraint, TimeConstraint,
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

## Setup
# define paths to directories
queue_folder = params.QUEUE_PATH + 'todo/'
horizon_file = params.CONFIG_PATH + 'horizon'

# set observing location
GOTO = params.SITE_OBSERVER

# priority settings
too_weight = 0.1
prob_dp = 3
prob_weight = 0.1
airmass_dp = 5
airmass_weight = 0.00001
tts_dp = 5
tts_weight = 0.00001

tts_horizon = 10*u.deg

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
        cached_altaz = _get_altaz(times, observer, targets)
        altaz = cached_altaz['altaz']
        artificial_horizon_alt = self.alt(altaz.az)*u.deg
        return altaz.alt > artificial_horizon_alt


def _two_point_interp(times, altitudes, horizon=0*u.deg):
    if not isinstance(times, Time):
        return Time(-999, format='jd')
    else:
        slope = (altitudes[1] - altitudes[0])/(times[1].jd - times[0].jd)
        time = times[1].jd - ((altitudes[1] - horizon)/slope).value
        return Time(time, format='jd')

def time_to_set(observer, targets, time, horizon=0*u.deg):
    """
    Time until ``target``s next set below the horizon
    """

    time_grid = np.linspace(0, 1, 150)*u.day
    times = time + time_grid

    cached_altaz = _get_altaz(times, observer, targets)
    altaz = cached_altaz['altaz']
    alts = altaz.alt

    n_targets = alts.shape[0]

    # Find index where altitude goes from above to below horizon
    condition = (alts[:, :-1] > horizon) * (alts[:, 1:] < horizon)
    crossing_target_inds, crossing_time_inds = np.nonzero(condition)


    # If some targets never cross the horizon
    target_inds = np.arange(n_targets)
    time_inds = [crossing_time_inds[np.where(crossing_target_inds==i)][0]
                   if i in crossing_target_inds
                   else np.nan
                   for i in np.arange(n_targets)]

    # Find the upper and lower time and altitude limits for each target
    time_lims = [times[i:i+2] if not np.isnan(i) else np.nan
                 for i in time_inds]
    alt_lims = [alts[i, j:j+2] if not np.isnan(j) else np.nan
                for i, j in zip(target_inds, time_inds)]

    set_times = Time([_two_point_interp(time_lims, alt_lims, horizon)
                     for time_lims, alt_lims in zip(time_lims, alt_lims)])

    seconds_until_set = (set_times - time).to(u.s)

    return seconds_until_set


Exposure = namedtuple('Exposure', 'tels, numexp, exptime, filt, binfac, exptype')


class Pointing:
    def __init__(self, id, name, ra, dec, priority, tileprob, too, maxsunalt,
                 minalt, mintime, maxmoon, user, start, stop):
        self.id = int(id)
        self.name = name
        self.coord = coord.SkyCoord(ra, dec, unit=u.deg, frame='icrs')
        self.priority = float(priority)
        self.tileprob = float(tileprob)
        self.too = bool(int(too))
        self.maxsunalt = float(maxsunalt)*u.deg
        self.minalt = float(minalt)*u.deg
        self.mintime = float(mintime)*u.s
        self.maxmoon = maxmoon
        self.user = user
        self.start = Time(start, scale='utc')
        self.stop = Time(stop, scale='utc')
        self.exposures = []

    def __eq__(self, other):
        try:
            return self.id == other.id
        except AttributeError:
            return False

    def __ne__(self, other):
        return not self == other

    def _as_target(self):
        '''Returns a FixedTarget object for the pointing target.'''
        return FixedTarget(coord=self.coord, name=self.name)

    def add_exposure(self, exp):
        if not isinstance(exp, Exposure):
            raise ValueError('exposure must be an Exposure instance')
        self.exposures.append(exp)

    @classmethod
    def from_file(cls, fname):
        lines = []
        with open(fname) as f:
            for line in f.readlines():
                if not line.startswith('#'):
                    lines.append(line)
        # first line is the pointing
        (id, name, ra, dec, priority, tileprob, too, sunalt, minalt,
         mintime, moon, user, start, stop) = lines[0].split()
        pointing = cls(id, name, ra, dec, priority, tileprob, too, sunalt,
                       minalt, mintime, moon, user, start, stop)
        # remaining lines are exposures
        for line in lines[1:]:
            tels, numexp, exptime, filt, binfac, exptype = line.split()
            numexp = int(numexp)
            exptime = float(exptime)*u.second
            binfac = int(binfac)
            exp = Exposure(tels, numexp, exptime, filt, binfac, exptype)
            pointing.add_exposure(exp)
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
                       name      = dbPointing.objectName,
                       ra        = dbPointing.ra,
                       dec       = dbPointing.decl,
                       priority  = dbPointing.rank,
                       tileprob  = tileprob,
                       too       = dbPointing.ToO,
                       maxsunalt = dbPointing.maxSunAlt,
                       minalt    = dbPointing.minAlt,
                       mintime   = dbPointing.minTime,
                       maxmoon   = dbPointing.maxMoon,
                       user      = dbPointing.userKey,
                       start     = dbPointing.startUTC,
                       stop      = dbPointing.stopUTC)
        # copy exposures
        if dbPointing.exposures is not None:
            for dbExp in dbPointing.exposures:
                exp = Exposure(tels    = dbExp.otaMask,
                               numexp  = dbExp.numexp,
                               exptime = dbExp.expTime,
                               filt    = dbExp.filt,
                               binfac  = dbExp.binning,
                               exptype = dbExp.typeFlag)
                pointing.add_exposure(exp)
        return pointing


class Queue:
    def __init__(self, pointings=None):
        if pointings is None:
            pointings = []
        self.pointings = pointings
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

    def initialise(self):
        '''Setup the queue and constraints when initialised.'''
        limits = {'B': 1.0, 'G': 0.65, 'D': 0.25}
        moondist_limit = params.MOONDIST_LIMIT * u.deg
        for pointing in self.pointings:
            self.target_arr.append(pointing._as_target())
            self.mintime_arr.append(pointing.mintime)
            self.start_arr.append(pointing.start)
            self.stop_arr.append(pointing.stop)
            self.maxsunalt_arr.append(pointing.maxsunalt)
            self.minalt_arr.append(pointing.minalt)
            self.maxmoon_arr.append(limits[pointing.maxmoon])
            self.priority_arr.append(pointing.priority)
            self.tileprob_arr.append(pointing.tileprob)

        self.target_arr = get_icrs_skycoord(self.target_arr)

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

    def check_validities(self, now, observer, current_pointing):
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
        later_arr = [now + mintime for mintime in self.mintime_arr]
        min_cons_valid_arr = apply_constraints(self.mintime_constraints,
                                               observer, self.target_arr,
                                               later_arr)

        for i in range(len(self.pointings)):
            pointing = self.pointings[i]
            pointing.valid_time = now

            # save constraints to the pointing objects
            pointing.all_constraint_names = self.all_constraint_names
            pointing.constraint_names = list(self.constraint_names)
            pointing.valid_arr = [x for x in cons_valid_arr[0][i]]
            # the current pointing doesn't apply the time constraint
            if pointing != current_pointing:
                pointing.constraint_names += self.time_constraint_name
                pointing.valid_arr += [x for x in time_cons_valid_arr[0][i]]
            # current pointing and queue fillers don't apply mintime cons
            if pointing.priority < 5 and pointing != current_pointing:
                pointing.constraint_names += self.mintime_constraint_names
                pointing.valid_arr += [x for x in min_cons_valid_arr[i][i]]

            # finally find out if it's valid or not
            pointing.valid = np.logical_and.reduce(pointing.valid_arr)

    def calculate_priorities(self, time, observer, current_pointing):
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
        cached_altaz_now = _get_altaz(Time([time]), observer, self.target_arr)
        altaz_now = cached_altaz_now['altaz']
        secz_now = np.array([x[0].value for x in altaz_now.secz])

        # airmass at mintime
        later_arr = Time([time + mintime for mintime in self.mintime_arr])
        cached_altaz_later = _get_altaz(later_arr, observer, self.target_arr)
        altaz_later = cached_altaz_later['altaz']
        secz_later = np.diag(altaz_later.secz).astype('float')

        # take average
        secz_arr = np.array((secz_now + secz_later)/2.)
        airmass_arr = np.around(secz_arr/10., decimals = airmass_dp)
        bad_airmass_mask = np.logical_or(airmass_arr < 0, airmass_arr >= 1)
        airmass_arr[bad_airmass_mask] = float('0.' + '9' * airmass_dp)

        ## Find time to set values (0.00.. to 0.99..)
        tts_sec_arr = time_to_set(observer, self.target_arr, time, tts_horizon)
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
        self.check_validities(time, observer, current_pointing)
        valid_mask = np.array([p.valid for p in self.pointings])
        invalid_mask = np.invert(valid_mask)
        priorities_now[invalid_mask] += 10

        # if the current pointing is invalid it must be complete
        # therefore add 10 to prevent it coming up again
        if current_pointing is not None:
            if current_pointing.priority_now > 10:
                id_arr = np.array([p.id for p in self.pointings])
                current_mask = np.array([id == current_pointing.id for id in id_arr])
                priorities_now[current_mask] += 10

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


def import_pointings_from_database():
    """
    Creates a list of `Pointing` objects from the GOTO database.

    Returns
    -------
    queue : `Queue`
        An Queue containing Pointings from the database.
    """
    queue = Queue()
    with db.open_session() as session:
        current_pointing, pending_pointings = db.get_queue(session)
        if pending_pointings is not None:
            for dbPointing in pending_pointings:
                queue.pointings.append(Pointing.from_database(dbPointing))
    queue.initialise()
    return queue


def find_highest_priority(queue, current_pointing, time, write_html=False):
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

    queue.calculate_priorities(time, GOTO, current_pointing)
    for pointing in queue.pointings:
        if write_html:
            html.write_flag_files(pointing, time, GOTO, current_pointing, 1)
            html.write_exp_files(pointing)

    pointinglist = list(queue.pointings)
    pointinglist.sort(key=lambda x: x.priority_now)

    highest_pointing = pointinglist[0]
    return highest_pointing, pointinglist


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
        if highest_pointing.priority_now < 10:
            return highest_pointing
        else:
            return current_pointing
    elif highest_pointing is None:
        if current_pointing.priority_now > 10:
            return None
        else:
            return current_pointing

    if current_pointing == highest_pointing:
        if current_pointing.priority_now >= 10 or highest_pointing.priority_now >= 10:
            return None  # it's either finished or is now illegal
        else:
            return highest_pointing

    if current_pointing.priority_now >= 10:  # current pointing is illegal (finished)
        if highest_pointing.priority_now < 10:  # new pointing is legal
            return highest_pointing
        else:
            return None
    else:  # telescope is observing legally
        if highest_pointing.priority_now >= 10:  # no legal pointings
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


def check_queue(current_pointing, now, write_html=False):
    """
    Check the current pointings in the queue, find the highest priority at
    the given time and decide whether to slew to it, stay on the current target
    or park the telescope.

    Parameters
    ----------
    current_pointing : `Pointing`
        The current pointing.
        `None` if the telescope is idle.

    now : `~astropy.time.Time`
        The time to calculate the priorities at.

    Returns
    -------
    new_pointing : `Pointing`
        The pointing to send to the pilot.
        Could be a new pointing, the current pointing or 'None' (park).
    """

    queue = import_pointings_from_database()

    if len(queue) > 0:
        highest_pointing, pointinglist = find_highest_priority(queue, current_pointing,
                                                               now, write_html)
    else:
        highest_pointing = None
        pointinglist = []

    new_pointing = what_to_do_next(current_pointing, highest_pointing)

    if write_html:
        html.write_queue_page(pointinglist, current_pointing, now)
    return new_pointing
