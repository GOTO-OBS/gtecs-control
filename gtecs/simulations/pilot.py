#!/usr/bin/env python
"""A fake pilot to test the scheduler."""

import os
from time import sleep

from astropy import units as u
from astropy.time import TimeDelta

import obsdb as db
from obsdb import mark_aborted, mark_completed, mark_interrupted, mark_running

from . import params as simparams
from .misc import estimate_completion_time
from .weather import Weather
from .. import logger
from .. import params
from .. import scheduler
from ..astronomy import get_night_times, night_startdate


class FakePilot(object):
    """A fake, simplified pilot.

    The fake pilot simply checks to see if a more important pointing
    is available from the scheduler, or if it has finished the pointing
    it is supposed to be currently doing.
    """

    def __init__(self, start_time, stop_time, log=None):
        # get a logger for the pilot if none is given
        if not log:
            self.log = logger.get_logger('fake_pilot',
                                         log_stdout=False,
                                         log_to_file=True,
                                         log_to_stdout=True)
        else:
            self.log = log
        self.log.info('Pilot started')

        self.start_time = start_time
        self.stop_time = stop_time

        self.weather = Weather(self.start_time, self.stop_time)

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

    def mark_current_pointing(self, status):
        """Mark the current pointing as completed, aborted etc."""
        self.log.info('marking pointing {} as {}'.format(self.current_id, status))

        if status == 'running':
            mark_running(self.current_id)

        elif status == 'completed':
            mark_completed(self.current_id)
            self.completed_pointings.append(self.current_id)
            self.completed_times.append(self.now)

        elif status == 'aborted':
            mark_aborted(self.current_id)
            self.aborted_pointings.append(self.current_id)
            self.current_id = None
            self.current_duration = None
            self.current_position = None

        elif status == 'interrupted':
            mark_interrupted(self.current_id)
            self.interrupted_pointings.append(self.current_id)
            self.current_id = None
            self.current_duration = None
            self.current_position = None

    def pause_observing(self):
        """Pause the system."""
        self.log.info('Pausing due to bad weather')
        self.dome_open = False
        self.pilot_status = 'Dome Closed'
        if self.current_id is not None:
            self.mark_current_pointing('aborted')

    def resume_observing(self):
        """Unpause the system."""
        self.dome_open = True
        self.pilot_status = 'Dome Open'

    def check_completion(self):
        """Check if the current pointing has finished.

        In the real pilot this happens when all the exposures are complete,
        but since we're not doing that we fake it.
        """
        if self.current_id is not None:
            elapsed = (self.now - self.current_start_time).to(u.s)
            if elapsed > self.current_duration:
                # Finished
                self.mark_current_pointing('completed')

    def check_weather(self):
        """Check if the weather is bad and pause if so."""
        # Check if the weather is bad
        if simparams.ENABLE_WEATHER:
            bad = self.weather.is_bad(self.now)
        else:
            bad = False

        # Decide to close or open
        if bad and self.dome_open:
            self.pause_observing()
        if not bad and not self.dome_open:
            self.resume_observing()

    def check_scheduler(self):
        """Find current highest priority from the scheduler."""
        self.log.info('checking scheduler')
        new_pointing = scheduler.check_queue(self.now,
                                             write_file=simparams.WRITE_QUEUE,
                                             write_html=simparams.WRITE_HTML,
                                             log=self.log)
        if new_pointing is not None:
            self.new_id = new_pointing.db_id
            self.new_mintime = new_pointing.mintime
        else:
            self.new_id = None
            self.new_mintime = None

        if self.new_id != self.current_id:
            self.log.info('scheduler returns {} (NEW)'.format(self.new_id))
        else:
            self.log.info('scheduler returns {}'.format(self.current_id))

    def log_state(self):
        """Write the current state of the pilot to a log file."""
        state = 'unknown'
        if not self.dome_open:
            state = 'closed'
        elif self.current_id is not None:
            with db.open_session() as session:
                current_pointing = db.get_pointing_by_id(session, self.current_id)
                current_name = current_pointing.object_name
                current_ra = current_pointing.ra
                current_dec = current_pointing.dec
                if current_pointing.survey_tile:
                    current_probability = current_pointing.survey_tile.current_weight
                else:
                    current_probability = 0
                state = 'obs;{};{};{:.4f};{:.4f};{:.7f}'.format(
                    self.current_id, current_name, current_ra, current_dec, current_probability)
        elif self.now == self.start_time:
            state = 'starting observing'
        elif self.now > self.stop_time:
            state = 'finished observing'
        else:
            state = 'idle'
        fname = os.path.join(params.FILE_PATH, 'fake_pilot_log')
        with open(fname, 'a') as f:
            f.write('{};{}\n'.format(self.now.iso, state))

    def observe(self):
        """Run through the pilot tasks from the start time to the stop time."""
        self.log.info('observing')

        # Run until stop time is reached
        self.now = self.start_time
        while True:
            # Check if we should stop
            if self.now > self.stop_time:
                break

            # Log time
            self.log.info(self.now.iso)

            # Check if the current pointing is complete
            self.check_completion()

            # Check the weather
            self.check_weather()

            # Find current highest priority from the scheduler
            self.check_scheduler()

            # Decide what to do
            if self.new_id == self.current_id:
                if self.current_id is not None:
                    elapsed = (self.now - self.current_start_time).to(u.s)
                    self.log.info('still observing {} ({:.0f}/{:.0f})'.format(
                        self.current_id, elapsed.value, self.current_mintime))
                else:
                    self.log.info('nothing to observe!')

            elif self.new_id is not None:
                if self.current_id is not None:
                    self.log.info('got new pointing from scheduler {}'.format(self.new_id))
                    if self.current_id not in self.completed_pointings:
                        # The pointing didn't finish, mark as interrupted
                        self.mark_current_pointing('interrupted')
                else:
                    self.log.info('unparking')

                self.log.info('starting pointing {}'.format(self.new_id))
                self.current_id = self.new_id
                self.current_mintime = self.new_mintime
                self.current_duration = estimate_completion_time(self.new_id, self.current_id)
                self.current_start_time = self.now
                self.mark_current_pointing('running')
            else:
                self.log.info('parking')
                self.current_id = None

            # Log the pilot state
            self.log_state()

            # Increase simulation time
            self.now += TimeDelta(simparams.TIMESTEP)

            # Sleep, if asked
            if simparams.SLEEP_TIME:
                sleep(float(simparams.SLEEP_TIME))

        self.log.info('observing completed!')

        # If we were running need to abort
        if self.current_id is not None:
            self.mark_current_pointing('aborted')

        # Final log entry
        self.log_state()


def run(date=None, log=None):
    """Run the fake pilot."""
    # If no date is given use tonight
    if date is None:
        date = night_startdate()

    # Get sun rise and set times
    sunset, sunrise = get_night_times(date, horizon=-10 * u.deg)

    # Create the pilot
    pilot = FakePilot(start_time=sunset, stop_time=sunrise, log=log)

    # Loop until the night is over
    pilot.observe()

    # Print results
    print('{} pointings completed:'.format(len(pilot.completed_pointings)))
    for pointing_id, timedone in zip(pilot.completed_pointings, pilot.completed_times):
        print(pointing_id, timedone.iso)

    print('{} pointings aborted:'.format(len(pilot.aborted_pointings)))
    for pointing_id in pilot.aborted_pointings:
        print(pointing_id)

    print('{} pointings interrupted:'.format(len(pilot.interrupted_pointings)))
    for pointing_id in pilot.interrupted_pointings:
        print(pointing_id)
