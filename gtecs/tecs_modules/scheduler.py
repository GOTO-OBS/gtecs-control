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
import warnings
import signal
from collections import namedtuple

import numpy as np
from scipy import interpolate

from astropy import coordinates as coord, units as u
from astroplan import (FixedTarget, is_observable)
from astroplan import (Constraint, AltitudeConstraint,
                       AtNightConstraint, MoonSeparationConstraint)
from astroplan.moon import moon_illumination
from astroplan.constraints import _get_altaz
from astropy.time import Time

# TeCS modules
from . import params
from . import misc
from . import html

## Setup
# define paths to directories
queue_folder = params.QUEUE_PATH + 'todo/'
horizon_file = params.CONFIG_PATH + 'horizon'

# set observing location
GOTO = params.SITE_OBSERVER

# set debug level
debug = 1

# catch ctrl-c
signal.signal(signal.SIGINT, misc.signal_handler)


def _get_moon_data(times, observer,
                   force_zero_pressure=False):
    """
    Calculate moon altitude az and illumination for an array of
    times for ``observer``.

    Cache the result on the ``observer`` object.

    Parameters
    ----------
    times : `~astropy.time.Time`
        Array of times on which to test the constraint

    observer : `~astroplan.Observer`
        The observer who has constraints ``constraints``

    Returns
    -------
    moon_dict : dict
        Dictionary containing three key-value pairs. (1) 'times' contains the
        times for the computations, (2) 'altaz' contains the
        corresponding alt/az coordinates at those times and (3) contains
        the moon illumination for those times.
    """
    if not hasattr(observer, '_transit_cache'):
        observer._transit_cache = {}

    # convert times to tuple for hashing
    aakey = (tuple(times.jd))

    if aakey not in observer._transit_cache:
        try:
            if force_zero_pressure:
                observer_old_pressure = observer.pressure
                observer.pressure = 0

            altaz = observer.moon_altaz(times)
            illumination = np.array(moon_illumination(times,
                                                      observer.location))
            observer._transit_cache[aakey] = dict(times=times,
                                                  illum=illumination,
                                                  altaz=altaz)
        finally:
            if force_zero_pressure:
                observer.pressure = observer_old_pressure

    return observer._transit_cache[aakey]


def _get_transit_times(times, observer, targets):
    """
    Calculate next transit for an array of times for
    ``targets`` and ``observer``.

    Cache the result on the ``observer`` object.

    Parameters
    ----------
    times : `~astropy.time.Time`
        Array of times on which to test the constraint

    observer : `~astroplan.Observer`
        The observer who has constraints ``constraints``

    targets : {list, `~astropy.coordinates.SkyCoord`, `~astroplan.FixedTarget`}
        Target or list of targets

    Returns
    -------
    time_dict : dict
        Dictionary containing a key-value pair. 'times' contains the
        transit times.
    """
    if not hasattr(observer, '_transit_cache'):
        observer._transit_cache = {}

    # convert times to tuple for hashing
    aakey = (tuple(times.jd), tuple(targets))

    if aakey not in observer._transit_cache:
        transit_times = Time([observer.target_meridian_transit_time(
                              time, target, which='next') for target in targets
                              for time in times])
        observer._transit_cache[aakey] = dict(times=transit_times)

    return observer._transit_cache[aakey]


class MoonIlluminationConstraint(Constraint):
    """
    Constrain the fractional illumination of the Earth's moon.
    Is also valid if moon is not up.
    """
    def __init__(self, min=None, max=None):
        """
        Parameters
        ----------
        min : float or `None` (optional)
            Minimum acceptable fractional illumination (inclusive). `None`
            indicates no limit.
        max : float or `None` (optional)
            Maximum acceptable fractional illumination (inclusive). `None`
            indicates no limit.
        """
        self.min = min
        self.max = max

    def compute_constraint(self, times, observer, targets):
        # first is the moon up?
        cached_moon = _get_moon_data(times, observer)
        moon_alt = cached_moon['altaz'].alt
        moon_alt_mask = moon_alt < 0

        illumination = cached_moon['illum']
        if self.min is None and self.max is not None:
            mask = self.max >= illumination
        elif self.max is None and self.min is not None:
            mask = self.min <= illumination
        elif self.min is not None and self.max is not None:
            mask = ((self.min <= illumination) &
                    (illumination <= self.max))
        else:
            raise ValueError("No max and/or min specified in "
                             "MoonSeparationConstraint.")

        mask = np.logical_or(moon_alt_mask, mask)
        mask = np.tile(mask, len(targets))
        return mask.reshape(len(targets), len(times))


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


class UtcDateConstraint(Constraint):
    """Constrain the observing time to be within UTC datetime limits"""
    def __init__(self, min=None, max=None):
        """
        Parameters
        ----------
        min : `~astropy.time.Time`
            Earliest time (inclusive). `None` indicates no limit.

        max : `~astropy.time.Time`
            Latest time (inclusive). `None` indicates no limit.

        Examples
        --------
        Constrain the observations to targets that are observable between
        23:50 and 04:08 UTC:

        >>> from astroplan import Observer
        >>> from astropy.time import Time
        >>> import datetime as dt
        >>> subaru = Observer.at_site("Subaru")
        >>> t1 = Time("2016-03-28T12:00:00")
        >>> t2 = Time("2016-03-30T12:00:00")
        >>> constraint = UTCDateConstraint(t1,t2)
        """
        self.min = min
        self.max = max

        if self.min is None and self.max is None:
            raise ValueError("You must at least supply either a minimum " +
                             "or a maximum time.")

        if self.min is not None:
            if not isinstance(self.min, Time):
                raise TypeError("Time limits must be specified as " +
                                "astropy.time.Time objects.")
        if self.max is not None:
            if not isinstance(self.max, Time):
                raise TypeError("Time limits must be specified as " +
                                "astropy.time.Time objects.")

    def compute_constraint(self, times, observer, targets):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            min_time = (Time("1950-01-01T00:00:00") if self.min is None
                        else self.min)
            max_time = (Time("2120-01-01T00:00:00") if self.max is None
                        else self.max)
        calculation_times = Time([t for target in targets for t in times])
        mask = np.logical_and(calculation_times > min_time,
                              calculation_times < max_time)
        return mask.reshape((len(targets), len(times)))


ExposureSet = namedtuple('ExposureSet',
                         'tels, numexp, exptime, filt, binfac, exptype')


class Observation:
    def __init__(self, id, name, ra, dec, priority, too, maxsunalt,
                 minalt, mintime, maxmoon, user, start, stop):
        self.id = int(id)
        self.name = name
        self.coord = coord.SkyCoord(ra, dec, unit=u.deg, frame='icrs')
        self.priority = float(priority)
        self.too = bool(int(too))
        self.maxsunalt = float(maxsunalt)*u.deg
        self.minalt = float(minalt)*u.deg
        self.mintime = float(mintime)*u.s
        self.maxmoon = maxmoon
        self.user = user
        self.start = Time(start, scale='utc')
        self.stop = Time(stop, scale='utc')
        self.exposuresets = []
        self.initialise_constraints()

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

    def altaz(self, time, location):
        '''Returns coords transformed to AltAz at given location and time'''
        altaz = self.coord.transform_to(coord.AltAz(obstime=time,
                                                    location=location))
        return altaz

    def initialise_constraints(self):
        '''Setup the constraints when initialised.'''
        limits = {'B': 1.0, 'G': 0.65, 'D': 0.25}
        moondist_limit = params.MOONDIST_LIMIT * u.deg
        self.constraints = [UtcDateConstraint(self.start, self.stop),
                            AtNightConstraint(self.maxsunalt),
                            AltitudeConstraint(self.minalt, None),
                            ArtificialHorizonConstraint(),
                            MoonIlluminationConstraint(None,
                                                       limits[self.maxmoon]),
                            MoonSeparationConstraint(moondist_limit, None)]
        self.constraint_names = ['Date',
                                 'SunAlt',
                                 'MinAlt',
                                 'ArtHoriz',
                                 'Moon',
                                 'MoonSep']

        self.mintime_constraints = []
        self.mintime_constraint_names = []
        for name, constraint in zip(self.constraint_names, self.constraints):
            if name not in ['Date', 'Moon', 'MoonSep']:
                self.mintime_constraints.append(constraint)
                self.mintime_constraint_names.append(name + '_mintime')

    def is_valid(self, times, observer):
        ''' Check if the observation is valid for observer at given time(s)'''
        targets = [self._as_target()]
        later = times + self.mintime
        valid_now = is_observable(self.constraints, observer,
                                  targets, times=times)
        valid_later = is_observable(self.mintime_constraints, observer,
                                    targets, times=later)
        # 'queue fillers' don't care about mintime constraints
        if self.priority < 5:
            return np.logical_and(valid_now, valid_later)
        else:
            return valid_now

    def print_validity(self, times, observer):
        targets = [self._as_target()]
        later = times + self.mintime
        for name, constraint in zip(self.constraint_names, self.constraints):
            print(name,
                  is_observable(constraint, observer, targets, times=times))

        for name, constraint in zip(self.mintime_constraint_names,
                                    self.mintime_constraints):
            print(name,
                  is_observable(constraint, observer, targets, times=later))

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
        (id, name, ra, dec, priority, too, sunalt, minalt,
         mintime, moon, user, start, stop) = lines[0].split()
        new_obs = cls(id, name, ra, dec, priority, too, sunalt,
                      minalt, mintime, moon, user, start, stop)
        # remaining lines are exposure sets
        for line in lines[1:]:
            tels, numexp, exptime, filt, binfac, exptype  = line.split()
            numexp = int(numexp)
            exptime = float(exptime)*u.second
            binfac = int(binfac)
            expset = ExposureSet(tels, numexp, exptime, filt, binfac, exptype)
            new_obs.add_exposureset(expset)
        return new_obs


def import_obs_from_folder(queue_folder):
    """
    Creates a list of `Observation` objects from a folder containing obs files.

    Parameters
    ----------
    queue_folder : str
        The location of the observation files.

    Returns
    -------
    obslist : list of `Observation` objects
        List containing all the observations in the given folder.
        Will return an empty list if the folder was empty.
    """
    obslist = []
    queue_files = os.listdir(queue_folder)

    if queue_files is not None:
        for obsfile in queue_files:
            obslist.append(Observation.from_file(queue_folder + obsfile))
    return obslist


def calculate_priority(obs, time):
    """
    Calculate priority of an observation at a given time.

    Current method (based on pt5m with addition of tiling ranks):
        The base priority is an integer between 0-5 (for normal observations)
            or 6-9 ('queue fillers').
        The first three decimal places are reserved for the GOTO tiling rank.
        Then if it is a ToO the next digit is zero.
        The following digits are given by the airmass in the middle
            of the minimum observation period.
        Finally if the observation is invalid the priority is increased by 10.

    Parameters
    ----------
    obs : `Observation`
        The observation object

    time : `~astropy.time.Time`
        The time to calculate the priorities at

    Returns
    -------
    priority_now : float
        The numeric priority value at the given time.
    """

    # calculate airmass
    midtime = Time(time) + obs.mintime/2.
    altaz_mid = obs.altaz(Time(midtime), GOTO.location)
    airmass_mid = altaz_mid.secz
    if not 1 < airmass_mid < 10:
        airmass_mid = 9.999

    # add airmass onto priority
    if obs.too:
        priority_now = obs.priority + airmass_mid/100000
    else:
        priority_now = obs.priority + airmass_mid/10000

    # if it's currently unobservable add 10
    if not obs.is_valid(time, GOTO):
        priority_now += 10

    return priority_now


def find_highest_priority(obslist, time, obs_now, write_html):
    """
    Calculate priorities for a list of observations at a given time
    and return the observation with the highest priority.

    Parameters
    ----------
    obslist : list of `Observation` objects
        The list of potential observations

    time : `~astropy.time.Time`
        The time to calculate the priorities at

    Returns
    -------
    obs_hp : `Observation`
        `Observation` object containing the observation with the highest
        calculated priority at the time given.
    """

    for obs in obslist:
        obs.priority_now = calculate_priority(obs, time)
        if write_html:
            html.write_obs_flag_files(obs, time, GOTO, 1)
            html.write_obs_exp_files(obs)

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
            return None # it's either finished or is now illegal
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
            elif obs_hp.too:  # slew if ToO, unless now is higher-priority ToO
                if obs_now.too and obs_now.priority_now > obs_hp.priority_now:
                    return obs_now
                else:
                    return obs_hp
            else:  # stay for normal observations
                return obs_now


def check_queue(obs_now, now, write_html):
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

    obslist = import_obs_from_folder(queue_folder)

    if len(obslist) > 0:
        obs_hp, obslist_sorted = find_highest_priority(obslist, now, obs_now, write_html)
    else:
        obs_hp = None
        obslist_sorted = []

    obs_new = what_to_do_next(obs_now, obs_hp)

    if write_html:
        html.write_queue_page(obslist_sorted, obs_now, now)
    return obs_new
