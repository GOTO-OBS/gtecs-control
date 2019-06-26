#!/usr/bin/env python
"""A fake pilot to test the scheduler."""

import os
import time
import traceback

from astropy import units as u
from astropy.time import Time

from gtecs import astronomy
from gtecs import params
from gtecs import scheduler
from gtecs.astronomy import get_night_times, night_startdate
from gtecs.simulations import params as simparams
from gtecs.simulations.misc import estimate_completion_time, set_pointing_status
from gtecs.simulations.skymap import update_skymap_probabilities
from gtecs.simulations.weather import Weather

import obsdb as db


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

    def review_target_situation(self, now, session):
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
        new_pointing = scheduler.check_queue(now,
                                             write_html=simparams.WRITE_HTML)
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


def run(date):
    """Run the dummy pilot."""
    pilot = DummyPilot()

    sunset, sunrise = get_night_times(date, horizon=-10 * u.deg)

    if simparams.ENABLE_WEATHER:
        weather = Weather(sunset, sunrise)

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
                pilot.review_target_situation(now, session)
            else:
                print('  dome closed')
            pilot.log_state(now, session)

            # increment by scheduler loop timestep
            now += simparams.DELTA_T
            time.sleep(float(simparams.SLEEP_TIME))

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

    usage = 'python fake_pilot.py date sleep_time write_html'

    parser = argparse.ArgumentParser(description='Run the fake pilot for a night',
                                     usage=usage)
    parser.add_argument('date',
                        nargs='?',
                        default=night_startdate(),
                        help='night starting date to simulate')
    args = parser.parse_args()

    run(args.date)
