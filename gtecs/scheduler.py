"""Robotic queue scheduler functions."""

import json
import os
import warnings

from astroplan import (AltitudeConstraint, AtNightConstraint,
                       Constraint, MoonIlluminationConstraint, MoonSeparationConstraint,
                       Observer, TimeConstraint)
from astroplan.constraints import _get_altaz

from astropy import coordinates as coord, units as u
from astropy._erfa import ErfaWarning
from astropy.time import Time

import numpy as np

import obsdb as db

from scipy import interpolate

from . import astronomy
from . import astropy_speedups  # noqa: F401 to ignore unused module
from . import html
from . import params


# Setup
warnings.simplefilter("ignore", ErfaWarning)

# priority settings
WEIGHTING_WEIGHT = 10
AIRMASS_WEIGHT = 1
TTS_WEIGHT = 0.1

WEIGHTING_WEIGHT /= (WEIGHTING_WEIGHT + AIRMASS_WEIGHT + TTS_WEIGHT)
AIRMASS_WEIGHT /= (WEIGHTING_WEIGHT + AIRMASS_WEIGHT + TTS_WEIGHT)
TTS_WEIGHT /= (WEIGHTING_WEIGHT + AIRMASS_WEIGHT + TTS_WEIGHT)

# set debug level
debug = 1

# invalid rank
INVALID_PRIORITY = 1000

HARD_ALT_LIM = 10
HARD_HA_LIM = 8
MOON_PHASES = {'B': 1.0, 'G': 0.65, 'D': 0.25}


def apply_constraints(constraints, observer, targets, times):
    """Determine if the targets are observable for given times, observer, and constraints.

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
    """Ensure altitude is above artificial horizon."""

    def __init__(self):
        horizon_file = os.path.join(params.FILE_PATH, 'horizon')
        az, alt = np.loadtxt(horizon_file, usecols=(0, 1)).T
        self.alt = interpolate.interp1d(az, alt, bounds_error=False,
                                        fill_value=12.0)

    def compute_constraint(self, times, observer, targets):
        """Compute the constraint."""
        altaz = _get_altaz(times, observer, targets)['altaz']
        artificial_horizon_alt = self.alt(altaz.az) * u.deg
        return altaz.alt > artificial_horizon_alt


def time_to_set(observer, targets, now):
    """Time until ``target``s next set below the horizon."""
    # Create grid of times
    set_times = astronomy._rise_set_trig(now, targets, observer.location,
                                         'next', 'setting')
    seconds_until_set = (set_times - now).to(u.s)
    return seconds_until_set


class Pointing(object):
    """A class to contain infomation on each pointing."""

    def __init__(self, db_id, ra, dec, rank, weight, too, maxsunalt,
                 minalt, mintime, maxmoon, minmoonsep, start, stop,
                 current):
        self.db_id = int(db_id)
        self.ra = float(ra)
        self.dec = float(dec)
        self.rank = int(rank)
        self.weight = float(weight)
        self.too = bool(int(too))
        self.maxsunalt = float(maxsunalt)
        self.minalt = float(minalt)
        self.mintime = float(mintime)
        self.maxmoon = maxmoon
        self.minmoonsep = minmoonsep
        self.start = start
        self.stop = stop
        self.current = bool(current)

    def __eq__(self, other):
        try:
            return self.db_id == other.db_id
        except AttributeError:
            return False

    def __ne__(self, other):
        return not self == other

    def __repr__(self):
        template = ("Pointing(db_id={}, ra={}, dec={}, rank={}, weight={}, " +
                    "too={}, maxsunalt={}, minalt={}, mintime={}, maxmoon={}, " +
                    "minmoonsep={}, start={}, stop={}, " +
                    "current={})")
        return template.format(
            self.db_id, self.ra, self.dec, self.rank, self.weight,
            self.too, self.maxsunalt, self.minalt, self.mintime,
            self.maxmoon, self.minmoonsep, self.start, self.stop,
            self.current)

    @classmethod
    def from_database(cls, db_pointing):
        """Import a pointing from the database."""
        # not every pointing has an associated survey tile weighting
        # if it doesn't, it effectively contains 100% of the target so weight=1
        if db_pointing.survey_tile:
            weight = db_pointing.survey_tile.current_weight
        else:
            weight = 1
        # the current pointing has running status
        current = bool(db_pointing.status == 'running')

        # create pointing object
        pointing = cls(db_id=db_pointing.db_id,
                       ra=db_pointing.ra,
                       dec=db_pointing.dec,
                       rank=db_pointing.rank,
                       weight=weight,
                       too=db_pointing.too,
                       maxsunalt=db_pointing.max_sunalt,
                       minalt=db_pointing.min_alt,
                       mintime=db_pointing.min_time,
                       maxmoon=db_pointing.max_moon,
                       minmoonsep=db_pointing.min_moonsep,
                       start=db_pointing.start_time,
                       stop=db_pointing.stop_time,
                       current=current)
        return pointing


class PointingQueue(object):
    """A class to represent a queue of pointings."""

    def __init__(self, pointings=None):
        if pointings is None:
            pointings = []
        self.pointings = pointings
        if len(self.pointings) > 0:
            self.initialise()

    def __len__(self):
        return len(self.pointings)

    def __repr__(self):
        template = ("PointingQueue(length={})")
        return template.format(len(self.pointings))

    @classmethod
    def from_database(cls, time, observer):
        """Create a Pointing Queue from current Pointings in the database.

        Parameters
        ----------
        time : `~astropy.time.Time`
            The time to fetch the queue for.
        observer : `astroplan.Observer`
            The observer of the pointings

        Returns
        -------
        pointings : list of `Pointing`
            An list containing Pointings from the database.

        """
        with db.open_session() as session:
            current, pending = db.get_filtered_queue(session,
                                                     time=time,
                                                     location=observer.location,
                                                     altitude_limit=HARD_ALT_LIM,
                                                     hourangle_limit=HARD_HA_LIM)

            pointings = [Pointing.from_database(dbpointing) for dbpointing in pending]
            if current:
                pointings.append(Pointing.from_database(current))
            queue = cls(np.array(pointings))
        return queue

    def get_current_pointing(self):
        """Return the current pointing from the queue."""
        for p in self.pointings:
            if p.current:
                return p
        return None

    def initialise(self):
        """Set up the queue and constraints when initialised."""
        # create pointing data arrays
        self.ra_arr = np.array([float(p.ra) for p in self.pointings])
        self.dec_arr = np.array([float(p.dec) for p in self.pointings])
        self.mintime_arr = np.array([float(p.mintime) for p in self.pointings])
        self.start_arr = np.array([p.start for p in self.pointings])
        self.stop_arr = np.array([p.stop for p in self.pointings])
        self.maxsunalt_arr = np.array([float(p.maxsunalt) for p in self.pointings])
        self.minalt_arr = np.array([float(p.minalt) for p in self.pointings])
        self.maxmoon_arr = np.array([float(MOON_PHASES[p.maxmoon]) for p in self.pointings])
        self.minmoonsep_arr = np.array([float(p.minmoonsep) for p in self.pointings])

        # Create normal constraints
        # Apply to every pointing
        self.targets = coord.SkyCoord(self.ra_arr, self.dec_arr,
                                      unit=u.deg, frame='icrs')
        self.mintimes = u.Quantity(self.mintime_arr, unit=u.s)
        self.maxsunalts = u.Quantity(self.maxsunalt_arr, unit=u.deg)
        self.minalts = u.Quantity(self.minalt_arr, unit=u.deg)
        self.minmoonseps = u.Quantity(self.minmoonsep_arr, unit=u.deg)

        self.constraints = [AtNightConstraint(self.maxsunalts),
                            AltitudeConstraint(self.minalts, None),
                            ArtificialHorizonConstraint(),
                            MoonIlluminationConstraint(None, self.maxmoon_arr),
                            MoonSeparationConstraint(self.minmoonseps, None)]
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
        # The stop time can be None for non-expiring pointings,
        # but the constraint needs a Time so just say a long time from now.
        longtime = Time.now() + 10 * u.year
        self.stops_t = Time([stoptime if stoptime else longtime.datetime
                            for stoptime in self.stop_arr[self.time_mask]],
                            scale='utc', format='datetime')

        self.time_constraint = [TimeConstraint(self.starts_t, self.stops_t)]
        self.time_constraint_name = ['Time']

        # Create mintime constraints
        # Apply to all non-current pointings
        self.mintime_mask = np.array([not p.current for p in self.pointings])

        if np.all(self.mintime_mask):
            # no current pointing => use existing objects
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
        """Check if the pointings are valid, both now and after mintimes."""
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
        """Calculate priorities at a given time for each pointing."""
        # Find ToO values (0 or 1)
        too_mask = np.array([p.too for p in self.pointings])
        too_arr = np.array(np.invert(too_mask), dtype=float)

        # Find weight values (0 to 1)
        weights = np.array([float(p.weight) for p in self.pointings])
        weight_arr = 1 - weights
        bad_weight_mask = np.logical_or(weight_arr < 0, weight_arr > 1)
        weight_arr[bad_weight_mask] = 1

        # Find airmass values (0 to 1)
        # airmass at start
        altaz_now = _get_altaz(time, observer, self.targets)['altaz']
        secz_now = altaz_now.secz

        # airmass at mintime (NB all targets)
        later_arr = time + self.mintimes
        altaz_later = _get_altaz(later_arr, observer, self.targets)['altaz']
        secz_later = altaz_later.secz

        # take average
        secz_arr = (secz_now + secz_later) / 2.
        airmass_arr = secz_arr.value / 10.
        bad_airmass_mask = np.logical_or(airmass_arr < 0, airmass_arr > 1)
        airmass_arr[bad_airmass_mask] = 1

        # Find time to set values (0 to 1)
        tts_arr = time_to_set(observer, self.targets, time).to(u.hour).value
        tts_arr = tts_arr / 24.
        bad_tts_mask = np.logical_or(tts_arr < 0, tts_arr > 1)
        tts_arr[bad_tts_mask] = 1

        # Find base priority based on rank
        priorities = np.array([float(p.rank) for p in self.pointings])

        # Construct the current priority based on weightings
        priorities += 0.1 * too_arr
        priorities += 0.01 * (weight_arr * WEIGHTING_WEIGHT +
                              airmass_arr * AIRMASS_WEIGHT +
                              tts_arr * TTS_WEIGHT)

        # check validities, add INVALID_PRIORITY to invalid pointings
        self.check_validities(time, observer)
        valid_mask = np.array([p.valid for p in self.pointings])
        invalid_mask = np.invert(valid_mask)
        priorities[invalid_mask] += INVALID_PRIORITY

        # if the current pointing is invalid it must be complete
        # therefore add INVALID_PRIORITY to prevent it coming up again
        current_mask = np.array([p.current for p in self.pointings])
        current_complete_mask = np.logical_and(current_mask, invalid_mask)
        priorities[current_complete_mask] += INVALID_PRIORITY

        # save current priority to pointing
        for pointing, priority in zip(self.pointings, priorities):
            pointing.priority = priority

    def get_highest_priority_pointing(self, time, observer):
        """Return the pointing with the highest priority."""
        if len(self.pointings) == 0:
            return None

        self.calculate_priorities(time, observer)

        pointing_list = list(self.pointings)  # a copy
        pointing_list.sort(key=lambda p: p.priority)
        return pointing_list[0]

    def write_to_file(self, time, observer, filename):
        """Write any time-dependent pointing infomation to a file."""
        # The queue should already have priorities calculated
        try:
            self.pointings[0].priority
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
        combined.sort(key=lambda x: x[0].priority)

        # now save as json file
        with open(filename, 'w') as f:
            json.dump(str(time), f)
            f.write('\n')
            json.dump(self.all_constraint_names, f)
            f.write('\n')
            for pointing, altaz_now, altaz_later in combined:
                valid_nonbool = [int(b) for b in pointing.valid_arr]
                con_list = list(zip(pointing.constraint_names, valid_nonbool))
                json.dump([pointing.db_id, pointing.priority,
                           altaz_now, altaz_later, con_list], f)
                f.write('\n')


def what_to_do_next(current_pointing, highest_pointing):
    """Decide whether to slew to a new target, remain on the current target or park the telescope.

    NOTE currently based on pt5m logic, will need revision for GOTO.

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
          `None`           (nothing to do, park).

    """
    # Deal with either being missing (telescope is idle or queue is empty)
    if current_pointing is None and highest_pointing is None:
        print('Not doing anything; Nothing to do => Do nothing', end='\t')
        return None
    elif current_pointing is None:
        if highest_pointing.priority < INVALID_PRIORITY:
            print('Not doing anything; HP valid => Do HP', end='\t')
            return highest_pointing
        else:
            print('Not doing anything; HP invalid => Do nothing', end='\t')
            return None
    elif highest_pointing is None:
        if current_pointing.priority > INVALID_PRIORITY:
            print('CP invalid; Nothing to do => Do nothing', end='\t')
            return None
        else:
            print('CP valid; Nothing to do => Do CP', end='\t')  # TODO - is that right?
            return current_pointing

    if current_pointing == highest_pointing:
        if (current_pointing.priority >= INVALID_PRIORITY or
                highest_pointing.priority >= INVALID_PRIORITY):
            print('CP==HP and invalid => Do nothing', end='\t')
            return None  # it's either finished or is now illegal
        else:
            print('CP==HP and valid => Do HP', end='\t')
            return highest_pointing

    if current_pointing.priority >= INVALID_PRIORITY:  # current pointing is illegal (finished)
        if highest_pointing.priority < INVALID_PRIORITY:  # new pointing is legal
            print('CP invalid; HP valid => Do HP', end='\t')
            return highest_pointing
        else:
            print('CP invalid; HP invalid => Do nothing', end='\t')
            return None
    else:  # telescope is observing legally
        if highest_pointing.priority >= INVALID_PRIORITY:  # no legal pointings
            print('CP valid; HP invalid => Do nothing', end='\t')
            return None
        else:  # both are legal
            if highest_pointing.too:  # slew to a ToO, unless now is also a ToO
                if not current_pointing.too:
                    print('CP < HP; CP is not ToO and HP is => Do HP', end='\t')
                    return highest_pointing
                else:
                    print('CP < HP; CP is is ToO and HP is ToO => Do CP', end='\t')
                    return current_pointing
            else:  # stay for normal pointings
                print('CP < HP; but not a ToO => Do CP', end='\t')
                return current_pointing


def check_queue(time=None, write_html=False):
    """Check the queue and decide what to do.

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

    observer = Observer(astronomy.observatory_location())

    queue = PointingQueue.from_database(time, observer)

    if len(queue) == 0:
        return None

    highest_pointing = queue.get_highest_priority_pointing(time, observer)
    current_pointing = queue.get_current_pointing()

    if current_pointing is not None:
        print('CP: {}'.format(current_pointing.db_id), end='\t')
    else:
        print('CP: None', end='\t')
    if highest_pointing is not None:
        print('HP: {}'.format(highest_pointing.db_id), end='\t')
    else:
        print('HP: None', end='\t')

    queue_file = os.path.join(params.QUEUE_PATH, 'queue_info')
    queue.write_to_file(time, observer, queue_file)

    if write_html:
        # since it's now independent, this could be run from elsewhere
        # that would save the scheduler doing it
        html.write_queue_page()

    new_pointing = what_to_do_next(current_pointing, highest_pointing)
    if new_pointing is not None:
        print('NP: {}'.format(new_pointing.db_id))
    else:
        print('NP: None')

    if new_pointing is not None:
        return new_pointing
    else:
        return None
