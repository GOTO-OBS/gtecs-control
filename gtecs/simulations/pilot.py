#!/usr/bin/env python
"""A fake pilot to test the scheduler."""

import os
import time

from astropy import units as u
from astropy.time import Time, TimeDelta

from gtecs import params
from gtecs import scheduler
from gtecs.astronomy import get_night_times, get_sunalt, night_startdate
from gtecs.simulations import params as simparams
from gtecs.simulations.misc import estimate_completion_time, set_pointing_status
from gtecs.simulations.weather import Weather

import obsdb as db


class FakePilot(object):
    """A fake, simplified pilot.

    The fake pilot simply checks to see if a more important pointing
    is available from the scheduler, or if it has finished the pointing
    it is supposed to be currently doing.
    """

    def __init__(self):
        self.start_time = Time.now()

        self.current_id = None
        self.current_mintime = None
        self.current_start_time = None
        self.current_duration = None

        self.new_id = None
        self.new_mintime = None

        self.completed_pointings = []
        self.completed_times = []
        self.interrupted_pointings = []
        self.aborted_pointings = []

        self.dome_open = False
        self.pilot_status = None

    def pause_observing(self, session):
        """Pause the system."""
        self.dome_open = False
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
        self.dome_open = True
        self.pilot_status = 'Dome Open'

    def check_weather(self, weather, now, session):
        """Check if the weather is bad and pause if so."""
        # Check if the weather is bad
        if simparams.ENABLE_WEATHER:
            bad = weather.is_bad(now)
        else:
            bad = False

        # Decide to close or open
        if bad and self.dome_open:
            self.pause_observing(session)
        if not bad and not self.dome_open:
            self.resume_observing()

    def log_state(self, now, session):
        """Write the current state of the pilot to a log file."""
        state = 'unknown'
        if self.pilot_status == 'Suspended':
            state = 'manual'
        elif not self.dome_open:
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

            state = 'OBS: {} ("{}"; {:.4f}; {:.4f}; prob: {:.7f}%)'.format(
                self.current_id,
                self.current_name,
                self.current_ra,
                self.current_dec,
                self.current_probability * 100,
            )
        else:
            state = 'idle'
        fname = os.path.join(params.FILE_PATH, 'state_log.txt')
        with open(fname, 'a') as f:
            f.write('%s %s\n' % (now. iso, state))

    def check_scheduler(self, now):
        """Find current highest priority from the scheduler."""
        new_pointing = scheduler.check_queue(now, write_html=simparams.WRITE_HTML)
        if new_pointing is not None:
            self.new_id = new_pointing.db_id
            self.new_mintime = new_pointing.mintime
        else:
            self.new_id = None
            self.new_mintime = None

    def review_target_situation(self, now, session):
        """Check queue for a new target and, if necessary, go to it."""
        if self.current_id:
            print('  current : ID', self.current_id)
        else:
            print('  current :', None)

        # Check if the current pointing has finished
        if self.current_id is not None:
            elapsed = (now - self.current_start_time).to(u.s)
            if elapsed > self.current_duration:
                # Finished
                set_pointing_status(self.current_id, 'completed', session)
                self.completed_pointings.append(self.current_id)
                self.completed_times.append(now)

        # Find current highest priority from the scheduler
        self.check_scheduler(now)
        if self.new_id:
            print('  queue   : ID', self.new_id)
        else:
            print('  queue   :', None)

        # Decide what to do
        if self.new_id == self.current_id:
            if self.current_id is not None:
                elapsed = (now - self.current_start_time).to(u.s)
                print('  still observing {} ({:.0f}/{:.0f})'.format(
                      self.current_id, elapsed.value, self.current_mintime))
            else:
                print('  nothing to observe!')

        elif self.new_id is not None:
            if self.current_id is not None:
                print('  got new pointing from scheduler {}'.format(self.new_id))
                if self.current_id not in self.completed_pointings:
                    # The pointing didn't finish, mark as interrupted
                    set_pointing_status(self.current_id, 'interrupted', session)
                    self.interrupted_pointings.append(self.current_id)
            else:
                print('  unparking')

            print('  starting pointing {}'.format(self.new_id))
            self.current_id = self.new_id
            self.current_mintime = self.new_mintime
            self.current_duration = estimate_completion_time(self.new_id, self.current_id, session)
            self.current_start_time = now
            set_pointing_status(self.current_id, 'running', session)
        else:
            print('  parking')
            self.current_id = None


def run(date):
    """Run the fake pilot."""
    # Create the pilot
    pilot = FakePilot()

    # Get sun rise and set times
    sunset, sunrise = get_night_times(date, horizon=-10 * u.deg)

    # Create weather class
    weather = Weather(sunset, sunrise)

    # Open the database connection
    session = db.load_session()

    # Loop until the night is over
    now = sunset
    print('Starting loop...')
    while now < sunrise:
        # Print loop
        print('{} (sunalt={:>5.1f})'.format(now.strftime('%H:%M:%S'),
                                            get_sunalt(now)))

        # Check the weather
        pilot.check_weather(weather, now, session)

        # Review target if the dome is open
        if pilot.dome_open:
            pilot.review_target_situation(now, session)
        else:
            print('  dome closed')

        # Log the pilot state
        pilot.log_state(now, session)

        # Increment by timestep
        now += TimeDelta(simparams.TIMESTEP)

        # Sleep, if asked
        if simparams.SLEEP_TIME:
            time.sleep(float(simparams.SLEEP_TIME))

    # Remember to close the DB session
    session.close()

    # Print results
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
