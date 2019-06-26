#!/usr/bin/env python
"""A fake pilot to test the scheduler."""

import math
import os
import signal
import time
import traceback
import warnings

import astroplan

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time, TimeDelta

from gototile.skymap import SkyMap
from gototile.skymaptools import tile_skymap
from gototile.telescope import GOTON4

from gtecs import astronomy
from gtecs import misc
from gtecs import params
from gtecs import scheduler

import numpy as np

import obsdb as db

from . import params as simparams
from .weather import Weather


with warnings.catch_warnings():
    warnings.simplefilter('ignore')

# catch ctrl-c
signal.signal(signal.SIGINT, misc.signal_handler)


class DummyScheduler(object):
    """A fake scheduler for testing.

    Initialised with a list of pointing IDs and their durations,
    the Scheduler class can be called to get the next pointing.

    If the current pointing has elapsed, a new pointing ID is returned.

    With each call, there is a finite chance a ToO occurs,
    and a new pointing ID is returned, even if the current pointing has
    not elapsed.
    """

    def __init__(self):
        self.last_called = Time.now()
        self.current_id = None
        self.current_mintime = None
        self.current_priority = None

    def __call__(self):
        """Return a random pointing from the queue."""
        wait_time = np.random.uniform(20, 120)
        time_check = (Time.now() - self.last_called) > wait_time * u.s
        if time_check:
            self.last_called = Time.now()
            with db.open_session() as s:
                curr, pointings = db.get_queue(s)
                new = np.random.choice(pointings)
                self.current_id = new.db_id
                self.current_mintime = new.min_time
                self.current_priority = new.rank
        return self.current_id, self.current_mintime, self.current_priority


def update_skymap_probabilities(session, survey):
    """When a GW tile is observed, update all the Survey tile probabilities.

    THIS SHOUD BE MOVED SOMEWHERE BETTER!
    """
    print('    Updating skymap probabilities: ', end='\t')
    try:
        completed_pointings = session.query(db.Pointing).filter(
            db.Pointing.survey == survey).filter(
            db.Pointing.status == 'completed').all()

        completed_tilenames = [p.grid_tile.name for p in completed_pointings]

        filepath = survey.event.skymap
        skymap = SkyMap(filepath)

        pointings = tile_skymap(skymap, [GOTON4()],
                                observed=[completed_tilenames])

        i = 0
        for tile in survey.survey_tiles:
            old_prob = float(tile.current_weight)
            index = np.where(pointings['fieldname'] == tile.survey_tile.name)[0][0]
            new_prob = float(pointings['prob'][index])

            if not math.isclose(old_prob, new_prob, abs_tol=0.0000001):
                i += 1
                if new_prob < 0.001:
                    new_prob = 0
                tile.current_weight = new_prob
        print(' updated {:.0f} tiles'.format(i))

        session.commit()
        return 0
    except Exception:
        print('ERROR')
        session.rollback()
        return 1


def set_pointing_status(db_id, status, session):
    """Mark a pointing as completed, aborted etc."""
    if status not in ['aborted', 'completed', 'interrupted', 'running']:
        print('Illegal status:', status)
        return 1
    else:
        print('    Marking pointing', db_id, 'as', status)
        try:
            pointing = db.get_pointing_by_id(session, db_id)
            pointing.status = status
            session.commit()
            return 0
        except Exception:
            session.rollback()
            print('Session error!!')
            return 1


def get_night_times(date):
    """Calculate the start and stop times of a given date.

    Defined as sunrise and sunset times for La Palma.
    """
    lapalma = astroplan.Observer.at_site('lapalma')
    # Time(date) gives start of date, add one day to get midnight that night
    midnight = Time(date) + TimeDelta(1 * u.day)
    sunset = lapalma.sun_set_time(midnight, which="previous", horizon=-8 * u.deg)
    sunrise = lapalma.sun_rise_time(midnight, which="next", horizon=-8 * u.deg)
    return sunset, sunrise


def estimate_completion_time(new_id, current_id, session):
    """Extimate the exposure time for a new pointing.

    Based on the combined exposure times in all exposures,
    and the time to move into position.
    """
    total_exptime = 0 * u.s
    new_pointing = db.get_pointing_by_id(session, new_id)
    for exp in new_pointing.exposure_sets:
        total_exptime += ((exp.exptime * u.s + simparams.READOUT_TIME) * exp.num_exp)

    if current_id is not None:
        current_pointing = db.get_pointing_by_id(session, current_id)
        current_position = SkyCoord(current_pointing.ra,
                                    current_pointing.dec,
                                    unit=u.deg, frame='icrs')
        new_position = SkyCoord(new_pointing.ra,
                                new_pointing.dec,
                                unit=u.deg, frame='icrs')
        slew_distance = current_position.separation(new_position)
        slew_time = slew_distance / simparams.SLEWRATE
    else:
        slew_time = 0 * u.s
    return slew_time + total_exptime


class DummyPilot(object):
    """A fake, simplified pilot.

    The dummy pilot simply checks to see if a more important pointing
    is available from the scheduler, or if it has finished the pointing
    it is supposed to be currently doing.
    """

    def __init__(self):
        self.start_time = Time.now()

        self.current_id = None
        self.current_priority = None
        self.current_mintime = None
        self.current_start_time = None
        self.current_duration = None

        self.completed_pointings = []
        self.completed_times = []
        self.interrupted_pointings = []
        self.aborted_pointings = []

        self.dome_status = 0  # 1 = open, 0 = shut
        self.pilot_status = None

    def pause_observing(self, session):
        """Pause the system."""
        self.dome_status = 0
        self.pilot_status = 'Dome Closed'
        if self.current_id is not None:
            print('Aborting ID= %i due to bad weather' % self.current_id)
            self.aborted_pointings.append(self.current_id)
            set_pointing_status(self.current_id, 'aborted', session)
            self.current_id = None
            self.current_duration = None
            self.current_position = None

    def resume_observing(self):
        """Unpause the system."""
        self.dome_status = 1
        self.pilot_status = 'Dome Open'

    def check_weather(self, weather, now, session):
        """Check if the weather is bad and pause if so."""
        bad_weather = weather.is_bad(now)
        if bad_weather and self.dome_status:
            self.pause_observing(session)
        if not bad_weather and not self.dome_status:
            self.resume_observing()

    def log_state(self, now, session):
        """Write the current state of the pilot to a log file."""
        state = 'unknown'
        if self.pilot_status == 'Suspended':
            state = 'manual'
        elif self.dome_status == 0:
            state = 'closed'
        elif self.current_id is not None:
            current_pointing = db.get_pointing_by_id(session, self.current_id)
            self.current_name = current_pointing.object_name
            self.current_ra = current_pointing.ra
            self.current_dec = current_pointing.dec
            survey_tile = current_pointing.survey_tile
            if survey_tile:
                self.current_probability = survey_tile.current_weight
            else:
                self.current_probability = 0

            state = 'OBS: {} ("{}"; {:.4f}; {:.4f}; prob: {:.7f}%; {:.9f})'.format(
                self.current_id,
                self.current_name,
                self.current_ra,
                self.current_dec,
                self.current_probability * 100,
                self.current_priority,)
        else:
            state = 'idle'
        fname = os.path.join(params.FILE_PATH, 'state_log.txt')
        with open(fname, 'a') as f:
            f.write('%s %s\n' % (now. iso, state))

    def review_target_situation(self, now, write_html, session):
        """Check queue for a new target and, if necessary, go to it."""
        # check if current pointing is finished
        if self.current_id is not None:
            time_elapsed = (now - self.current_start_time).to(u.s)
            if time_elapsed > self.current_duration:
                self.current_priority += 100
                set_pointing_status(self.current_id, 'completed', session)

                current_pointing = db.get_pointing_by_id(session, self.current_id)
                survey = current_pointing.survey
                if survey is not None and survey.event.skymap is not None:
                    update_skymap_probabilities(session, survey)
                self.completed_pointings.append(self.current_id)
                self.completed_times.append(now)
            print('  current : ID', self.current_id)  # , self.current_priority)
        else:
            print('  current :', None)

        # find new highest priority from the scheduler
        new_pointing = scheduler.check_queue(now, write_html)
        if new_pointing is not None:
            new_id = new_pointing.db_id
            new_priority = new_pointing.priority
            new_mintime = new_pointing.mintime
            print('  queue   : ID', new_id)  # , new_priority)
        else:
            new_id = None
            new_priority = None
            new_mintime = None
            print('  queue   :', None)

        # what next
        if new_id != self.current_id and new_id not in self.completed_pointings:
            if self.current_id is not None:
                # we're already doing something,
                # mark as finished or interrupted
                try:
                    time_elapsed = (now - self.current_start_time).to(u.s)
                except Exception:
                    time_elapsed = 0.
                if self.current_id not in self.completed_pointings:
                    if time_elapsed > self.current_mintime:
                        set_pointing_status(self.current_id, 'completed', session)
                        self.completed_pointings.append(self.current_id)
                        self.completed_times.append(now)
                    else:
                        set_pointing_status(self.current_id, 'interrupted', session)
                        self.interrupted_pointings.append(self.current_id)

            if new_id is not None:
                print('    -- STARTING NEW POINTING')
                self.current_id = new_id
                self.current_priority = new_priority
                self.current_mintime = new_mintime
                self.current_duration = estimate_completion_time(new_id, self.current_id, session)
                self.current_start_time = now
                set_pointing_status(self.current_id, 'running', session)
            else:
                print('    -- PARKING')
                self.current_id = None


def run(date, sleep_time, write_html):
    """Run the dummy pilot."""
    pilot = DummyPilot()

    # weather has typical timescale = 1h and we lose 10% of time to bad weather
    sunset, sunrise = get_night_times(date)
    if simparams.ENABLE_WEATHER:
        weather = Weather(sunset, sunrise, 1.0, 0.1)

    # loop until night is over
    print('Starting loop...')
    session = db.load_session()
    try:
        now = sunset
        ts = time.time()
        while now < sunrise:
            tprev = ts
            ts = time.time()
            now.format = 'iso'
            now.precision = 0
            sunalt = astronomy.get_sunalt(now)
            print('Loop: {} ({:>5.2f}) ---  dt:{:.3f}s'.format(
                now, sunalt, (ts - tprev)))
            if simparams.ENABLE_WEATHER:
                pilot.check_weather(weather, now, session)
            if pilot.dome_status:  # open
                pilot.review_target_situation(now, bool(write_html), session)
            else:
                print('  dome closed')
            pilot.log_state(now, session)

            # increment by scheduler loop timestep
            now += simparams.DELTA_T
            time.sleep(float(sleep_time))

    except Exception:
        traceback.print_exc()

    session.close()

    n_completed = len(pilot.completed_pointings)
    print('Listing completed pointings ({}) and time done'.format(n_completed))
    for db_id, timedone in zip(pilot.completed_pointings, pilot.completed_times):
        print(db_id, timedone)

    n_aborted = len(pilot.aborted_pointings)
    print('Listing pointings aborted due to bad weather ({})'.format(n_aborted))
    for db_id in pilot.aborted_pointings:
        print(db_id)

    n_interrupted = len(pilot.interrupted_pointings)
    print('Listing pointings interrupted by other pointings ({})'.format(n_interrupted))
    for db_id in pilot.interrupted_pointings:
        print(db_id)


if __name__ == "__main__":
    import argparse

    usage = """python fake_pilot.py date sleep_time write_html"""

    parser = argparse.ArgumentParser(description="run fake pilot for a night",
                                     usage=usage)
    parser.add_argument('date', help="night starting date to simulate")
    parser.add_argument('sleep_time', help="time to sleep each period")
    parser.add_argument('write_html', help="write html webpages?", type=int)
    args = parser.parse_args()

    run(args.date, args.sleep_time, args.write_html)
