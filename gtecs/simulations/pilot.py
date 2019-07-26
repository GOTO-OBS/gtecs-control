#!/usr/bin/env python
"""A fake pilot to test the scheduler."""

import os

from astroplan import Observer

from astropy import units as u
from astropy.coordinates import EarthLocation

from obsdb import mark_aborted, mark_completed, mark_interrupted, mark_running

from .database import reschedule_pointing
from .hardware import estimate_completion_time
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

    sites : `astropy.coordinates.EarthLocation` or list of same, optional
        The site(s) of the telescope(s) to observe from.
        Default uses one site, `gtecs.astronomy.observatory_location()` (defaults to La Palma).

    telescopes : int or list of same, optional
        The number of telescopes to simulate across the given sites.
        Must either be an integer or a list of integers the same length as `sites`.
        e.g. len(sites)=1 & telescopes=1 will simulate 1 telescope at the given site.
             len(sites)=1 & telescopes=2 will simulate 2 telescopes at the given site.
             len(sites)=2 & telescopes=2 will simulate 4 telescopes, 2 at each site.
             len(sites)=2 & telescopes=[2,1] will simulate 3 telescopes, 2 at site A & 1 at site B.
             len(sites)=2 & telescopes=[1,2] will simulate 3 telescopes, 1 at site A & 2 at site B.
             len(sites)=1 & telescopes=[2,1] will raise a ValueError.
        Default is 1.

    timestep : float, optional
        Time to add on between simulation steps.
        This is ignored if `quick=True` (see below)
        Default is 60 seconds.

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

    weather : bool, optional
        If True, simulate weather conditions by occasionally closing the domes during the day.
        Default is False.

    """

    def __init__(self, start_time, stop_time=None, sites=None, telescopes=1,
                 timestep=60, quick=None, target_pointings=None, weather=False, log=None):
        # Make a logger for the pilot if none is given
        if not log:
            self.log = logger.get_logger('fake_pilot',
                                         log_stdout=False,
                                         log_to_file=True,
                                         log_to_stdout=True)
        else:
            self.log = log
        self.log.info('Pilot started')

        # Set start time
        self.start_time = start_time

        # Set stop time, if not given then 24 hours after the start time
        if stop_time is not None:
            self.stop_time = stop_time
        else:
            self.stop_time = self.start_time + 24 * u.hour

        # Set sites, just `observatory_location` if not given
        if sites is None:
            sites = observatory_location()
        if isinstance(sites, EarthLocation):
            self.sites = [sites]
        else:
            self.sites = sites
        self.site_ids = list(range(len(self.sites)))

        # Create Observer and Weather for each site
        self.observers = {site_id: Observer(self.sites[site_id])
                          for site_id in self.site_ids}
        self.weather = {site_id: Weather(self.start_time, self.stop_time)
                        for site_id in self.site_ids}
        self.enable_weather = weather

        # Set initial dome state to closed for all sites
        self.domes_open = {site_id: False for site_id in self.site_ids}

        # Set number of telescopes
        if isinstance(telescopes, int):
            telescopes = [telescopes] * len(self.sites)
        if len(telescopes) != len(self.sites):
            raise ValueError('List of telescopes must be same length as list of sites.')
        self.telescopes_per_site = {site_id: telescopes[site_id] for site_id in self.site_ids}
        self.telescopes = sum([self.telescopes_per_site[site_id] for site_id in self.site_ids])
        self.telescope_ids = list(range(self.telescopes))
        self.telescopes_at_site = {}
        for site_id in self.site_ids:
            if site_id == 0:
                at_site = self.telescopes_per_site[site_id]
                self.telescopes_at_site[site_id] = list(range(0, at_site))
            else:
                at_prev = self.telescopes_per_site[site_id - 1]
                at_site = self.telescopes_per_site[site_id]
                self.telescopes_at_site[site_id] = list(range(at_prev, at_prev + at_site))
        self.sites_hosting_telescope = {}
        for site_id in self.site_ids:
            for telesope_id in self.telescopes_at_site[site_id]:
                self.sites_hosting_telescope[telesope_id] = site_id

        # Set timestep
        self.timestep = timestep

        # Set quick mode, enforced if simulating more than one telescope
        if self.telescopes > 1 and quick is None:
            quick = True
        elif self.telescopes > 1 and quick is False:
            raise ValueError('For telescopes > 1 quick must be True')
        self.quick = quick

        # Set target pointings
        if target_pointings is not None:
            self.target_pointings = target_pointings
        else:
            self.target_pointings = []

        # Create current pointing dicts
        self.current_ids = {telescope_id: None for telescope_id in self.telescope_ids}
        self.current_mintimes = {telescope_id: None for telescope_id in self.telescope_ids}
        self.current_start_times = {telescope_id: None for telescope_id in self.telescope_ids}
        self.current_durations = {telescope_id: None for telescope_id in self.telescope_ids}

        # Create new pointing dicts
        self.new_ids = {telescope_id: None for telescope_id in self.telescope_ids}
        self.new_mintimes = {telescope_id: None for telescope_id in self.telescope_ids}

        # Create finished pointing dicts
        self.completed_pointings = {telescope_id: [] for telescope_id in self.telescope_ids}
        self.completed_times = {telescope_id: [] for telescope_id in self.telescope_ids}
        self.interrupted_pointings = {telescope_id: [] for telescope_id in self.telescope_ids}
        self.aborted_pointings = {telescope_id: [] for telescope_id in self.telescope_ids}

        # Create all finished pointing dicts
        self.all_completed_pointings = []
        self.all_completed_times = []
        self.all_completed_telescopes = []
        self.all_interrupted_pointings = []
        self.all_aborted_pointings = []

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
            self.all_completed_telescopes.append(telescope_id)
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

    def check_completion(self):
        """Check if the current pointing for each telescope has finished.

        In the real pilot this happens when all the exposures are complete,
        but since we're not doing that we fake it.
        """
        for telescope_id in self.telescope_ids:
            if self.current_ids[telescope_id] is not None:
                elapsed = (self.now - self.current_start_times[telescope_id]).to(u.s)
                if elapsed > self.current_durations[telescope_id]:
                    # Finished
                    self.mark_current_pointing('completed', telescope_id)

    def check_conditions(self):
        """Check if the weather is bad at each site and close the dome(s) there if so."""
        for site_id in self.site_ids:
            # Check if the weather is bad
            if self.enable_weather:
                weather_bad = self.weather[site_id].is_bad(self.now)
            else:
                weather_bad = False

            # Check if it is night time
            dark_bad = not self.observers[site_id].is_night(self.now, horizon=-10 * u.deg)

            # Get overall conditions
            conditions_bad = weather_bad or dark_bad

            # Do we need to close?
            if conditions_bad and self.domes_open[site_id]:
                # Set the domes to closed
                self.domes_open[site_id] = False
                for telescope_id in self.telescopes_at_site[site_id]:
                    self.log.info('{}: closing the dome'.format(telescope_id))
                    # Abort any current pointings
                    if self.current_ids[telescope_id] is not None:
                        self.mark_current_pointing('aborted', telescope_id)

            # Can we reopen?
            if not conditions_bad and not self.domes_open[site_id]:
                # Set the domes to open
                self.domes_open[site_id] = True
                for telescope_id in self.telescopes_at_site[site_id]:
                    self.log.info('{}: opening the dome'.format(telescope_id))

    def check_scheduler(self):
        """Find highest priority pointing for each telescope from the scheduler."""
        self.log.info('checking scheduler')
        # This is based on scheduler.check_queue()
        # Currently the scheduler won't like if multiple pointings are marked as 'running'.
        # Which will happen if the pilot is simulating more than one telescope.
        # However, if in quick mode pointings will always be marked as completed before this
        # function is run, so the scheduler will never know. Smart!

        # Get the highest pointings for each site.
        # We get the highest for the TOTAL number of telescopes (self.telescopes),
        # not the number at each site (self.telescopes_per_site[site_id]).
        # This is to facilitate observing from multiple sites at the same time, which we can't do
        # yet but might want to in the future.
        new_pointings = {site_id: [None] * self.telescopes for site_id in self.site_ids}
        for site_id in self.site_ids:
            # If the domes are closed at this site then don't bother
            if not self.domes_open[site_id]:
                new_pointings[site_id] = [None] * self.telescopes
                continue

            # Import the queue from the database
            queue = scheduler.PointingQueue.from_database(self.now, self.observers[site_id])

            # Don't bother getting the current pointing, as it will always be None.
            # Get the X highest priority pointings, where X is the TOTAL number of telescopes.
            highest_pointings = queue.get_highest_priority_pointings(self.now,
                                                                     self.observers[site_id],
                                                                     number=self.telescopes)

            # Working out what to do next is also simple, as the current pointing is always None.
            new_pointings[site_id] = [scheduler.what_to_do_next(None, highest_pointing,
                                                                log=self.log)
                                      for highest_pointing in highest_pointings]

        # Now the tricky bit.
        # If multiple sites are observing at once it's possible, even likely, they might return
        # the same highest pointings.
        # e.g. imagine the highest priority pointing is a Swift burst localised to only one tile.
        # For these simulations we'll only really ever be considering La Palma and Australia, who
        # shouldn't be observing at the same time anyway.
        # TODO: Use altitude/airmass as a tie-breaker.
        all_highest_ids = [pointing.db_id
                           for site_id in self.site_ids
                           for pointing in new_pointings[site_id]
                           if pointing is not None]
        if len(all_highest_ids) != len(set(all_highest_ids)):
            # Uh oh, there are duplicates
            print(new_pointings)
            print(all_highest_ids)
            raise NotImplementedError('Multiple sites want to observe the same tile!')

        # Assign pointings to telescopes
        for site_id in self.site_ids:
            for i, telescope_id in enumerate(self.telescopes_at_site[site_id]):
                # Currently we always assign the highest priority pointing to telescope 0,
                # the second highest (if there is one) to telescope 1, and so on.
                # TODO: Assign them based on the distance from the telescope's current position.
                new_pointing = new_pointings[site_id][i]

                # Get the pointing IDs and mintimes
                if new_pointing is not None:
                    new_id = new_pointing.db_id
                    new_mintime = new_pointing.mintime
                else:
                    new_id = None
                    new_mintime = None

                # Log
                if new_id != self.current_ids[telescope_id]:
                    self.log.info('{}: scheduler returns {} (NEW)'.format(telescope_id, new_id))
                else:
                    self.log.info('{}: scheduler returns {}'.format(telescope_id, new_id))

                # Store in the dicts
                self.new_ids[telescope_id] = new_id
                self.new_mintimes[telescope_id] = new_mintime

    def log_state(self):
        """Write the current state of the pilot to a log file."""
        states = ['unknown'] * len(self.telescope_ids)
        for site_id in self.site_ids:
            for telescope_id in self.telescopes_at_site[site_id]:
                if not self.domes_open[site_id]:
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

            # Check if the current pointings are complete
            self.check_completion()

            # Check the conditions at each site
            self.check_conditions()

            # Get new pointings from the scheduler
            self.check_scheduler()

            # Decide what to do for each telescope
            for site_id in self.site_ids:
                for telescope_id in self.telescopes_at_site[site_id]:
                    if not self.domes_open[site_id]:
                        # The domes are closed
                        self.log.info('{}: dome closed'.format(telescope_id))

                    else:
                        # Compare the current ID with the one returned from the scheduler
                        # This logic is lifted wholesale form the real pilot
                        current_id = self.current_ids[telescope_id]
                        new_id = self.new_ids[telescope_id]

                        if new_id == current_id:
                            if current_id is not None:
                                elapsed = self.now - self.current_start_times[telescope_id]
                                self.log.info('{}: still observing {} ({:.0f}/{:.0f})'.format(
                                    telescope_id, current_id, elapsed.to(u.s).value,
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
                            new_mintime = self.new_mintimes[telescope_id]
                            self.current_ids[telescope_id] = new_id
                            self.current_mintimes[telescope_id] = new_mintime
                            duration = estimate_completion_time(new_id, current_id)
                            self.current_durations[telescope_id] = duration
                            self.current_start_times[telescope_id] = self.now
                            self.mark_current_pointing('running', telescope_id)
                        else:
                            self.log.info('{}: parking'.format(telescope_id))
                            self.current_ids[telescope_id] = None

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
                self.now += self.timestep * u.s

        self.log.info('observing completed!')

        # If we were running need to abort
        for telescope_id in self.telescope_ids:
            if self.current_ids[telescope_id] is not None:
                self.mark_current_pointing('aborted', telescope_id)

        # Final log entry
        self.log_state()
