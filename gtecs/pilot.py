#!/usr/bin/env python
"""Master control program for the observatory."""

import asyncio
import functools
import os
import sys
import time

from astropy import units as u
from astropy.time import Time

from obsdb import mark_aborted, mark_completed, mark_interrupted, mark_running

import pkg_resources

from . import logger
from . import monitors
from . import params
from .astronomy import get_sunalt, local_midnight, night_startdate, sunalt_time
from .asyncio_protocols import SimpleProtocol
from .errors import RecoveryError
from .flags import Conditions, Status
from .misc import execute_command, send_email
from .observing import (cameras_are_cool, check_schedule, filters_are_homed,
                        get_pointing_status)
from .slack import send_slack_msg


SCRIPT_PATH = pkg_resources.resource_filename('gtecs', 'observing_scripts')


class Pilot(object):
    """Run the scheduler and telescope.

    The Pilot uses asyncio to run several tasks concurrently,
    including checking the Scheduler for the best job to
    execute at the moment and then starting an observing job.

    Other tasks include, but are not limited to, logging the status,
    checking for midday (when the pilot shuts down), and checking
    to see if robotic mode is disabled, in which case the Pilot
    should do nothing.

    The most important jobs the pilot has check the observing conditions
    and the emergency shutdown flags. These will close the dome and park
    the mount if necessary.

    The idea behind this version of the Pilot is that it is
    pretty dumb, and simply hands off complicated jobs to
    external scripts, which is runs as a subprocess.
    """

    def __init__(self, testing=False):
        # get a logger for the pilot
        self.log = logger.get_logger('pilot',
                                     log_stdout=True,
                                     log_to_file=params.FILE_LOGGING,
                                     log_to_stdout=params.STDOUT_LOGGING)
        self.log.info('Pilot started')
        send_slack_msg('{} pilot started'.format(params.TELESCOP))

        # current and next job from scheduler
        self.current_id = None
        self.current_mintime = None
        self.current_priority = None
        self.current_start_time = None
        self.new_id = None
        self.new_mintime = None
        self.new_priority = None

        # store the name of the running script (if any)
        self.running_script = None
        # for communicating with the external process
        self.running_script_transport = None
        self.running_script_protocol = None
        # future to store result of running script when it's done
        self.running_script_result = None

        # a list of all running tasks to cancel at end of night
        # also used to pause and resume operations?
        self.running_tasks = []

        # lists of routine jobs. Each job a dict of name, protocol, cmd and sunalt
        self.daytime_jobs = []  # before dome opens
        self.evening_jobs = []  # after dome opens
        self.morning_jobs = []  # after observing
        self.startup_complete = False
        self.night_operations = False
        self.jobs_pending = False
        self.observing = False
        self.mount_is_tracking = False   # should the mount be tracking?
        self.dome_is_open = False        # should the dome be open?

        # hardware to keep track of and fix if necessary
        self.hardware = {'dome': monitors.DomeMonitor(self.log),
                         'mnt': monitors.MntMonitor(self.log),
                         'power': monitors.PowerMonitor(self.log),
                         'cam': monitors.CamMonitor(self.log),
                         'filt': monitors.FiltMonitor(self.log),
                         'foc': monitors.FocMonitor(self.log),
                         'exq': monitors.ExqMonitor(self.log),
                         'conditions': monitors.ConditionsMonitor(self.log),
                         'scheduler': monitors.SchedulerMonitor(self.log),
                         'sentinel': monitors.SentinelMonitor(self.log),
                         }
        self.current_errors = {k: set() for k in self.hardware.keys()}

        # override and conditions flags
        self.status = Status()
        self.conditions = Conditions()

        # dictionary of reasons to pause
        self.whypause = {'hw': False, 'cond': False, 'manual': False}
        self.time_paused = 0

        # dome check flags
        self.dome_confirmed_closed = False
        self.close_command_time = 0.

        self.initial_hardware_check_complete = False

        self.force_scheduler_check = False
        self.scheduler_check_time = 0
        self.initial_scheduler_check_complete = False

        # flag for daytime testing
        self.testing = testing

        # flag to shutdown early (in emergencies)
        self.shutdown_now = 0

    # Check routines
    async def check_scheduler(self):
        """Check scheduler and update current job every 10 seconds."""
        self.log.info('scheduler check routine initialised')

        sleep_time = 10
        while True:
            now = time.time()
            if self.force_scheduler_check or (now - self.scheduler_check_time) > sleep_time:
                if not self.observing:
                    self.log.debug('scheduler checks suspended when not observing')
                elif self.paused:
                    self.log.debug('scheduler checks suspended while paused')
                else:
                    # check scheduler daemon
                    self.log.debug('checking scheduler')

                    check_results = check_schedule()
                    self.new_id, self.new_priority, self.new_mintime = check_results
                    # NOTE we don't actually use the priority anywhere in the pilot!

                    if self.new_id != self.current_id:
                        self.log.info('scheduler check: NEW JOB {}'.format(self.new_id))
                    else:
                        self.log.debug('scheduler check: continue {}'.format(self.current_id))

                    self.initial_scheduler_check_complete = True

                self.scheduler_check_time = now
                self.force_scheduler_check = False
            await asyncio.sleep(1)

    async def check_hardware(self):
        """Continuously monitor hardware and try to fix any issues."""
        self.log.info('hardware check routine initialised')
        good_sleep_time = 30
        bad_sleep_time = 10
        bad_timestamp = 0

        sleep_time = good_sleep_time
        while True:
            if self.status.mode == 'manual':
                self.log.debug('hardware checks suspended in manual mode')
                await asyncio.sleep(sleep_time)
                continue

            if not self.startup_complete:
                self.log.debug('hardware checks suspended until after startup')
                await asyncio.sleep(sleep_time)
                continue

            error_count = 0
            self.log.debug('checking hardware')
            for monitor in self.hardware.values():
                num_errs, errors = monitor.check()
                for error in [e for e in errors if e not in self.current_errors[monitor.daemon_id]]:
                    self.current_errors[monitor.daemon_id].add(error)
                    msg = 'New error from {}: {}'.format(monitor.monitor_id, error)
                    self.log.warning(msg)
                    send_slack_msg(msg)
                for error in [e for e in self.current_errors[monitor.daemon_id] if e not in errors]:
                    self.current_errors[monitor.daemon_id].remove(error)
                    msg = 'Fixed error from {}: {}'.format(monitor.monitor_id, error)
                    self.log.info(msg)
                    send_slack_msg(msg)
                error_count += num_errs
                if num_errs > 0:
                    try:
                        monitor.recover()  # Will log recovery commands
                    except RecoveryError as err:
                        # Uh oh, we're out of options
                        send_slack_msg(err)
                        asyncio.ensure_future(self.emergency_shutdown('Unfixable hardware error'))

            if error_count > 0:
                await self.handle_pause('hw', True)

                # check more frequently untill fixed, and save time for delay afterwards
                sleep_time = bad_sleep_time
                bad_timestamp = time.time()
            else:
                self.log.debug('hardware status AOK')
                await self.handle_pause('hw', False)

                # only allow the night marshal to open after a
                # successful hardware check
                self.initial_hardware_check_complete = True

                # Revert to 30 checks after a minute of good checks
                if time.time() - bad_timestamp > 60:
                    sleep_time = good_sleep_time
                    bad_timestamp = 0
                else:
                    sleep_time = bad_sleep_time

            await asyncio.sleep(sleep_time)

    async def check_flags(self):
        """Check the conditions and override flags."""
        self.log.info('flags check routine initialised')

        sleep_time = 10
        while True:
            # handle overrides first
            self.status = Status()
            if self.status.mode == 'manual':
                await self.handle_pause('manual', True)
            else:
                await self.handle_pause('manual', False)

            # now handle conditions
            self.conditions = Conditions()
            if self.conditions.bad:
                self.log.warning('Conditions bad: ({})'.format(self.conditions.bad_flags))
                await self.handle_pause('cond', True)
            else:
                await self.handle_pause('cond', False)

            # emergency file
            if self.status.emergency_shutdown:
                reasons = ', '.join(self.status.emergency_shutdown_reasons)
                self.log.warning('Conditions critical: ({})'.format(reasons))
                asyncio.ensure_future(self.emergency_shutdown(reasons))

            # print if we're paused
            if self.paused:
                reasons = [k for k in self.whypause if self.whypause[k]]
                self.log.info('pilot paused ({})'.format(', '.join(reasons)))

            await asyncio.sleep(sleep_time)

    async def check_dome(self):
        """Double check that dome is closed if it should be."""
        self.log.info('dome check routine initialised')

        sleep_time = 10
        while True:
            if self.status.mode == 'manual':
                self.log.debug('dome checks suspended in manual mode')
                await asyncio.sleep(10)
                continue

            if not self.dome_is_open and not self.dome_confirmed_closed:
                dome_status = self.hardware['dome'].get_hardware_status()
                if dome_status in ['closed', 'in_lockdown']:
                    # phew
                    self.dome_confirmed_closed = True
                    self.log.info('dome confirmed closed')
                elif time.time() - self.close_command_time > 65.:
                    self.log.warning('dome not closed. Trying again...')
                    self.close_dome()
            await asyncio.sleep(sleep_time)

    async def check_time_paused(self):
        """Keep track of the time the pilot has been paused."""
        self.log.info('pause check routine initialised')

        sleep_time = 60
        while True:
            if self.paused:
                self.time_paused += sleep_time
            await asyncio.sleep(sleep_time)

    # Night marshal
    async def nightmarshal(self, restart=False, late=False):
        """Start tasks at the right time (based on the sun's altitude).

        Parameters
        ----------
        restart : bool
            If true, we will skip startup jobs and get straight to observing

        late : bool
            If true, we will try to do evening jobs even if it's too late
            (note FLATS will fail anyway)

        """
        self.log.info('night marshal initialised')

        # if paused due to manual mode we should not do anything
        while self.whypause['manual']:
            self.log.info('in manual mode, tasks suspended')
            await asyncio.sleep(30)

        # wait for the right sunalt to start
        if not restart:
            await self.wait_for_sunalt(12, 'STARTUP')

        # Startup: do this always, unless we're restarting
        if not restart:
            if not self.startup_complete:
                await self.startup()
        self.log.info('startup complete')
        self.send_startup_report()
        self.startup_complete = True

        # now startup is complete we can start hardware checks
        while not self.initial_hardware_check_complete:
            self.log.info('waiting for the first successful hardware check')
            await asyncio.sleep(30)

        # make sure filters are homed and cams are cool, in case of restart
        await self.prepare_for_images_async()

        # Daytime jobs: do these even in bad weather
        if not restart:
            await self.run_through_jobs(self.daytime_jobs, rising=False,
                                        ignore_conditions=True,
                                        ignore_late=late)

        # wait for the right sunalt to open dome
        await self.wait_for_sunalt(0, 'OPEN')

        # no point opening if we are paused due to bad weather or hw fault
        while self.paused:
            self.log.info('opening suspended until pause is cleared')
            await asyncio.sleep(30)

        # OK - open the dome and start nightly operations
        self.log.info('starting night operations')
        self.night_operations = True
        await self.open_dome()
        await self.unpark_mount()

        # Evening jobs
        if not restart:
            await self.run_through_jobs(self.evening_jobs, rising=False,
                                        ignore_late=late)

        # Wait for darkness
        await self.wait_for_sunalt(-15, 'OBS')

        # Start observing: will automatically stop at the target sun alt
        if self.testing:
            await self.observe(until_sunalt=90)
        else:
            # await self.observe(until_sunalt=-14.6, last_obs_sunalt=-15)
            await self.observe(until_sunalt=-12, last_obs_sunalt=-14)

        # Morning jobs
        await self.run_through_jobs(self.morning_jobs, rising=True,
                                    ignore_late=False)

        # Wait for morning jobs to finish
        while self.jobs_pending or self.running_script:
            await asyncio.sleep(10)

        # Finished.
        self.log.info('finished night operations')
        self.night_operations = False

    # External scripts
    async def start_script(self, name, protocol, cmd):
        """Launch an external Python script.

        Parameters
        ----------
        name : str
            A name for this process. Prepended to output from process.
        protocol : `pilot_protocols.PilotJobProtocol`
            Protocol used to process output from Process
        cmd : list
            A list of the command to be executed with Python.
            The first element of the list is the Python script to execute,
            any additional elements are the arguments to the script.

        """
        # first cancel any currently running script
        await self.cancel_running_script(why='new script starting')

        # create a future to store result in
        self.running_script_result = asyncio.Future()

        # fill the name, future and log_name arguments of protocol(...)
        # using functools.partial
        factory = functools.partial(protocol, name, self.running_script_result,
                                    'pilot')
        loop = asyncio.get_event_loop()

        # create the process coroutine which will return
        # a 'transport' and 'protocol' when scheduled
        proc = loop.subprocess_exec(factory, sys.executable, '-u', *cmd,
                                    stdin=None)

        # start the process and get transport and protocol for control of it
        self.log.info("starting {}".format(name))
        self.running_script = name
        self.running_script_transport, self.running_script_protocol = await proc

        # process started, await completion
        retcode, result = await self.running_script_result

        # done
        self.log.info("finished {}".format(name))
        self.running_script = None

        # if it was an observation that just finished, force a scheduler check
        if name == 'OBS':
            self.log.debug("forcing scheduler check".format(name))
            self.force_scheduler_check = True

        return retcode, result

    async def cancel_running_script(self, why):
        """Cancel the currently running Python script.

        This does nothing if the script is already done.
        """
        if self.running_script is not None:
            # check job is still running
            if self.running_script_transport.get_returncode() is None:
                self.log.info('killing {}, reason: "{}"'.format(self.running_script, why))

                # check job is still running again, just in case
                try:
                    self.running_script_transport.terminate()
                    await self.running_script_result
                except Exception:
                    self.log.debug('{} already exited?'.format(self.running_script))

                execute_command('exq clear')
                execute_command('cam abort')

                # if we were observing, mark as aborted
                if self.running_script == 'OBS' and self.current_id is not None:
                    mark_aborted(self.current_id)

                self.log.info("killed {}".format(self.running_script))
                self.running_script = None

    # Daily jobs
    def assign_jobs(self):
        """Assign the daily jobs for the pilot."""
        # daytime jobs: done before opening the dome
        darks = {'name': 'DARKS',
                 'sunalt': 8,
                 'script': os.path.join(SCRIPT_PATH, 'takeBiasesAndDarks.py'),
                 'args': [str(params.NUM_DARKS)],
                 'protocol': SimpleProtocol}

        self.daytime_jobs = [darks]

        # evening jobs: done after opening the dome, before observing starts
        flats_e = {'name': 'FLATS',
                   'sunalt': -2,
                   'script': os.path.join(SCRIPT_PATH, 'takeFlats.py'),
                   'args': ['EVE'],
                   'protocol': SimpleProtocol}
        autofoc = {'name': 'FOC',
                   'sunalt': -11,
                   'script': os.path.join(SCRIPT_PATH, 'autoFocus.py'),
                   'args': [],
                   'protocol': SimpleProtocol}

        self.evening_jobs = [flats_e, autofoc]

        # morning jobs: done after observing, before closing the dome
        # foc_run = {'name': 'FOCRUN',
        #           'sunalt': -14.5,
        #           'script': os.path.join(SCRIPT_PATH, 'takeFocusRun.py'),
        #           'args': ['1000', '100', 'n'],
        #           'protocol': SimpleProtocol}
        flats_m = {'name': 'FLATS',
                   'sunalt': -10,
                   'script': os.path.join(SCRIPT_PATH, 'takeFlats.py'),
                   'args': ['MORN'],
                   'protocol': SimpleProtocol}

        # self.morning_jobs = [foc_run, flats_m]
        self.morning_jobs = [flats_m]

    async def run_through_jobs(self, job_list, rising=False,
                               ignore_conditions=False,
                               ignore_late=False):
        """Just pop jobs off a list and run them at correct sunalt."""
        while job_list:
            self.jobs_pending = True
            job = job_list.pop(0)
            name = job['name']
            sunalt = job['sunalt']
            cmd = [job['script'], *job['args']]
            protocol = job['protocol']

            self.log.info('next job: {}'.format(name))

            # wait for the right sun altitude
            OK = await self.wait_for_sunalt(sunalt, name, rising, ignore_late)

            if not OK:
                # too late
                self.log.warning('too late to start {}'.format(name))
                continue

            elif ((self.whypause['hw']) or
                  (self.whypause['manual']) or
                  (self.whypause['cond'] and not ignore_conditions)):
                # need to check if we're paused
                # if ignore_conditions (daytime jobs) we can start even if
                # paused for conditions, but not for other reasons
                self.log.warning('currently paused, will not start {}'.format(name))
                await asyncio.sleep(15)
                continue

            elif self.testing or ignore_late:
                # wait for each script to finish
                await self.start_script(name, protocol, cmd)

            else:
                # don't wait for script finish, but start each one when the
                # sun alt says so, cancelling running script if not done
                asyncio.ensure_future(self.start_script(name, protocol, cmd))

            await asyncio.sleep(1)

        self.jobs_pending = False

    async def wait_for_sunalt(self, sunalt, why,
                              rising=False, ignore_late=False):
        """Return when the sun reaches the given altitude.

        Parameters
        ----------
        sunalt : float
            sun altitude in degrees to wait for
        why : str
            a brief reason why we're waiting, helpful for the log
        rising : bool
            whether the sun is rising or setting
        ignore_late : bool, optional
            if true, will ignore checks for if you're too late
            (will still wait if you're too early)

        Returns
        --------
        OK : bool
            True if you are safe to go, False if we think you are too late

        """
        # if the pilot is in testing mode then return immediately
        if self.testing:
            self.log.info('in testing mode, start {} immediately'.format(why))
            return True

        self.log.info('waiting for {}'.format(why))
        now = Time.now()

        # check if we're on the wrong side of midnight
        midnight = local_midnight(night_startdate())
        if not rising and now > midnight and not ignore_late:
            # wow, you're really late
            return False
        elif rising and now < midnight:
            return False

        # check if we've missed the sun (with a 5 degree margin)
        if not ignore_late:
            sunalt_now = get_sunalt(now)
            if not rising and sunalt_now < (sunalt - 5):
                # missed your chance
                return False
            elif rising and sunalt_now > (sunalt + 5):
                return False

        # we're on time, so wait until the sun is in the right position
        sleep_time = 60
        while True:
            now = Time.now()
            sunalt_now = get_sunalt(now)

            # Log to debug if there's a script running, info if not
            msg = 'sunalt={:.1f}, waiting for {:.1f} ({})'.format(sunalt_now, sunalt, why)
            if self.running_script is None:
                self.log.info(msg)
            else:
                self.log.debug(msg)

            # has our watch ended?
            if rising and sunalt_now > sunalt:
                break
            elif not rising and sunalt_now < sunalt:
                break

            await asyncio.sleep(sleep_time)

        self.log.info('reached sun alt target, ready for {}'.format(why))
        return True

    async def observe(self, until_sunalt=-14.6, last_obs_sunalt=-15):
        """Observe until further notice.

        Parameters
        ----------
        until_sunalt : float
            sun altitude at which to stop observing
        last_obs_sunalt : float
            sun altitude at which to schedule last new observation

        """
        self.log.info('observing')
        self.observing = True

        sleep_time = 5
        while True:
            # do nothing if paused
            if self.paused:
                await asyncio.sleep(30)
                continue

            # no point observing if we haven't checked the scheduler yet
            while not self.initial_scheduler_check_complete:
                self.log.info('waiting for first scheduler check')
                self.force_scheduler_check = True
                await asyncio.sleep(2)
                continue

            # should we stop for the sun?
            now = Time.now()
            midnight = local_midnight(night_startdate())
            sunalt_now = get_sunalt(now)
            if now > midnight:
                if sunalt_now > last_obs_sunalt and self.observing:
                    self.log.debug('stopping scheduler checks, current observation will continue')
                    self.observing = False
                if sunalt_now > until_sunalt:
                    # end observing
                    break

            # See if a new target has arrived and mark job appropriately
            # There are 6 options (technically 5, bottom left & bottom right
            # are the same...):
            #               | | new_id is  |    new_id is    |   new_id   |
            #               | |  same as   |  different to   |     is     |
            #               | | current_id |   current_id    |    None    |
            #  -------------+-+------------+-----------------+------------+
            #  -------------+-+------------+-----------------+------------+
            #    current_id | |  carry on  | stop current_id |    park    |
            #   is not None | | current_id | & start new_id  |            |
            #  -------------+-+------------+-----------------+------------+
            #    current_id | |    stay    |      start      |    stay    |
            #      is None  | |   parked   |      new_id     |   parked   |

            if self.new_id == self.current_id:
                if self.current_id is not None:
                    now = time.time()
                    elapsed = now - self.current_start_time
                    self.log.debug('still observing {} ({:.0f}/{:.0f})'.format(
                                   self.current_id, elapsed, self.current_mintime))
                else:
                    self.log.warning('nothing to observe!')
                    if not self.testing:
                        send_slack_msg('{} pilot has nothing to observe!'.format(params.TELESCOP))

            elif self.new_id is not None:
                if self.current_id is not None:
                    self.log.info('got new job from scheduler {}'.format(self.new_id))

                    # Get current job status
                    current_status = get_pointing_status(self.current_id)
                    self.log.debug('current job status = {}'.format(current_status))

                    # Check if we're interupting a still ongoing job and need
                    # to mark it as interupted. The alternative is that the
                    # OBS script has finished which means it will have already
                    # been marked as completed.
                    if (self.running_script == 'OBS' and
                            self.running_script_transport.get_returncode() is None and
                            current_status != 'completed'):

                        # cancel the script first (will mark as aborted)
                        await self.cancel_running_script(why='new job')

                        # now correctly mark it as completed or interupted
                        now = time.time()
                        elapsed = now - self.current_start_time - self.time_paused

                        self.log.debug('min time = {:.1f}, time elapsed = {:.1f}'.format(
                                       self.current_mintime, elapsed))
                        if elapsed > self.current_mintime:
                            mark_completed(self.current_id)
                            self.log.debug('job completed: {}'.format(self.current_id))
                        else:
                            mark_interrupted(self.current_id)
                            self.log.debug('job interrupted: {}'.format(self.current_id))

                else:
                    self.log.info('got job from scheduler {}'.format(self.new_id))
                    # we weren't doing anything, which implies we were parked
                    await self.unpark_mount()

                # start the new job
                self.log.debug('starting Job {}'.format(self.new_id))

                script = os.path.join(SCRIPT_PATH, 'observe.py')
                args = [str(self.new_id), str(int(self.new_mintime))]
                cmd = [script, *args]
                asyncio.ensure_future(self.start_script('OBS', SimpleProtocol, cmd))

                mark_running(self.new_id)

                self.current_start_time = time.time()
                self.current_id = self.new_id
                self.current_priority = self.new_priority
                self.current_mintime = self.new_mintime
                self.time_paused = 0

            else:
                self.log.warning('nothing to do, parking mount')
                self.park_mount()
                execute_command('exq clear')
                execute_command('cam abort')
                self.current_id = None
                self.current_priority = None
                self.current_mintime = None
                # If we've interrupted a job it needs to be cancelled,
                # this will mark it as aborted
                await self.cancel_running_script('obs parking')

            await asyncio.sleep(sleep_time)

        self.log.info('observing completed!')
        self.observing = False

        # finish observing
        execute_command('exq clear')
        execute_command('cam abort')

        # If we've interrupted a job it needs to be cancelled,
        # this will mark it as aborted
        await self.cancel_running_script('obs finished')
        self.current_id = None
        self.current_priority = None
        self.current_mintime = None

    # Pausing
    @property
    def paused(self):
        """Return True if the pilot is paused for any reason."""
        return True in self.whypause.values()

    async def handle_pause(self, reason, pause):
        """Handle possible changes in pause status.

        This checks all the other flags and the current pause status.
        If appropriate it pauses or unpauses operations

        Parameters
        ----------
        reason : string
            the reason why we might pause or unpause
            one of 'manual', 'cond' or 'hw'
        pause : bool
            does reason suggest a pause (True) or unpause (False)

        """
        if pause and not self.whypause[reason]:
            # we can set this here because we want to pause right away
            self.whypause[reason] = True

            if reason == 'cond':
                msg = 'Pausing due to bad conditions ({})'.format(self.conditions.bad_flags)
                self.log.warning(msg)

                if self.night_operations:
                    # only need to stop scripts if the dome is open
                    # (this way we don't kill darks if the weather goes bad)
                    execute_command('exq pause')
                    execute_command('cam abort')
                    await self.cancel_running_script('conditions bad')

                # always make sure we're closed and parked
                self.close_dome()
                self.park_mount()

            elif reason == 'hw':
                msg = 'Pausing operations due to hardware fault'
                self.log.warning(msg)

                if self.running_script == 'STARTUP':
                    # don't cancel startup due to hardware issue
                    pass
                elif self.running_script == 'OBS':
                    # just pause the queue until fixed
                    execute_command('exq pause')
                    execute_command('cam abort')
                elif self.running_script is not None:
                    # other scripts cannot handle losing frames
                    execute_command('exq clear')
                    execute_command('cam abort')
                    await self.cancel_running_script('hardware fault')

            elif reason == 'manual':
                msg = 'Pausing operations due to manual override'
                self.log.warning(msg)

                # don't actually kill anything, coroutines will pause themselves
                self.log.info('pausing for pilot for manual override')
                self.log.info('current job will continue')

        # does this change suggest a global unpause?
        unpause = (not any([self.whypause[key] for key in self.whypause if key != reason]) and
                   not pause)
        if unpause and self.paused:
            # OK, we can resume
            self.log.warning('resuming operations')
            if self.night_operations:
                if not self.dome_is_open:
                    # open the dome if it's closed
                    # this way wait and don't resume until the dome is open
                    await self.open_dome()
                if not self.mount_is_tracking:
                    # unpark the mount if it's parked
                    # this way we don't unpark if we're still observing,
                    # which can happen if we paused manually
                    await self.unpark_mount()
                execute_command('exq resume')

        # finally, change global pause status by updating flag
        # by putting this last, we dont unpause until the dome
        # is actually open or HW is fixed etc.
        self.whypause[reason] = pause

    # Night countdown
    async def night_countdown(self, stop_time):
        """Return when night is done.

        This function simply keeps running until the stop_time is reached.
        The use of such a function is that it can be added to the list
        of tasks supplied to `~asyncio.BaseEventLoop.run_until_complete`
        and the loop will keep going until the stop_time is reached.

        Parameters
        -----------
        stop_time : `~astropy.time.Time`
            the time to stop the Pilot

        """
        self.log.info('night countdown initialised')

        sleep_time = 60
        while True:
            now = Time.now()
            # check if we have reached stop time
            if now > stop_time:
                self.log.info('stop time reached, night is over')
                await self.shutdown()
                break

            # check if we need to stop early
            if self.shutdown_now:
                self.log.warning('stopping early due to emergency shutdown')
                break

            stop_time.precision = 0
            delta = stop_time - now
            delta_min = delta.to('min').value
            status_str = 'end of night at {} ({:.0f} mins)'.format(stop_time.iso, delta_min)
            if delta_min < 30:
                self.log.info(status_str)
            else:
                self.log.debug(status_str)

            await asyncio.sleep(sleep_time)

        self.log.info('finished for tonight')

    # Startup and shutdown commands
    async def startup(self):
        """Start up the system.

        Runs the startup script, and sets the startup_complete flag
        """
        # start startup script
        self.log.debug('running startup script')
        cmd = [os.path.join(SCRIPT_PATH, 'startup.py')]
        retcode, result = await self.start_script('STARTUP', SimpleProtocol, cmd)
        if retcode != 0:
            self.log.warning('STARTUP ended abnormally')

        self.log.debug('startup script complete')

    async def shutdown(self):
        """Shut down the system.

        Close any running scripts and jobs, run the shutdown script, ensure the
        dome is closed and finish.
        """
        # first shut down all running tasks
        # this is so check_flags doesn't initiate two shutdowns,
        # or we don't end up trying to restart if conditions clear
        # or an "unfixible" hardware error gets fixed
        self.log.warning('cancelling running tasks')
        for task in self.running_tasks:
            task.cancel()

        # then cancel any currently running script
        await self.cancel_running_script(why='shutdown')

        # start shutdown script
        self.log.warning('running shutdown script')
        cmd = [os.path.join(SCRIPT_PATH, 'shutdown.py')]
        retcode, result = await self.start_script('SHUTDOWN', SimpleProtocol, cmd)
        if retcode != 0:
            self.log.warning('SHUTDOWN ended abnormally')

        # next and most important.
        # NEVER STOP WITHOUT CLOSING THE DOME!
        # EMAIL IF DOME WON'T CLOSE
        self.log.warning('making sure dome is closed')
        await self.close_dome_confirm()

        self.log.warning('shutdown process complete')

    async def emergency_shutdown(self, why):
        """Send a warning and then shut down."""
        self.log.warning('performing emergency shutdown: {}'.format(why))
        send_slack_msg('{} pilot is performing an emergency shutdown: {}'.format(
                       params.TELESCOP, why))

        self.log.warning('closing dome immediately')
        self.close_dome()

        self.log.warning('running shutdown')
        await self.shutdown()

        # end the night countdown early to close the pilot
        self.shutdown_now = 1

    # Hardware commands
    async def open_dome(self):
        """Open the dome and await until it is finished."""
        self.log.warning('opening dome')
        send_slack_msg('{} pilot is opening the dome'.format(params.TELESCOP))
        execute_command('dome open')
        self.dome_is_open = True
        self.dome_confirmed_closed = False
        self.hardware['dome'].mode = 'open'
        # wait for dome to open
        sleep_time = 5
        while True:
            dome_status = self.hardware['dome'].get_hardware_status()
            self.log.debug('dome is {}'.format(dome_status))
            if dome_status == 'full_open':
                break
            await asyncio.sleep(sleep_time)
        self.log.info('dome confirmed open')

    def close_dome(self):
        """Send the dome close command and return immediately."""
        self.log.warning('closing dome')
        dome_status = self.hardware['dome'].get_hardware_status()
        if dome_status not in ['closed', 'in_lockdown']:
            send_slack_msg('{} pilot is closing the dome'.format(params.TELESCOP))
        execute_command('dome close')
        self.dome_is_open = False
        self.hardware['dome'].mode = 'closed'
        self.dome_confirmed_closed = False
        self.close_command_time = time.time()

    async def close_dome_confirm(self, mins_until_panic=10):
        """Close the dome, make sure it's closed and alert if it won't.

        Parameters
        ----------
        mins_until_panic : float
            time in minutes to wait before emailing

        """
        start_time = time.time()
        self.close_dome()

        # wait for dome to close
        sleep_time = 5
        while True:
            dome_status = self.hardware['dome'].get_hardware_status()
            self.log.debug('dome is {}'.format(dome_status))
            if dome_status in ['closed', 'in_lockdown']:
                break

            # panic time
            elapsed_time = time.time() - start_time
            if elapsed_time / 60. > mins_until_panic:
                msg = "IMPORTANT: {} pilot cannot close dome!".format(params.TELESCOP)
                send_slack_msg(msg)
                try:
                    send_email(message=msg)
                except Exception:
                    self.log.error('Error sending email')

            await asyncio.sleep(sleep_time)

        self.dome_confirmed_closed = True
        send_slack_msg('{} pilot confirmed dome is closed'.format(params.TELESCOP))
        self.log.info('dome confirmed closed')

    async def unpark_mount(self):
        """Unpark the mount and await until it is finished."""
        self.log.warning('unparking mount')
        execute_command('mnt unpark')
        self.mount_is_tracking = True
        self.hardware['mnt'].mode = 'tracking'
        await asyncio.sleep(5)
        mount_status = self.hardware['mnt'].get_hardware_status()
        if not mount_status == 'tracking':
            # slew to above horizon, to stop errors
            execute_command('mnt slew_altaz 50 0')
            # wait for mount to slew
            sleep_time = 5
            while True:
                mount_status = self.hardware['mnt'].get_hardware_status()
                self.log.debug('mount is {}'.format(mount_status))
                if mount_status == 'tracking':
                    break
                await asyncio.sleep(sleep_time)
        self.log.info('mount confirmed tracking')

    def park_mount(self):
        """Send the mount park command and return immediately."""
        self.log.warning('parking mount')
        execute_command('mnt park')
        self.mount_is_tracking = False
        self.hardware['mnt'].mode = 'parked'

    async def prepare_for_images_async(self):
        """Prepare for taking images."""
        # Home the filter wheels
        if not filters_are_homed():
            execute_command('filt home')
            while not filters_are_homed():
                await asyncio.sleep(1)
        self.log.info('filters are homed')

        # Bring the CCDs down to temperature
        if not cameras_are_cool():
            execute_command('cam temp {}'.format(params.CCD_TEMP))
            while not cameras_are_cool():
                await asyncio.sleep(1)
        self.log.info('cameras are cool')

    def send_startup_report(self):
        """Format and send a Slack message with a summery of the current conditions."""
        msg = '{} pilot reports startup complete'.format(params.TELESCOP)
        conditions = Conditions()
        conditions_summary = conditions.get_formatted_string(good=':heavy_check_mark:',
                                                             bad=':exclamation:')
        if conditions.bad:
            msg2 = ':warning: Conditions are bad! :warning:'
            colour = 'danger'
        else:
            msg2 = 'Conditions are good'
            colour = 'good'

        attach_conds = {'fallback': 'Conditions summary',
                        'title': msg2,
                        'text': conditions_summary,
                        'color': colour,
                        'ts': conditions.update_time,
                        }

        env_url = 'http://lapalma-observatory.warwick.ac.uk/environment/'
        mf_url = 'https://www.mountain-forecast.com/peaks/Roque-de-los-Muchachos/forecasts/2423'
        ing_url = 'http://catserver.ing.iac.es/weather/index.php?view=site'
        not_url = 'http://www.not.iac.es/weather/'
        tng_url = 'https://tngweb.tng.iac.es/weather/'
        links = ['<{}|Local enviroment page>'.format(env_url),
                 '<{}|Mountain forecast>'.format(mf_url),
                 '<{}|ING>'.format(ing_url),
                 '<{}|NOT>'.format(not_url),
                 '<{}|TNG>'.format(tng_url),
                 ]
        attach_links = {'fallback': 'Useful links',
                        'text': '  -  '.join(links),
                        'color': colour,
                        }

        ts = '{:.0f}'.format(conditions.update_time)
        webcam_url = 'http://lapalma-observatory.warwick.ac.uk/webcam/ext2/static?' + ts
        attach_webcm = {'fallback': 'External webcam view',
                        'title': 'External webcam view',
                        'title_link': 'http://lapalma-observatory.warwick.ac.uk/eastcam/',
                        'text': 'Image attached:',
                        'image_url': webcam_url,
                        'color': colour,
                        }

        sat_url = 'https://en.sat24.com/image?type=infraPolair&region=ce&' + ts
        attach_irsat = {'fallback': 'IR satellite view',
                        'title': 'IR satellite view',
                        'title_link': 'https://en.sat24.com/en/ce/infraPolair',
                        'text': 'Image attached:',
                        'image_url': sat_url,
                        'color': colour,
                        }

        send_slack_msg(msg, [attach_conds, attach_links, attach_webcm, attach_irsat])


def run(test=False, restart=False, late=False):
    """Start the pilot and run until the ned of the night.

    Parameters
    ----------
    test : bool
        run the pilot in test mode
    restart : bool
        skip the evening tasks and go straight to observing
    late : bool
        run the evening tasks even if it's too late

    """
    loop = asyncio.get_event_loop()
    loop.set_debug(False)
    pilot = Pilot(testing=test)
    pilot.assign_jobs()

    # start the recurrent tasks
    pilot.running_tasks.extend([
        asyncio.ensure_future(pilot.check_hardware()),  # periodically check hardware
        asyncio.ensure_future(pilot.check_time_paused()),  # keep track of time paused
        asyncio.ensure_future(pilot.check_flags()),  # check flags for bad weather or override
        asyncio.ensure_future(pilot.check_scheduler()),  # start checking the schedule
        asyncio.ensure_future(pilot.check_dome()),  # keep a close eye on dome
        asyncio.ensure_future(pilot.nightmarshal(restart, late)),  # run through scheduled jobs
    ])

    # keep the pilot runing until the end of the night
    if pilot.testing:
        sunrise = Time.now() + 15 * u.minute
    else:
        date = night_startdate()
        sunrise = sunalt_time(date, 0 * u.deg, eve=False)
    stop_signal = pilot.night_countdown(stop_time=sunrise)

    try:
        # actually start the event loop - nothing happens until this line is reached!
        loop.run_until_complete(stop_signal)
    except asyncio.CancelledError:
        print('Tasks cancelled')
    finally:
        print('Pilot done')
        send_slack_msg('{} pilot done'.format(params.TELESCOP))
        loop.close()
