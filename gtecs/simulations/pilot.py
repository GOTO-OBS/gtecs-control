#!/usr/bin/env python
"""A fake pilot to test the scheduler."""

import os

from astroplan import Observer

from astropy import units as u

from obsdb import mark_aborted, mark_completed, mark_interrupted, mark_running

from . import params as simparams
from .database import reschedule_pointing
from .misc import estimate_completion_time
from .weather import Weather
from .. import logger
from .. import params
from .. import scheduler
from ..astronomy import observatory_location


class FakePilot(object):
    """A fake, simplified pilot.

    The fake pilot simply checks to see if a more important pointing
    is available from the scheduler, or if it has finished the pointing
    it is supposed to be currently doing.

    Parameters
    ----------
    start_time : `astropy.time.Time`
        Time to start running the simulation at.

    stop_time : `astropy.time.Time`, optional
        Time to stop the simulation.
        Default is 24 hours after the start_time.

    site : `astropy.coordinates.EarthLocation`, optional
        The site of the telescope(s) to observe from.
        Default uses `gtecs.astronomy.observatory_location()` (which defaults to La Palma).

    telescopes : int, optional
        The number of telescopes to simulate at the given site.
        Default is 1.

    quick : bool, optional
        If True, run the pilot in quick mode.
        This means when observing the pilot will skip forward exactly the right amount of time
        to finish each observation.
        For tecnical reasons, if `telescopes` is greater than 1 the pilot currently has to
        run in quick mode.
        Default is False if `telescopes` is 1, True otherwise.

    target_pointings : list of str, optional
        If given a list of pointing IDs, the pilot will abort the simulation once any of those
        pointings have been observed.

    """

    def __init__(self, start_time, stop_time=None, site=None, telescopes=1, quick=None,
                 target_pointings=None, log=None):
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
        if stop_time is not None:
            self.stop_time = stop_time
        else:
            self.stop_time = self.start_time + 24 * u.hours

        if site is not None:
            self.site = site
        else:
            self.site = observatory_location()
        self.observer = Observer(self.site)

        self.telescopes = telescopes
        self.telescope_ids = range(self.telescopes)

        if self.telescopes > 1 and quick is None:
            quick = True
        elif self.telescopes > 1 and quick is False:
            raise ValueError('For telescopes > 1 quick must be True')
        self.quick = quick

        if target_pointings is not None:
            self.target_pointings = target_pointings
        else:
            self.target_pointings = []

        self.weather = Weather(self.start_time, self.stop_time)

        self.current_ids = {telescope_id: None for telescope_id in self.telescope_ids}
        self.current_mintimes = {telescope_id: None for telescope_id in self.telescope_ids}
        self.current_start_times = {telescope_id: None for telescope_id in self.telescope_ids}
        self.current_durations = {telescope_id: None for telescope_id in self.telescope_ids}

        self.new_ids = {telescope_id: None for telescope_id in self.telescope_ids}
        self.new_mintimes = {telescope_id: None for telescope_id in self.telescope_ids}

        self.completed_pointings = {telescope_id: [] for telescope_id in self.telescope_ids}
        self.completed_times = {telescope_id: [] for telescope_id in self.telescope_ids}
        self.interrupted_pointings = {telescope_id: [] for telescope_id in self.telescope_ids}
        self.aborted_pointings = {telescope_id: [] for telescope_id in self.telescope_ids}

        self.all_completed_pointings = []
        self.all_completed_times = []
        self.all_interrupted_pointings = []
        self.all_aborted_pointings = []

        self.dome_open = {telescope_id: False for telescope_id in self.telescope_ids}

    def mark_current_pointing(self, status, telescope_id):
        """Mark the current pointing as completed, aborted etc."""
        current_id = self.current_ids[telescope_id]
        self.log.info('{}: marking pointing {} as {}'.format(telescope_id, current_id, status))

        if status == 'running':
            mark_running(current_id)

        elif status == 'completed':
            mark_completed(current_id)
            self.completed_pointings[telescope_id].append(current_id)
            self.completed_times[telescope_id].append(self.now)
            self.all_completed_pointings.append(current_id)
            self.all_completed_times.append(self.now)
            # Schedule the next pointing, since we don't have a caretaker
            reschedule_pointing(current_id, self.now)

        elif status == 'aborted':
            mark_aborted(current_id)
            self.aborted_pointings[telescope_id].append(current_id)
            self.all_aborted_pointings.append(current_id)
            self.current_ids[telescope_id] = None
            # Schedule the next pointing, since we don't have a caretaker
            reschedule_pointing(current_id, self.now)

        elif status == 'interrupted':
            mark_interrupted(current_id)
            self.interrupted_pointings[telescope_id].append(current_id)
            self.all_interrupted_pointings.append(current_id)
            self.current_ids[telescope_id] = None
            # Schedule the next pointing, since we don't have a caretaker
            reschedule_pointing(current_id, self.now)

    def pause_observing(self, telescope_id):
        """Pause the system."""
        self.log.info('{}: closing the dome'.format(telescope_id))
        self.dome_open[telescope_id] = False
        if self.current_ids[telescope_id] is not None:
            self.mark_current_pointing('aborted', telescope_id)

    def resume_observing(self, telescope_id):
        """Unpause the system."""
        self.log.info('{}: opening the dome'.format(telescope_id))
        self.dome_open[telescope_id] = True

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
        dark_bad = not self.observer.is_night(self.now, horizon=-10 * u.deg)

        # Get overall conditions
        conditions_bad = weather_bad or dark_bad

        # Decide to close or open
        if conditions_bad and self.dome_open[telescope_id]:
            self.pause_observing(telescope_id)
        if not conditions_bad and not self.dome_open[telescope_id]:
            self.resume_observing(telescope_id)

    def check_scheduler(self):
        """Find highest priority pointing for each telescope from the scheduler."""
        self.log.info('checking scheduler')
        # This is based on scheduler.check_queue()
        # Currently the scheduler won't like if multiple pointings are marked as 'running'.
        # Which will happen if the pilot is simulating more than one telescope.
        # However, if in quick mode pointings will always be marked as completed before this
        # function is run, so the scheduler will never know. Smart!

        # Import the queue from the database
        queue = scheduler.PointingQueue.from_database(self.now, self.observer)
        if len(queue) == 0:
            return None

        # Don't bother getting the current pointing, as it will always be None.
        # Get the X highest priority pointings, where X is the number of telescopes at the site.
        highest_pointings = queue.get_highest_priority_pointings(self.now, self.observer,
                                                                 number=self.telescopes)

        # Working out what to do next is also simple, becuase the current pointing is always None.
        new_pointings = [scheduler.what_to_do_next(None, highest_pointing, log=self.log)
                         for highest_pointing in highest_pointings]

        # We now always assign the highest priority pointing to telescope 0, the second highest
        # to telescope 1 and so on.
        # TODO: What if we assigned them based on the distance from their current positions?
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
                line += '{}:{};'.format(telescope_id, states[telescope_id])
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

            # Check if we've observed any of the target pointings
            if any(target_id in self.all_completed_pointings
                   for target_id in self.target_pointings):
                # OK, we're done
                break

            # Increase simulation time
            if self.quick:
                if any([self.current_ids[telescope_id] is not None
                        for telescope_id in self.telescope_ids]):
                    # Skip the current duration, so we're quicker to run through
                    # (plus 10 seconds to be sure)
                    longest_duration = max([self.current_durations[telescope_id]
                                            for telescope_id in self.telescope_ids
                                            if self.current_durations[telescope_id] is not None
                                            ])
                    self.now += longest_duration + 10 * u.s
                else:
                    # Skip forward 5 minutes
                    self.now += 5 * 60 * u.s
            else:
                self.now += simparams.TIMESTEP

        self.log.info('observing completed!')

        # If we were running need to abort
        for telescope_id in self.telescope_ids:
            if self.current_ids[telescope_id] is not None:
                self.mark_current_pointing('aborted', telescope_id)

        # Final log entry
        self.log_state()
