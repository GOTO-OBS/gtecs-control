"""Robotic queue scheduler functions."""

import json
import logging
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
# from . import astropy_speedups  # noqa: F401 to ignore unused module
from . import html
from . import params


warnings.simplefilter('ignore', ErfaWarning)


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
        second dimension is given by the number of targets
        third dimension is given by the number of constraints

    """
    if not hasattr(constraints, '__len__'):
        constraints = [constraints]

    applied_constraints = np.array([constraint(observer, targets, times)
                                    for constraint in constraints])
    return np.transpose(applied_constraints)


class ArtificialHorizonConstraint(Constraint):
    """Ensure altitude is above artificial horizon."""

    def __init__(self, az, alt):
        self.get_alt_limit = interpolate.interp1d(az, alt,
                                                  bounds_error=False,
                                                  fill_value='extrapolate')

    def compute_constraint(self, times, observer, targets):
        """Compute the constraint."""
        altaz = _get_altaz(times, observer, targets)['altaz']
        alt_limit = self.get_alt_limit(altaz.az) * u.deg
        return altaz.alt > alt_limit


def time_to_set(observer, targets, now):
    """Time until ``target``s next set below the horizon."""
    # Create grid of times
    set_times = astronomy._rise_set_trig(now, targets, observer.location,
                                         'next', 'setting')
    seconds_until_set = (set_times - now).to(u.s)
    return seconds_until_set


class Pointing(object):
    """A class to contain infomation on each pointing."""

    def __init__(self, db_id, name, ra, dec, rank, weight, num_obs, too,
                 maxsunalt, minalt, mintime, maxmoon, minmoonsep,
                 start, stop, current):
        self.db_id = int(db_id)
        self.name = name
        self.ra = float(ra)
        self.dec = float(dec)
        self.rank = int(rank)
        self.weight = float(weight)
        self.num_obs = int(num_obs)
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
        template = ('Pointing(db_id={}, name={}, ra={}, dec={}, rank={}, weight={}, ' +
                    'num_obs={}, too={}, maxsunalt={}, minalt={}, mintime={}, maxmoon={}, ' +
                    'minmoonsep={}, start={}, stop={}, ' +
                    'current={})')
        return template.format(
            self.db_id, self.name, self.ra, self.dec, self.rank, self.weight,
            self.num_obs, self.too, self.maxsunalt, self.minalt, self.mintime,
            self.maxmoon, self.minmoonsep, self.start, self.stop,
            self.current)

    @classmethod
    def from_database(cls, db_pointing):
        """Import a pointing from the database."""
        # num_obs will be stored on the Mpointing, if it has one
        # If not then it's a one-off, so num_obs=0
        if db_pointing.mpointing:
            num_obs = db_pointing.mpointing.num_completed
        else:
            num_obs = 0

        # weight is stored on the Survey Tile, if it has one
        # If not it effectively contains 100% of the target, so weight=1
        if db_pointing.survey_tile:
            weight = db_pointing.survey_tile.current_weight
        else:
            weight = 1

        # The current pointing has running status
        current = bool(db_pointing.status == 'running')

        # Create pointing object
        pointing = cls(db_id=db_pointing.db_id,
                       name=db_pointing.object_name,
                       ra=db_pointing.ra,
                       dec=db_pointing.dec,
                       rank=db_pointing.rank,
                       weight=weight,
                       num_obs=num_obs,
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

        # Can't do much with no Pointings
        if len(self.pointings) == 0:
            return

        # Create pointing data arrays
        self.targets = coord.SkyCoord([float(p.ra) for p in self.pointings],
                                      [float(p.dec) for p in self.pointings],
                                      unit=u.deg, frame='icrs')
        self.mintimes = u.Quantity([float(p.mintime) for p in self.pointings], unit=u.s)

    def __len__(self):
        return len(self.pointings)

    def __repr__(self):
        template = ('PointingQueue(length={})')
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
                                                     altitude_limit=params.HARD_ALT_LIM,
                                                     hourangle_limit=params.HARD_HA_LIM)

            pointings = [Pointing.from_database(db_pointing) for db_pointing in pending]
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

    def apply_constraints(self, now, observer, horizon):
        """Check if the pointings are valid, both now and after mintimes."""
        # Create Constraints
        self.constraints = {}

        # SunAlt
        maxsunalts = u.Quantity([float(p.maxsunalt) for p in self.pointings], unit=u.deg)
        self.constraints['SunAlt'] = AtNightConstraint(maxsunalts)

        # MinAlt
        minalts = u.Quantity([float(p.minalt) for p in self.pointings], unit=u.deg)
        self.constraints['MinAlt'] = AltitudeConstraint(minalts, None)

        # ArtHoriz
        self.constraints['ArtHoriz'] = ArtificialHorizonConstraint(*horizon)

        # Moon
        moonphases = [float(params.MOON_PHASES[p.maxmoon]) for p in self.pointings]
        self.constraints['Moon'] = MoonIlluminationConstraint(None, moonphases)

        # MoonSep
        minmoonseps = u.Quantity([float(p.minmoonsep) for p in self.pointings], unit=u.deg)
        self.constraints['MoonSep'] = MoonSeparationConstraint(minmoonseps, None)

        # Time
        starts = Time([p.start for p in self.pointings], scale='utc', format='datetime')
        # NB the stop time can be None for non-expiring pointings,
        #    but the constraint needs a Time so just say a long time from now.
        longtime = Time.now() + 10 * u.year
        stops = Time([p.stop if p.stop else longtime.datetime for p in self.pointings],
                     scale='utc', format='datetime')
        self.constraints['Time'] = TimeConstraint(starts, stops)

        # Apply constraints
        normal_cons = ['SunAlt', 'MinAlt', 'ArtHoriz', 'Moon', 'MoonSep']
        cons_valid_arr = apply_constraints([self.constraints[name] for name in normal_cons],
                                           observer, self.targets, now)
        time_cons = ['Time']
        time_cons_valid_arr = apply_constraints([self.constraints[name] for name in time_cons],
                                                observer, self.targets, now)
        mintime_cons = ['SunAlt', 'MinAlt', 'ArtHoriz']
        later_arr = now + u.Quantity([float(p.mintime) for p in self.pointings], unit=u.s)
        min_cons_valid_arr = apply_constraints([self.constraints[name] for name in mintime_cons],
                                               observer, self.targets, later_arr)
        mintime_names = [name + '_mintime' for name in mintime_cons]
        self.all_constraint_names = normal_cons + time_cons + mintime_names

        # Save constraint results on Pointings and calculate if they are valid
        for i, pointing in enumerate(self.pointings):
            # normal constraints
            pointing.constraint_names = list(normal_cons)
            pointing.valid_arr = cons_valid_arr[i]

            # extra constraints
            if not pointing.current:
                pointing.constraint_names += time_cons
                pointing.valid_arr = np.concatenate((pointing.valid_arr,
                                                     time_cons_valid_arr[i]))
                pointing.constraint_names += mintime_names
                pointing.valid_arr = np.concatenate((pointing.valid_arr,
                                                    min_cons_valid_arr[i]))

            # save all constraint names on all
            pointing.all_constraint_names = self.all_constraint_names

            # finally find out if each pointing is valid or not
            pointing.valid = np.all(pointing.valid_arr)
            pointing.valid_time = now

    def calculate_tiebreakers(self, time, observer):
        """Calculate the tiebreaker values for every pointing."""
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
        later_arr = time + u.Quantity([float(p.mintime) for p in self.pointings], unit=u.s)
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

        # Construct the tiebreaker value
        total_weight = params.WEIGHTING_WEIGHT + params.AIRMASS_WEIGHT + params.TTS_WEIGHT
        w_weight = params.WEIGHTING_WEIGHT / total_weight
        a_weight = params.AIRMASS_WEIGHT / total_weight
        t_weight = params.TTS_WEIGHT / total_weight
        tiebreak_arr = (weight_arr * w_weight + airmass_arr * a_weight + tts_arr * t_weight)

        # Save values on the pointings
        for i, pointing in enumerate(self.pointings):
            pointing.altaz_now = altaz_now[i]
            pointing.altaz_later = altaz_later[i]
            pointing.weight = weight_arr[i]
            pointing.airmass = airmass_arr[i]
            pointing.tts = tts_arr[i]
            pointing.tiebreaker = tiebreak_arr[i]

    def get_highest_priority_pointing(self, time, observer, horizon):
        """Return the pointing with the highest priority."""
        # If there are no pointings, return None
        if len(self.pointings) == 0:
            return None

        # Apply constraints and calculate tiebreakers for all pointings
        self.apply_constraints(time, observer, horizon)
        self.calculate_tiebreakers(time, observer)

        # Sort the pointings
        #   - First is by validity
        #   - Then by rank
        #   - Then by ToO flag
        #   - Then by number of times already observed
        #   - Finally use the tiebreaker
        pointings = list(self.pointings)  # make a copy
        pointings.sort(key=lambda p: (not p.valid, p.rank, not p.too, p.num_obs, p.tiebreaker))

        return pointings[0]

    def get_highest_priority_pointings(self, time, observer, horizon, number=1):
        """Return the top X highest priority pointings."""
        # If there are no pointings, return None
        if len(self.pointings) == 0:
            return [None] * number

        # Apply constraints and calculate tiebreakers for all pointings
        self.apply_constraints(time, observer, horizon)
        self.calculate_tiebreakers(time, observer)

        # Sort the pointings
        #   - First is by validity
        #   - Then by rank
        #   - Then by ToO flag
        #   - Then by number of times already observed
        #   - Finally use the tiebreaker
        pointings = list(self.pointings)  # make a copy
        pointings.sort(key=lambda p: (not p.valid, p.rank, not p.too, p.num_obs, p.tiebreaker))

        # Return the top X pointings as requested
        if len(pointings) < number:
            pointings += [None] * (number - len(pointings))
        return pointings[0:number]

    def write_to_file(self, time, observer, filename):
        """Write any time-dependent pointing infomation to a file."""
        # The queue should already have priorities calculated
        if not hasattr(self.pointings[0], 'valid') or not hasattr(self.pointings[0], 'tiebreaker'):
            raise ValueError('Queue has not yet had priorities calculated')

        # get and sort pointings
        pointings = list(self.pointings)  # make a copy
        pointings.sort(key=lambda p: (not p.valid, p.rank, not p.too, p.num_obs, p.tiebreaker))

        # now save as json file
        with open(filename, 'w') as f:
            json.dump(str(time), f)
            f.write('\n')
            json.dump(self.all_constraint_names, f)
            f.write('\n')
            for p in pointings:
                valid_nonbool = [int(b) for b in p.valid_arr]
                json.dump([p.db_id,
                           p.name,
                           1,
                           p.rank,
                           int(p.too),
                           p.num_obs,
                           p.tiebreaker,
                           list(zip(p.constraint_names, valid_nonbool)),
                           ],
                          f)
                f.write('\n')


def what_to_do_next(current_pointing, highest_pointing, log=None):
    """Decide whether to slew to a new target, remain on the current target or park the telescope.

    Parameters
    ----------
    current_pointing : `Pointing`
        The current pointing.
        `None` if the telescope is idle.
    highest_pointing : `Pointing`
        The current highest priority pointing from the queue.
        `None` if the queue is empty.

    log: `logging.Logger`, optional
        log object to direct output to

    Returns
    -------
    new_pointing : `Pointing`
        The new pointing to send to the pilot
        Could be either:
          current_pointing (remain on current target),
          highest_pointing (slew to new target) or
          `None`           (nothing to do, park).

    """
    # Create a logger if one isn't given
    if log is None:
        logging.basicConfig(level=logging.DEBUG)
        log = logging.getLogger('scheduler')

    # Deal with either being missing (telescope is idle or queue is empty)
    if current_pointing is None and highest_pointing is None:
        reason = 'Not doing anything; Nothing to do => Do nothing'
        new_pointing = None
    elif current_pointing is None:
        if highest_pointing.valid:
            reason = 'Not doing anything; HP valid => Do HP'
            new_pointing = highest_pointing
        else:
            reason = 'Not doing anything; HP invalid => Do nothing'
            new_pointing = None
    elif highest_pointing is None:
        if not current_pointing.valid:
            reason = 'CP invalid; Nothing to do => Do nothing'
            new_pointing = None
        else:
            reason = 'CP valid; Nothing to do => Do CP'  # TODO - is that right?
            new_pointing = current_pointing

    elif current_pointing == highest_pointing:
        if not current_pointing.valid or not highest_pointing.valid:
            reason = 'CP==HP and invalid => Do nothing'
            new_pointing = None  # it's either finished or is now illegal
        else:
            reason = 'CP==HP and valid => Do HP'
            new_pointing = highest_pointing

    elif not current_pointing.valid:  # current pointing is illegal (finished)
        if highest_pointing.valid:  # new pointing is legal
            reason = 'CP invalid; HP valid => Do HP'
            new_pointing = highest_pointing
        else:
            reason = 'CP invalid; HP invalid => Do nothing'
            new_pointing = None
    else:  # telescope is observing legally
        if not highest_pointing.valid:  # no legal pointings
            reason = 'CP valid; HP invalid => Do nothing'
            new_pointing = None
        else:  # both are legal
            if highest_pointing.too:  # slew to a ToO, unless now is also a ToO
                if not current_pointing.too:
                    reason = 'CP < HP; CP is not ToO and HP is => Do HP'
                    new_pointing = highest_pointing
                else:
                    reason = 'CP < HP; CP is is ToO and HP is ToO => Do CP'
                    new_pointing = current_pointing
            else:  # stay for normal pointings
                reason = 'CP < HP; but not a ToO => Do CP'
                new_pointing = current_pointing

    # Log decision
    if current_pointing:
        current_str = '{}'.format(current_pointing.db_id)
        if not current_pointing.valid:
            current_str = '*' + current_str
        if current_pointing.too:
            current_str = current_str + '!'
    else:
        current_str = 'None'

    if highest_pointing:
        highest_str = '{}'.format(highest_pointing.db_id)
        if not highest_pointing.valid:
            highest_str = '*' + highest_str
        if highest_pointing.too:
            highest_str = highest_str + '!'
    else:
        highest_str = 'None'

    if new_pointing:
        new_str = '{}'.format(new_pointing.db_id)
        if not new_pointing.valid:
            new_str = '*' + new_str
        if new_pointing.too:
            new_str = new_str + '!'
    else:
        new_str = 'None'

    log.debug('CP={} HP={}: NP={} ({})'.format(current_str, highest_str, new_str, reason))

    return new_pointing


def check_queue(time=None, location=None, horizon=None,
                write_file=True, write_html=False, log=None):
    """Check the queue and decide what to do.

    Check the current pointings in the queue, find the highest priority at
    the given time and decide whether to slew to it, stay on the current target
    or park the telescope.

    Parameters
    ----------
    time : `~astropy.time.Time`, optional
        The time to calculate the priorities at.
        Default is `astropy.time.Time.now()`.

    location : `~astropy.coordinates.EarthLocation`, optional
        The location of the observer on Earth.
        Default is `gtecs.astronomy.observatory_location()`.

    horizon : tuple of (azs, alts), optional
        The horizon limits at the given site.

    write_file : bool, optional
        Should the scheduler write out the queue to a file?
        Default is True.

    write_html : bool, optional
        Should the scheduler write the HTML queue webpage?
        Default is False.

    log: `logging.Logger`, optional
        log object to direct output to

    Returns
    -------
    new_pointing : `Pointing`
        The pointing to send to the pilot.
        Could be a new pointing, the current pointing or 'None' (park).

    """
    # Use current time if not given
    if time is None:
        time = Time.now()

    # Create an observer and load horizon file if not given
    if location is None:
        location = astronomy.observatory_location()
    observer = Observer(location)
    if horizon is None:
        horizon = astronomy.get_horizon()

    # Import the queue from the database
    queue = PointingQueue.from_database(time, observer)
    if len(queue) == 0:
        return None

    # Get the current pointing and the highest priority pointing
    current_pointing = queue.get_current_pointing()
    highest_pointing = queue.get_highest_priority_pointing(time, observer, horizon)

    # Write out the queue file and web pages
    if write_file:
        queue_file = os.path.join(params.QUEUE_PATH, 'queue_info')
        queue.write_to_file(time, observer, queue_file)
    if write_html:
        # TODO this could be run from elsewhere
        html.write_queue_page()

    # Work out what to do next
    new_pointing = what_to_do_next(current_pointing, highest_pointing, log)
    return new_pointing
