#!/usr/bin/env python
"""A fake pilot to test the scheduler."""

import os
from time import sleep

from astroplan import Observer

from astropy import units as u
from astropy.coordinates import EarthLocation
from astropy.time import TimeDelta

from obsdb import mark_aborted, mark_completed, mark_interrupted, mark_running

from . import params as simparams
from .database import reschedule_pointing
from .misc import estimate_completion_time
from .weather import Weather
from .. import logger
from .. import params
from .. import scheduler


class FakePilot(object):
    """A fake, simplified pilot.

    The fake pilot simply checks to see if a more important pointing
    is available from the scheduler, or if it has finished the pointing
    it is supposed to be currently doing.
    """

    def __init__(self, sites, start_time, stop_time, log=None):
        # get a logger for the pilot if none is given
        if not log:
            self.log = logger.get_logger('fake_pilot',
                                         log_stdout=False,
                                         log_to_file=True,
                                         log_to_stdout=True)
        else:
            self.log = log
        self.log.info('Pilot started')

        if isinstance(sites, EarthLocation):
            self.sites = [sites]
        else:
            self.sites = sites
        self.telescope_ids = range(len(self.sites))
        self.observers = [Observer(site) for site in self.sites]

        self.start_time = start_time
        self.stop_time = stop_time

        self.weather = Weather(self.start_time, self.stop_time)

        self.current_ids = [None] * len(self.telescope_ids)
        self.current_mintimes = [None] * len(self.telescope_ids)
        self.current_start_times = [None] * len(self.telescope_ids)
        self.current_durations = [None] * len(self.telescope_ids)

        self.new_ids = [None] * len(self.telescope_ids)
        self.new_mintimes = [None] * len(self.telescope_ids)

        self.completed_pointings = [[]] * len(self.telescope_ids)
        self.completed_times = [[]] * len(self.telescope_ids)
        self.interrupted_pointings = [[]] * len(self.telescope_ids)
        self.aborted_pointings = [[]] * len(self.telescope_ids)

        self.dome_open = [False] * len(self.telescope_ids)
        self.pilot_status = [None] * len(self.telescope_ids)

    def mark_current_pointing(self, status, telescope_id=0):
        """Mark the current pointing as completed, aborted etc."""
        current_id = self.current_ids[telescope_id]
        self.log.info('marking pointing {} as {}'.format(current_id, status))

        if status == 'running':
            mark_running(current_id)

        elif status == 'completed':
            mark_completed(current_id)
            self.completed_pointings[telescope_id].append(current_id)
            self.completed_times[telescope_id].append(self.now)
            # Schedule the next pointing, since we don't have a caretaker
            reschedule_pointing(current_id, self.now)

        elif status == 'aborted':
            mark_aborted(current_id)
            self.aborted_pointings[telescope_id].append(current_id)
            self.current_ids[telescope_id] = None
            # Schedule the next pointing, since we don't have a caretaker
            reschedule_pointing(current_id, self.now)

        elif status == 'interrupted':
            mark_interrupted(current_id)
            self.interrupted_pointings[telescope_id].append(current_id)
            self.current_ids[telescope_id] = None
            # Schedule the next pointing, since we don't have a caretaker
            reschedule_pointing(current_id, self.now)

    def pause_observing(self, telescope_id):
        """Pause the system."""
        self.log.info('Pausing due to bad weather')
        self.dome_open[telescope_id] = False
        self.pilot_status[telescope_id] = 'Dome Closed'
        if self.current_ids[telescope_id] is not None:
            self.mark_current_pointing('aborted', telescope_id)

    def resume_observing(self, telescope_id):
        """Unpause the system."""
        self.dome_open[telescope_id] = True
        self.pilot_status[telescope_id] = 'Dome Open'

    def check_completion(self, telescope_id):
        """Check if the current pointing has finished.

        In the real pilot this happens when all the exposures are complete,
        but since we're not doing that we fake it.
        """
        if self.current_ids[telescope_id] is not None:
            elapsed = (self.now - self.current_start_times[telescope_id]).to(u.s)
            if elapsed > self.current_durations[telescope_id]:
                # Finished
                self.mark_current_pointing('completed', telescope_id)

    def check_conditions(self, telescope_id):
        """Check if the weather is bad and pause if so."""
        # Check if the weather is bad
        if simparams.ENABLE_WEATHER:
            weather_bad = self.weather[telescope_id].is_bad(self.now)
        else:
            weather_bad = False

        # Check if it is night time
        dark_bad = not self.observers[telescope_id].is_night(self.now, horizon=-10 * u.deg)

        # Get overall conditions
        conditions_bad = weather_bad or dark_bad

        # Decide to close or open
        if conditions_bad and self.dome_open[telescope_id]:
            self.pause_observing(telescope_id)
        if not conditions_bad and not self.dome_open[telescope_id]:
            self.resume_observing(telescope_id)

    def check_scheduler(self):
        """Find current highest priority from the scheduler."""
        self.log.info('checking scheduler')
        # This is a hacked for now, WIP
        new_pointings = scheduler.check_queue(self.now,
                                              # self.sites,
                                              write_file=simparams.WRITE_QUEUE,
                                              write_html=simparams.WRITE_HTML,
                                              log=self.log)
        new_pointings = [new_pointings]  #

        for telescope_id in self.telescope_ids:
            new_pointing = new_pointings[telescope_id]
            if new_pointing is not None:
                new_id = new_pointing.db_id
                new_mintime = new_pointing.mintime
            else:
                new_id = None
                new_mintime = None

            current_id = self.current_ids[telescope_id]
            if new_id != current_id:
                self.log.info('{}: scheduler returns {} (NEW)'.format(telescope_id, new_id))
            else:
                self.log.info('{}: scheduler returns {}'.format(telescope_id, current_id))

            self.new_ids[telescope_id] = new_id
            self.new_mintimes[telescope_id] = new_mintime

    def log_state(self):
        """Write the current state of the pilot to a log file."""
        states = ['unknown'] * len(self.telescope_ids)
        for telescope_id in self.telescope_ids:
            if not self.dome_open[telescope_id]:
                state = 'closed'
            elif self.current_ids[telescope_id] is not None:
                obs_num = len(self.completed_pointings[telescope_id]) + 1
                current_id = self.current_ids[telescope_id]
                state = 'obs,{},{}'.format(obs_num, current_id)
            else:
                state = 'idle'
            states[telescope_id] = state

        fname = os.path.join(params.FILE_PATH, 'fake_pilot_log')
        with open(fname, 'a') as f:
            line = '{};'.format(self.now.iso)
            for telescope_id in self.telescope_ids:
                line += '{}:{}'.format(telescope_id, states[telescope_id])
            line += '\n'
            f.write(line)

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
            for telescope_id in self.telescope_ids:
                self.check_completion(telescope_id)

            # Check the conditions
            for telescope_id in self.telescope_ids:
                self.check_conditions(telescope_id)

            # If the domes are closed we can skip to the next loop
            if any([self.dome_open[telescope_id] for telescope_id in self.telescope_ids]):

                # Find current highest priority from the scheduler
                self.check_scheduler()  # TODO

                # Decide what to do
                for telescope_id in self.telescope_ids:
                    current_id = self.current_ids[telescope_id]
                    new_id = self.new_ids[telescope_id]
                    new_mintime = self.new_mintimes[telescope_id]
                    if new_id == current_id:
                        if current_id is not None:
                            elapsed = (self.now - self.current_start_times[telescope_id]).to(u.s)
                            self.log.info('{}: still observing {} ({:.0f}/{:.0f})'.format(
                                telescope_id, current_id, elapsed.value,
                                self.current_mintimes[telescope_id]))
                        else:
                            self.log.info('{}: nothing to observe!'.format(telescope_id))

                    elif new_id is not None:
                        if current_id is not None:
                            self.log.info('{}: got new pointing from scheduler {}'.format(
                                telescope_id, new_id))
                            if current_id not in self.completed_pointings[telescope_id]:
                                # The pointing didn't finish, mark as interrupted
                                self.mark_current_pointing('interrupted', telescope_id)
                        else:
                            self.log.info('{}: unparking'.format(telescope_id))

                        self.log.info('{}: starting pointing {}'.format(telescope_id, new_id))
                        self.current_ids[telescope_id] = new_id
                        self.current_mintimes[telescope_id] = new_mintime
                        duration = estimate_completion_time(new_id, current_id)
                        self.current_durations[telescope_id] = duration
                        self.current_start_times[telescope_id] = self.now
                        self.mark_current_pointing('running', telescope_id)
                    else:
                        self.log.info('{}: parking'.format(telescope_id))
                        self.current_ids[telescope_id] = None
            else:
                for telescope_id in self.telescope_ids:
                    self.log.info('{}: dome closed'.format(telescope_id))

            # Log the pilot state
            self.log_state()

            # Increase simulation time
            self.now += TimeDelta(simparams.TIMESTEP)

            # Sleep, if asked
            if simparams.SLEEP_TIME:
                sleep(float(simparams.SLEEP_TIME))

        self.log.info('observing completed!')

        # If we were running need to abort
        for telescope_id in self.telescope_ids:
            if self.current_ids[telescope_id] is not None:
                self.mark_current_pointing('aborted', telescope_id)

        # Final log entry
        self.log_state()
