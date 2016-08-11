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


ExposureSet = namedtuple('ExposureSet',
                         'tels, numexp, exptime, filt, binfac, exptype')


class Observation:
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
        self.exposuresets = []

    def __eq__(self, other):
        try:
            return self.id == other.id
        except AttributeError:
            return False

    def __ne__(self, other):
        return not self == other

    def _as_target(self):
        '''Returns a FixedTarget object for the observation target.'''
        return FixedTarget(coord=self.coord, name=self.name)

    def add_exposureset(self, expset):
        if not isinstance(expset, ExposureSet):
            raise ValueError('exposure set must be an ExposureSet instance')
        self.exposuresets.append(expset)

    @classmethod
    def from_file(cls, fname):
        lines = []
        with open(fname) as f:
            for line in f.readlines():
                if not line.startswith('#'):
                    lines.append(line)
        # first line is observation
        (id, name, ra, dec, priority, tileprob, too, sunalt, minalt,
         mintime, moon, user, start, stop) = lines[0].split()
        new_obs = cls(id, name, ra, dec, priority, tileprob, too, sunalt,
                      minalt, mintime, moon, user, start, stop)
        # remaining lines are exposure sets
        for line in lines[1:]:
            tels, numexp, exptime, filt, binfac, exptype = line.split()
            numexp = int(numexp)
            exptime = float(exptime)*u.second
            binfac = int(binfac)
            expset = ExposureSet(tels, numexp, exptime, filt, binfac, exptype)
            new_obs.add_exposureset(expset)
        return new_obs


class ObservationSet:
    def __init__(self, observations=None):
        if observations is None:
            observations = []
        self.observations = observations
        self.target_arr = []
        self.mintime_arr = []
        self.start_arr = []
        self.stop_arr = []
        self.maxsunalt_arr = []
        self.minalt_arr = []
        self.maxmoon_arr = []
        self.priority_arr = []
        self.tileprob_arr = []
        if len(self.observations) > 0:
            self.initialise()

    def __len__(self):
        return len(self.observations)

    def initialise(self):
        '''Setup the observation set and constraints when initialised.'''
        limits = {'B': 1.0, 'G': 0.65, 'D': 0.25}
        moondist_limit = params.MOONDIST_LIMIT * u.deg
        for obs in self.observations:
            self.target_arr.append(obs._as_target())
            self.mintime_arr.append(obs.mintime)
            self.start_arr.append(obs.start)
            self.stop_arr.append(obs.stop)
            self.maxsunalt_arr.append(obs.maxsunalt)
            self.minalt_arr.append(obs.minalt)
            self.maxmoon_arr.append(limits[obs.maxmoon])
            self.priority_arr.append(obs.priority)
            self.tileprob_arr.append(obs.tileprob)

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

    def check_validities(self, now, observer, obs_now):
        ''' Check if the observations are valid, both now and after mintimes'''

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

        for i in range(len(self.observations)):
            obs = self.observations[i]
            obs.valid_time = now

            # save constraints to observations
            obs.all_constraint_names = self.all_constraint_names
            obs.constraint_names = list(self.constraint_names)
            obs.valid_arr = [x for x in cons_valid_arr[0][i]]
            # current observation doesn't apply the time constraint
            if obs != obs_now:
                obs.constraint_names += self.time_constraint_name
                obs.valid_arr += [x for x in time_cons_valid_arr[0][i]]
            # current observation and queue fillers don't apply mintime cons
            if obs.priority < 5 and obs != obs_now:
                obs.constraint_names += self.mintime_constraint_names
                obs.valid_arr += [x for x in min_cons_valid_arr[i][i]]

            # finally find out if it's valid or not
            obs.valid = np.logical_and.reduce(obs.valid_arr)

    def calculate_priorities(self, time, observer, obs_now):
        ''' Calculate priorities at a given time for each observation.

        Current method (based on pt5m with addition of tiling ranks):

            I.TGGGBBBB

            Base priority (I) is an integer.
            The first decimal place (T) is 0 if the observation is a ToO,
                or 1 if it is not.
            The next X decimal places (e.g. GGG for 3) are reserved for
                the GW tiling probabilities (if a tile, else zeros).
            The final Y decimal places are given by the 'tiebreaker';
                either based on the airmass in the middle of the observation
                or the time to set of the target.
            Finally check if the observation is invalid at the time given,
                if so the priority is increased by 10.
        '''

        ## Find base priority based on rank
        priorities = np.array([obs.priority for obs in self.observations])

        ## Find ToO values (0 or 1)
        too_mask = np.array([obs.too for obs in self.observations])
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

        # check validities, add 10 to invalid observations
        self.check_validities(time, observer, obs_now)
        valid_mask = np.array([obs.valid for obs in self.observations])
        invalid_mask = np.invert(valid_mask)
        priorities_now[invalid_mask] += 10

        # if the current observation is invalid it must be complete
        # therefore add 10 to prevent it coming up again
        if obs_now is not None:
            if obs_now.priority_now > 10:
                id_arr = np.array([obs.id for obs in self.observations])
                current_mask = np.array([id == obs_now.id for id in id_arr])
                priorities_now[current_mask] += 10

        # save priority_now to observation
        for obs, p_now in zip(self.observations, priorities_now):
            obs.priority_now = p_now


def import_obs_from_folder(queue_folder):
    """
    Creates a list of `Observation` objects from a folder containing obs files.

    Parameters
    ----------
    queue_folder : str
        The location of the observation files.

    Returns
    -------
    obsset : `ObservationSet`
        An ObservationSet containing Observations from the queue_folder.
    """
    obsset = ObservationSet()
    queue_files = os.listdir(queue_folder)

    if queue_files is not None:
        for obsfile in queue_files:
            path = os.path.join(queue_folder, obsfile)
            obsset.observations.append(Observation.from_file(path))
    obsset.initialise()
    return obsset


def find_highest_priority(obsset, obs_now, time, write_html=False):
    """
    Calculate priorities for a list of observations at a given time
    and return the observation with the highest priority.

    Parameters
    ----------
    obsset : `ObservationSet`
        An ObservationSet containing Observations to consider.

    time : `~astropy.time.Time`
        The time to calculate the priorities at.

    write_html : bool
        A flag to enable writing of html files.

    Returns
    -------
    obs_hp : `Observation`
        `Observation` object containing the observation with the highest
        calculated priority at the time given.

    obslist_sorted : list of `Observation` objects
        A list of Observations sorted by priority (for html queue page).
    """

    obsset.calculate_priorities(time, GOTO, obs_now)
    for obs in obsset.observations:
        if write_html:
            html.write_obs_flag_files(obs, time, GOTO, obs_now, 1)
            html.write_obs_exp_files(obs)

    obslist = list(obsset.observations)
    obslist.sort(key=lambda x: x.priority_now)

    obs_hp = obslist[0]
    return obs_hp, obslist


def what_to_do_next(obs_now, obs_hp):
    """
    Decide whether to slew to a new target, remain on the current target
    or park the telescope.
    NOTE currently based on pt5m logic, will need revision for GOTO

    Parameters
    ----------
    obs_now : `Observation`
        The current observation.
        `None` if the telescope is idle.

    obs_hp : `Observation`
        The current highest priority object from the queue.
        `None` if the queue is empty.

    Returns
    -------
    obs_new : `Observation`
        The new observation to send to the pilot
        Can be either obs_now (remain on current target), obs_new
        (slew to new target) or `None` (nothing to do, park scope).
    """

    # Deal with either being missing (telescope is idle or queue is empty)
    if obs_now is None and obs_hp is None:
        return obs_now
    elif obs_now is None:
        if obs_hp.priority_now < 10:
            return obs_hp
        else:
            return obs_now
    elif obs_hp is None:
        if obs_now.priority_now > 10:
            return None
        else:
            return obs_now

    if obs_now == obs_hp:
        if obs_now.priority_now >= 10 or obs_hp.priority_now >= 10:
            return None  # it's either finished or is now illegal
        else:
            return obs_hp

    if obs_now.priority_now >= 10:  # current observation is illegal (finished)
        if obs_hp.priority_now < 10:  # new observation is legal
            return obs_hp
        else:
            return None
    else:  # telescope is observing legally
        if obs_hp.priority_now >= 10:  # no legal observations
            return None
        else:  # both are legal
            if obs_now.priority_now > 5:  # a filler, always slew
                return obs_hp
            elif obs_hp.too:  # slew to a ToO, unless now is also a ToO
                if obs_now.too:
                    return obs_now
                else:
                    return obs_hp
            else:  # stay for normal observations
                return obs_now


def check_queue(obs_now, now, write_html=False):
    """
    Check the current observations in the queue, find the highest priority at
    the given time and decide whether to slew to it, stay on the current target
    or park the telescope.

    Parameters
    ----------
    obs_now : `Observation`
        The current observation.
        `None` if the telescope is idle.

    now : `~astropy.time.Time`
        The time to calculate the priorities at.

    Returns
    -------
    obs_new : `Observation`
        The new observation to send to the pilot.
        Could be a new observation, the same as obs_now or 'None' (park).
    """

    obsset = import_obs_from_folder(queue_folder)

    if len(obsset) > 0:
        obs_hp, obslist_sorted = find_highest_priority(obsset, obs_now, now,
                                                       write_html)
    else:
        obs_hp = None
        obslist_sorted = []

    obs_new = what_to_do_next(obs_now, obs_hp)

    if write_html:
        html.write_queue_page(obslist_sorted, obs_now, now)
    return obs_new
