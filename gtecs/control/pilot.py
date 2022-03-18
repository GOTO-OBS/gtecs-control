"""Master control program for the observatory."""

import abc
import asyncio
import functools
import sys
import time
try:
    import importlib.resources as pkg_resources
except ImportError:
    # Python < 3.7
    import importlib_resources as pkg_resources  # type: ignore

from astropy import units as u
from astropy.time import Time

from gtecs.common.logging import get_logger
from gtecs.obs.database import mark_aborted, mark_completed, mark_interrupted, mark_running

from . import monitors
from . import params
from .astronomy import get_sunalt, local_midnight, night_startdate, sunalt_time
from .errors import RecoveryError
from .flags import Conditions, Status
from .misc import execute_command, send_email
from .observing import check_schedule, get_pointing_status
from .slack import send_slack_msg, send_startup_report, send_database_report, send_timing_report


class TaskProtocol(asyncio.SubprocessProtocol, metaclass=abc.ABCMeta):
    """A protocol class to handle communication between the external process and the pilot itself.

    We communicate with external processes by defining
    protocols for them. A protocol for an external
    process handles communication with the process.

    The results of the process are stored in a `~asyncio.Future`
    and it is the protocol's job to parse the output
    of a process and store the result in the `~asyncio.Future`.

    Make concrete versions of this abstract class
    by implementing `_parseResults`, which parses the
    processess output and stores the result in the `done`
    Future.
    """

    FD_NAMES = ['stdin', 'stdout', 'stderr']

    def __init__(self, name, done, log_name=None, debug=False):
        """Create the protocol.

        Parameters
        -----------
        name : str
            A name for this task. Will be prepended to output.
        done : `~asyncio.Future`
            A Future object to store the result.
        log_name : str
            Name of logger, root logger used if none
        debug : boolean
            Default: False. Enable debug output.

        """
        self.name = name
        self.done = done
        self.debug = debug
        self.buffer = bytearray()
        self.log = get_logger(log_name, params.LOG_PATH)
        super().__init__()

    def connection_made(self, transport):
        """Run when a new process is started."""
        self.transport = transport
        pid = self.transport.get_pid()
        self.log.debug('{}: process {} started'.format(self.name, pid))

    def pipe_data_received(self, fd, data, log_bytes=False):
        """Log any readout is written to stdout or stderr."""
        if log_bytes:
            self.log.debug('{}: read {} bytes from {}'.format(
                self.name, len(data), self.FD_NAMES[fd]))

        if fd == 1:
            # data written to stdout
            lines_of_output = data.decode().strip().split('\n')
            for line in lines_of_output:
                self.log.info('{}: {}'.format(self.name, line.strip()))
            # store in buffer for processing when we finish
            self.buffer.extend(data)
        elif fd == 2:
            # data written to stderr
            lines_of_output = data.decode().strip().split('\n')
            for line in lines_of_output:
                self.log.error('{}: {}'.format(self.name, line.strip()))
            # store in buffer for processing when we finish
            self.buffer.extend(data)

    def process_exited(self):
        """Run when a process exits."""
        pid = self.transport.get_pid()
        self.log.debug('{}: process {} exited'.format(self.name, pid))

        retcode = self.transport.get_returncode()
        self.log.debug('{}: retcode={}'.format(self.name, retcode))

        cmd_output = bytes(self.buffer).decode()
        result = self._parse_results(cmd_output)
        if result is not None:
            self.log.debug('{}: result="{}"'.format(self.name, result))

        self.done.set_result((retcode, result))

    @abc.abstractmethod
    def _parse_results(self, cmd_output):
        """Parse the stdout buffer and store results."""
        return


class SimpleProtocol(TaskProtocol):
    """A simple protocol which does no parsing of the output.

    This protocol can be used to run any process where we just
    want to log the output but don't need to do anything with
    the results.
    """

    def _parse_results(self, cmd_output):
        return


class LoggedProtocol(TaskProtocol):
    """A fairly simple protocol which returns the last line of the output.

    This can be useful to report any errors that occur.
    """

    def _parse_results(self, cmd_output):
        if cmd_output is None or len(cmd_output) == 0:
            return
        output_lines = cmd_output.split('\n')
        last_line = output_lines[-1]
        if len(last_line) == 0:
            last_line = output_lines[-2]
        return last_line


def task_handler(func):
    """Wrapper to handle exceptions within the main pilot coroutines."""
    async def wrapper(pilot, *args, **kwargs):
        try:
            pilot.log.debug('starting {} routine'.format(func.__name__))
            await func(pilot, *args, **kwargs)
        except asyncio.CancelledError:
            pilot.log.warning('{} routine has been cancelled'.format(func.__name__))
        except Exception:
            pilot.log.error('caught exception in {} routine'.format(func.__name__))
            pilot.log.debug('', exc_info=True)
            send_slack_msg('Pilot detected exception in {} routine'.format(func.__name__))
            reason = 'Exception in {} routine'.format(func.__name__)
            asyncio.ensure_future(pilot.emergency_shutdown(reason))
        finally:
            try:
                pilot.log.debug('finished {} routine'.format(func.__name__))
            except NameError:
                # An interupt has already killed the logger
                # See https://stackoverflow.com/questions/64679139/
                print('finished {} routine'.format(func.__name__))
    return wrapper


class Pilot(object):
    """Run the scheduler and telescope.

    The Pilot uses asyncio to run several tasks concurrently,
    including checking the Scheduler for the best pointing to
    observe at the moment and then starting an observing task.

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
        self.log = get_logger('pilot', params.LOG_PATH,
                              log_stdout=True,
                              log_to_file=params.FILE_LOGGING,
                              log_to_stdout=params.STDOUT_LOGGING)
        self.log.info('Pilot started')

        # flag for daytime testing
        self.testing = testing

        # current and next pointing from scheduler
        self.current_id = None
        self.current_mintime = None
        self.current_start_time = None
        self.new_id = None
        self.new_mintime = None

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

        # define nightly tasks
        self.night_startdate = night_startdate()
        self.midnight = local_midnight(self.night_startdate)
        self.assign_tasks()

        # status flags, set during the night
        self.initial_hardware_check_complete = False
        self.initial_flags_check_complete = False
        self.initial_scheduler_check_complete = False
        self.startup_complete = False
        self.night_operations = False
        self.tasks_pending = False
        self.observing = False
        self.mount_is_tracking = False   # should the mount be tracking?
        self.dome_is_open = False        # should the dome be open?
        self.shutdown_now = False

        # hardware to keep track of and fix if necessary
        self.hardware = {'dome': monitors.DomeMonitor('closed', log=self.log),
                         'mnt': monitors.MntMonitor('parked', log=self.log),
                         'power': monitors.PowerMonitor(log=self.log),
                         'cam': monitors.CamMonitor('cool', log=self.log),
                         'ota': monitors.OTAMonitor('closed', log=self.log),
                         'filt': monitors.FiltMonitor(log=self.log),
                         'foc': monitors.FocMonitor(log=self.log),
                         'exq': monitors.ExqMonitor(log=self.log),
                         'conditions': monitors.ConditionsMonitor(log=self.log),
                         }
        self.current_errors = {k: set() for k in self.hardware.keys()}

        # store system mode
        self.system_mode = 'robotic'

        # reasons to pause and timers
        self.whypause = {'hardware': False, 'conditions': False, 'manual': False}
        self.time_paused = {'hardware': 0, 'conditions': 0, 'manual': 0}
        self.time_lost = 0
        self.bad_conditions_tasks_timer = 0
        self.bad_hardware = None
        self.bad_flags = None

        # dome check flags
        self.dome_confirmed_closed = False
        self.close_command_time = 0.

        # scheduler check flags
        self.force_scheduler_check = False
        self.scheduler_check_time = 0

    # Check routines
    @task_handler
    async def check_scheduler(self):
        """Check scheduler and update current pointing every 10 seconds."""
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
                    self.new_id, self.new_mintime = check_results

                    if self.new_id != self.current_id:
                        self.log.info('scheduler returns {} (NEW)'.format(self.new_id))
                    else:
                        self.log.debug('scheduler returns {}'.format(self.current_id))

                    self.initial_scheduler_check_complete = True

                self.scheduler_check_time = now
                self.force_scheduler_check = False
            await asyncio.sleep(1)

    @task_handler
    async def check_hardware(self):
        """Continuously monitor hardware and try to fix any issues."""
        self.log.info('hardware check routine initialised')
        good_sleep_time = 30
        bad_sleep_time = 10
        bad_timestamp = 0

        sleep_time = good_sleep_time
        while True:
            if self.system_mode == 'manual':
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
                    self.log.debug('{} info: {}'.format(monitor.monitor_id, monitor.info))
                    try:
                        monitor.recover()  # Will log recovery commands
                    except RecoveryError as err:
                        # Uh oh, we're out of options
                        send_slack_msg(str(err))
                        asyncio.ensure_future(self.emergency_shutdown('Unfixable hardware error'))

            if error_count > 0:
                self.bad_hardware = ', '.join([daemon_id for daemon_id in self.current_errors
                                               if len(self.current_errors[daemon_id]) > 0])
                self.log.warning('Bad hardware: ({})'.format(self.bad_hardware))

                await self.handle_pause('hardware', True)

                # check more frequently until fixed, and save time for delay afterwards
                sleep_time = bad_sleep_time
                bad_timestamp = time.time()
            else:
                self.log.debug('hardware status AOK')
                self.bad_hardware = None

                await self.handle_pause('hardware', False)

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

    @task_handler
    async def check_flags(self):
        """Check the conditions and status flags."""
        self.log.info('flags check routine initialised')

        sleep_time = 10
        while True:
            # get system mode
            status = Status()
            self.system_mode = status.mode

            # make sure pilot isn't running in engineering mode
            if self.system_mode == 'engineering':
                self.log.warning('System is in engineering mode, exiting abnormally')
                send_slack_msg('Pilot should not be running when system is in engineering mode')
                send_slack_msg('Pilot exiting abnormally')
                sys.exit(1)

            # check for the emergency file
            if status.emergency_shutdown:
                reasons = ', '.join(status.emergency_shutdown_reasons)
                self.log.warning('Emergency shutdown file detected: ({})'.format(reasons))
                if not self.startup_complete:
                    # If we haven't started yet then just quit here
                    send_slack_msg('Pilot exiting due to emergency shutdown')
                    sys.exit(1)
                else:
                    # The file has appeared while we're running, shut down now!
                    asyncio.ensure_future(self.emergency_shutdown(reasons))

            # pause if system is in manual mode
            if self.system_mode == 'manual':
                await self.handle_pause('manual', True)
            else:
                await self.handle_pause('manual', False)

            # pause if conditions are bad
            conditions = Conditions()
            if conditions.bad:
                self.bad_flags = ', '.join(conditions.bad_flags)
                self.log.warning('Bad conditions flags: ({})'.format(self.bad_flags))
                await self.handle_pause('conditions', True)
            else:
                self.bad_flags = None
                await self.handle_pause('conditions', False)

            # only allow the night marshal to start after a
            # successful flags check
            self.initial_flags_check_complete = True

            await asyncio.sleep(sleep_time)

    @task_handler
    async def check_dome(self):
        """Double check that dome is closed if it should be."""
        self.log.info('dome check routine initialised')

        sleep_time = 10
        while True:
            if self.system_mode == 'manual':
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

    @task_handler
    async def check_time_paused(self):
        """Keep track of the time the pilot has been paused."""
        self.log.info('pause check routine initialised')

        sleep_time = 10
        while True:
            if self.paused:
                reasons = [k for k in self.whypause if self.whypause[k]]

                # log why we're paused
                self.log.info('pilot paused ({})'.format(', '.join(reasons)))
                self.log.debug('pause times: {}'.format(self.time_paused))

                # track the observing time lost (this is reset whenever a new observation starts)
                self.time_lost += sleep_time

                # track the time paused for each reason (this is reset whenever the system unpauses)
                for reason in reasons:
                    self.time_paused[reason] += sleep_time

                # check if we're ONLY paused due to bad conditions
                # this means the script won't run if there's a hardware error or we're in manual
                if not self.whypause['hardware'] and not self.whypause['manual']:
                    # only start checking after the sun has set and we've finished normal darks
                    if (self.startup_complete and get_sunalt(Time.now()) < 0 and
                            not (self.tasks_pending or self.running_script)):
                        # check the time since the script last ran
                        delta = self.time_paused['conditions'] - self.bad_conditions_tasks_timer
                        if delta > params.BAD_CONDITIONS_TASKS_PERIOD:
                            paused_hours = self.time_paused['conditions'] / (60 * 60)
                            self.log.debug('bad conditions timer: {:.2f}h'.format(paused_hours))

                            # here we can run an obs script during poor weather
                            self.log.info('running bad conditions tasks')
                            # Note we want to be able to move the mount, so we have to set the
                            # monitor status to 'tracking' here.
                            # This means we can't park during the darks, which is annoying
                            # (because that would count as a hardware error).
                            # So we have to park again in start_script() once the script finishes.
                            if not self.mount_is_tracking:
                                await self.unpark_mount()
                            asyncio.ensure_future(self.start_script('BADCOND',
                                                                    'badConditionsTasks.py',
                                                                    args=[3]))

                            # save the counter
                            self.bad_conditions_tasks_timer = self.time_paused['conditions']

            await asyncio.sleep(sleep_time)

    # Night marshal
    @task_handler
    async def nightmarshal(self, restart=False, late=False):
        """Start tasks at the right time (based on the sun's altitude).

        Parameters
        ----------
        restart : bool
            If true, we will skip startup tasks and get straight to observing

        late : bool
            If true, we will try to do evening tasks even if it's too late
            (note FLATS will fail anyway)

        """
        self.log.info('night marshal initialised')

        # Send night start reports
        if not restart:
            send_timing_report(startup_sunalt=self.startup_sunalt,
                               open_sunalt=self.open_sunalt,
                               obs_start_sunalt=self.obs_start_sunalt,
                               obs_stop_sunalt=self.obs_stop_sunalt,
                               close_sunalt=self.close_sunalt,
                               )
            send_database_report()

        # Wait for first flag check
        while not self.initial_flags_check_complete:
            self.log.info('waiting for the first successful flags check')
            await asyncio.sleep(30)

        # Wait until manual mode is lifted (if starting in manual)
        message_sent = False
        while self.whypause['manual']:
            self.log.info('in manual mode, tasks suspended')
            if not message_sent:
                send_slack_msg('Pilot has started in manual mode, tasks suspended')
                message_sent = True
            await asyncio.sleep(30)

        # Wait for the right sunalt to start
        if not restart:
            await self.wait_for_sunalt(self.startup_sunalt, 'STARTUP')

        # 1) Startup (skip if we're restarting)
        if not restart:
            if not self.startup_complete:
                await self.startup()
        else:
            # Need to mark startup complete here, since the startup function won't
            self.startup_complete = True
        self.log.info('startup complete')

        # Wait for first successful hardware check
        while not self.initial_hardware_check_complete:
            self.log.info('waiting for the first successful hardware check')
            await asyncio.sleep(30)

        # 2) Daytime tasks (skip if we're restarting, and do even in bad weather)
        if not restart:
            await self.run_through_tasks(self.daytime_tasks,
                                         rising=False,
                                         ignore_conditions=True,
                                         ignore_late=late)

        # Wait for the right sunalt to open dome
        await self.wait_for_sunalt(self.open_sunalt, 'OPEN')

        # Wait for daytime tasks to finish
        await self.wait_for_tasks()

        # Wait for any pause to clear (can't open if conditions are bad or there's a hardware error)
        while self.paused:
            self.log.info('opening suspended until pause is cleared')
            await asyncio.sleep(30)

        # 3) Open the dome
        self.log.info('starting night operations')
        self.night_operations = True
        await self.open_dome()
        await self.unpark_mount()

        # 4) Evening tasks (skip if we're restarting)
        if not restart:
            await self.run_through_tasks(self.evening_tasks,
                                         rising=False,
                                         ignore_late=late)

        # Wait for darkness
        await self.wait_for_sunalt(self.obs_start_sunalt, 'OBS')

        # Wait for evening tasks to finish
        await self.wait_for_tasks()

        # 5) Start observing (will stop at the given sunalt)
        await self.observe(self.obs_stop_sunalt)

        # 6) Morning tasks
        await self.run_through_tasks(self.morning_tasks,
                                     rising=True,
                                     ignore_late=False)

        # Wait for morning tasks to finish
        await self.wait_for_tasks()

        # 7) All tasks finished, trigger shutdown
        self.log.info('finished night operations')
        self.night_operations = False
        self.shutdown_now = True

    # External scripts
    async def start_script(self, name, script, args=None, protocol=None):
        """Launch an external Python script.

        Parameters
        ----------
        name : str
            A name for this process. Prepended to output from process.
        script : str
            The Python script to execute.
        args : list, optional
            Arguments to the script.
        protocol : `gtecs.control.pilot.TaskProtocol`, optional
            Protocol used to process output from Process
            Default is `LoggedProtocol`

        """
        # first cancel any currently running script
        await self.cancel_running_script(why='new script starting')

        # create a future to store result in
        self.running_script_result = asyncio.Future()

        # fill the name, future and log_name arguments of protocol(...)
        # using functools.partial
        if protocol is None:
            protocol = LoggedProtocol
        script_name = name
        if name == 'OBS' and args is not None:
            # Add the pointing ID to the name used when logging
            script_name += '-' + args[0]
        factory = functools.partial(protocol, script_name, self.running_script_result, 'pilot')

        # create the process coroutine which will return
        # a 'transport' and 'protocol' when scheduled
        loop = asyncio.get_event_loop()
        with pkg_resources.path('gtecs.control._obs_scripts', script) as path:
            cmd = [str(path), *args] if args is not None else [str(path)]
            proc = loop.subprocess_exec(factory, params.PYTHON_EXE, '-u', *cmd,
                                        stdin=None)

        # start the process and get transport and protocol for control of it
        self.log.info('starting {}'.format(name))
        self.running_script = name
        self.running_script_transport, self.running_script_protocol = await proc

        # process started, await completion
        retcode, result = await self.running_script_result

        # done
        if retcode != 0:
            # process finished abnormally
            self.log.warning('{} ended abnormally'.format(name))
            if ('Error' in result) or ('Exception' in result):
                msg = 'Pilot {} task ended abnormally ("{}")'.format(name, result)
                send_slack_msg(msg)
            elif name not in ['OBS', 'BADCOND']:
                # It's not uncommon for OBS and BADCOND to be canceled early
                msg = 'Pilot {} task ended abnormally'.format(name)
                send_slack_msg(msg)

            # if we were observing, make sure the pointing is marked as aborted
            # (the observe.py closer should do this anyway, but best to be sure)
            if name == 'OBS' and self.current_id is not None:
                mark_aborted(self.current_id)
                self.log.debug('pointing {} was aborted'.format(self.current_id))

        self.log.info('finished {}'.format(name))
        self.running_script = None

        # if it was an observation that just finished (aborted or not) force a scheduler check
        if name == 'OBS':
            self.log.debug('forcing scheduler check')
            self.force_scheduler_check = True

        # if BADCOND has just finished and we're still paused then park the mount again
        if name == 'BADCOND':
            if (self.paused and not self.whypause['hardware'] and not self.whypause['manual'] and
                    self.mount_is_tracking):
                self.park_mount()

        return retcode, result

    async def cancel_running_script(self, why):
        """Cancel the currently running Python script.

        This does nothing if the script is already done.
        """
        if (self.running_script is not None and
                self.running_script_transport is not None and
                self.running_script_transport.get_returncode() is None):
            self.log.info('killing {}, reason: "{}"'.format(self.running_script, why))
            if self.running_script not in ['OBS', 'BADCOND']:
                msg = 'Pilot killing {} task early ("{}")'.format(self.running_script, why)
                send_slack_msg(msg)

            # check script is still running again, just in case
            try:
                self.running_script_transport.terminate()
                await self.running_script_result
            except Exception:
                self.log.debug('{} already exited?'.format(self.running_script))

            # Make sure everything is stopped
            execute_command('exq clear')
            execute_command('cam abort')
            if self.mount_is_tracking:
                execute_command('mnt stop')
                execute_command('mnt clear')
                execute_command('mnt track')

    # Daily tasks
    def assign_tasks(self):
        """Assign times and details of the daily tasks carried out by the pilot.

        In an ideal world these would all be defined in params, or even better in a
        JSON config file.
        """
        # startup
        self.startup_sunalt = 12

        # daytime tasks: done before opening the dome
        darks = {'name': 'DARKS',
                 'sunalt': 6,
                 'late_sunalt': 0,
                 'script': 'takeBiasesAndDarks.py',
                 'args': [str(params.NUM_DARKS)],
                 }
        # xdarks = {'name': 'XDARKS',
        #           'sunalt': 1,
        #           'late_sunalt': 0,
        #           'script': 'takeExtraDarks.py',
        #           'args': [],
        #           }

        # self.daytime_tasks = [darks, xdarks]
        self.daytime_tasks = [darks]

        # open
        self.open_sunalt = -4

        # evening tasks: done after opening the dome, before observing starts
        flats_e = {'name': 'FLATS',
                   'sunalt': -4.5,
                   'late_sunalt': -7,
                   'script': 'takeFlats.py',
                   'args': ['EVE'],
                   }
        autofoc = {'name': 'FOC',
                   'sunalt': -11,
                   'late_sunalt': None,  # Always autofocus if opening late
                   'script': 'autoFocus.py',
                   'args': ['-n', '1', '-t', '5'],
                   }

        self.evening_tasks = [flats_e, autofoc]

        # observing
        self.obs_start_sunalt = -12
        self.obs_stop_sunalt = -12  # -14 WITH FOCRUN

        # morning tasks: done after observing, before closing the dome
        # foc_run = {'name': 'FOCRUN',
        #           'sunalt': -14.5,
        #           'late_sunalt': -13,
        #           'script': 'takeFocusRun.py',
        #           'args': ['1000', '100', 'n'],
        #           }
        flats_m = {'name': 'FLATS',
                   'sunalt': -10,
                   'late_sunalt': -7.55,
                   'script': 'takeFlats.py',
                   'args': ['MORN'],
                   }

        # self.morning_tasks = [foc_run, flats_m]
        self.morning_tasks = [flats_m]

        # close sunalt
        # (NB we usually finish early, this is the limit used for the night countdown)
        self.close_sunalt = 0

    async def run_through_tasks(self, task_list, rising=False,
                                ignore_conditions=False,
                                ignore_late=False):
        """Just pop tasks off a list and run them at correct sunalt."""
        while task_list:
            self.tasks_pending = True
            task = task_list.pop(0)
            name = task['name']
            sunalt = task['sunalt']
            late_sunalt = task['late_sunalt']

            self.log.info('next task: {}'.format(name))

            # wait for the right sun altitude
            can_start = await self.wait_for_sunalt(sunalt, name, rising,
                                                   late_sunalt if not ignore_late else None)

            if not can_start:
                # too late
                self.log.info('too late to start {}'.format(name))
                continue

            elif ((self.whypause['hardware']) or
                  (self.whypause['manual']) or
                  (self.whypause['conditions'] and not ignore_conditions)):
                # need to check if we're paused
                # if ignore_conditions (daytime tasks) we can start even if
                # paused for conditions, but not for other reasons
                self.log.info('currently paused, will not start {}'.format(name))
                await asyncio.sleep(15)
                continue

            elif self.testing or ignore_late:
                # wait for each script to finish
                await self.start_script(name, task['script'], args=task['args'])
            else:
                # don't wait for script finish, but start each one when the
                # sun alt says so, cancelling running script if not done
                asyncio.ensure_future(self.start_script(name, task['script'], args=task['args']))

            await asyncio.sleep(1)

        self.tasks_pending = False

    async def wait_for_tasks(self):
        """Return when all running tasks are complete."""
        while self.tasks_pending or self.running_script:
            self.log.debug('waiting for running tasks to finish')
            await asyncio.sleep(10)
        return True

    async def wait_for_sunalt(self, sunalt, why,
                              rising=False, late_sunalt=None):
        """Return when the sun reaches the given altitude.

        Parameters
        ----------
        sunalt : float
            sun altitude in degrees to wait for
        why : str
            a brief reason why we're waiting, helpful for the log
        rising : bool
            whether the sun is rising or setting
        late_sunalt : float, optional (default=None)
            if given, return False if the sun is already past the given altitude

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

        # check if we're too late
        if late_sunalt is not None:
            sunalt_now = get_sunalt(now)
            too_late = False

            # check if we're on the wrong side of midnight
            if not rising and now > self.midnight:
                too_late = True
            elif rising and now < self.midnight:
                too_late = True

            # check if we've missed the late sunalt
            if not rising and sunalt_now < late_sunalt:
                too_late = True
            elif rising and sunalt_now > late_sunalt:
                too_late = True

            if too_late:
                self.log.info('sunalt={:.1f}, after {:.1f}: too late to start {}'.format(
                    sunalt_now, late_sunalt, why))
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

    # Observing
    async def observe(self, until_sunalt=-12, last_obs_sunalt=None):
        """Observe until further notice.

        Parameters
        ----------
        until_sunalt : float, default = -12
            sun altitude at which to stop observing.

        last_obs_sunalt : float, optional
            sun altitude at which to schedule last new observation.
            default is two degrees earlier than `until_sunalt`,
            e.g. if until_sunalt=-12 (the default) then last_obs_sunalt=-14

        """
        if last_obs_sunalt is None:
            last_obs_sunalt = until_sunalt - 2

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
            sunalt_now = get_sunalt(now)
            if now > self.midnight:
                if sunalt_now > last_obs_sunalt and self.observing:
                    self.log.debug('stopping scheduler checks, current observation will continue')
                    self.observing = False
                if sunalt_now > until_sunalt:
                    # end observing
                    break

            # See if a new target has arrived and mark the pointing appropriately
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
                    # We should already be parked
                    self.log.warning('nothing to observe!')
                    if not self.testing:
                        send_slack_msg('Pilot has nothing to observe!')

            elif self.new_id is not None:
                if self.current_id is not None:
                    self.log.info('got new pointing from scheduler {}'.format(self.new_id))

                    # Get current pointing status
                    current_status = get_pointing_status(self.current_id)
                    self.log.debug('current pointing {} status = {}'.format(
                                   self.current_id, current_status))

                    # Check if there is currently an observation running
                    if self.running_script == 'OBS':
                        # Cancel the script
                        # NOTE this will mark the pointing as aborted
                        await self.cancel_running_script(why='new pointing')

                        # Find the elapsed time, accounting for time lost due to being paused
                        elapsed = time.time() - self.current_start_time - self.time_lost
                        self.log.debug('min time = {:.1f}, time elapsed = {:.1f}'.format(
                                       self.current_mintime, elapsed))

                        if elapsed > self.current_mintime:
                            # We observed enough, mark the pointing as completed
                            mark_completed(self.current_id)
                            self.log.debug('pointing {} was completed'.format(self.current_id))
                        elif current_status == 'completed':
                            # We killed the script just as it was finishing,
                            # (after it marked the pointing as completed, but before it returned),
                            # so we need to re-mark the pointing as completed here.
                            mark_completed(self.current_id)
                            self.log.debug('pointing {} was completed'.format(self.current_id))
                        else:
                            # Mark the pointing as interrupted
                            mark_interrupted(self.current_id)
                            self.log.debug('pointing {} was interrupted'.format(self.current_id))

                else:
                    self.log.info('got pointing from scheduler {}'.format(self.new_id))
                    # we weren't doing anything, which implies we were parked
                    await self.unpark_mount()

                # start the new pointing (the script will mark it as running too, but best to do it
                # ASAP so the scheduler recognises it)
                self.log.debug('starting pointing {}'.format(self.new_id))
                mark_running(self.new_id)

                asyncio.ensure_future(self.start_script('OBS',
                                                        'observe.py',
                                                        args=[str(self.new_id)]))

                self.current_start_time = time.time()
                self.current_id = self.new_id
                self.current_mintime = self.new_mintime
                self.time_lost = 0

            else:
                self.log.info('nothing to do, parking mount')
                if not self.testing:
                    send_slack_msg('Pilot has nothing to do, parking mount')
                self.park_mount()
                execute_command('exq clear')
                execute_command('cam abort')
                self.current_id = None
                self.current_mintime = None
                # If we've interrupted a pointing it needs to be cancelled,
                # this will mark it as aborted
                await self.cancel_running_script('obs parking')

            await asyncio.sleep(sleep_time)

        self.log.info('observing completed!')
        self.observing = False

        # finish observing
        execute_command('exq clear')
        execute_command('cam abort')

        # If we've interrupted a pointing it needs to be cancelled,
        # this will mark it as aborted
        await self.cancel_running_script('obs finished')
        self.current_id = None
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
            one of 'manual', 'conditions' or 'hardware'
        pause : bool
            does reason suggest a pause (True) or unpause (False)

        """
        if pause and not self.whypause[reason]:
            # we can set this here because we want to pause right away
            self.whypause[reason] = True

            if reason == 'conditions':
                self.log.warning('Pausing (bad conditions: {})'.format(self.bad_flags))
                send_slack_msg('Pilot is pausing (bad conditions: {})'.format(self.bad_flags))

                if self.dome_is_open:
                    # only need to stop scripts if the dome is open
                    # (this way we don't kill darks if the weather goes bad)
                    execute_command('exq pause')
                    execute_command('cam abort')
                    await self.cancel_running_script('bad conditions')

                # always make sure we're closed and parked
                self.close_dome()
                self.park_mount()

                # reset the timer for bad conditions tasks to zero
                self.bad_conditions_tasks_timer = 0

            elif reason == 'hardware':
                self.log.warning('Pausing (hardware fault: {})'.format(self.bad_hardware))
                send_slack_msg('Pilot is pausing (hardware fault: {})'.format(self.bad_hardware))

                if self.running_script == 'STARTUP':
                    # don't cancel startup due to hardware issue
                    # (this shouldn't happen anyway, since hardware checks don't start until after
                    #  the startup script has been run)
                    pass

                # stop current actions
                if self.mount_is_tracking:
                    self.stop_mount()
                execute_command('exq clear')
                execute_command('cam abort')
                await self.cancel_running_script('hardware fault')

            elif reason == 'manual':
                self.log.warning('Pausing (system in manual mode)')
                send_slack_msg('Pilot is pausing (system in manual mode)')

                # kill the current script, we usually do it manually anyway
                if self.mount_is_tracking:
                    self.stop_mount()
                execute_command('exq clear')
                execute_command('cam abort')
                await self.cancel_running_script('system to manual mode')

        if not pause:
            if self.time_paused[reason] > 0:
                # reset the counter if we are no longer paused
                self.time_paused[reason] = 0

            # does this change suggest a global unpause?
            # (i.e. we're not paused for any other reason)
            unpause = not any(self.whypause[key] for key in self.whypause if key != reason)
            if unpause and self.paused:
                # OK, we can resume
                self.log.info('resuming operations')
                send_slack_msg('Pilot is resuming operations')
                if self.night_operations:
                    # get the dome and mount back to the correct mode
                    if self.running_script == 'BADCOND':
                        # Cancel the BADCOND script when unpausing
                        await self.cancel_running_script('unpausing')
                    if not self.dome_is_open:
                        # open the dome if it's closed
                        # this way wait and don't resume until the dome is open
                        await self.open_dome()
                    if not self.mount_is_tracking:
                        # unpark the mount if it's parked (which it will if the dome was closed),
                        # and start tracking again (in case it just stopped)
                        await self.unpark_mount()
                    execute_command('exq resume')

        # finally, change global pause status by updating flag
        # by putting this last, we don't unpause until the dome
        # is actually open or hardware is fixed etc.
        self.whypause[reason] = pause

    # Night countdown
    async def night_countdown(self, stop_time=None):
        """Shut down the system if triggered or at the stop time as a backup.

        The pilot loop will run until this function exits.

        If the system triggers a shutdown "normally" (either through the night marshal finishing or
        an emergency shutdown) then this function will pick it up, run the shutdown command and
        exit, stopping the pilot.

        If something fails then this function will independently ensure the system shuts down once
        the stop time is reached.
        """
        self.log.info('night countdown initialised')

        if stop_time is None:
            stop_time = sunalt_time(self.night_startdate, self.close_sunalt * u.deg, eve=False)
        self.log.info('setting end of night for {}'.format(stop_time.iso))

        last_log = Time.now()
        sleep_time = 10
        while True:
            now = Time.now()

            # check if the shutdown command has been sent
            if self.shutdown_now:
                self.log.info('shutdown triggered, exiting night countdown')
                break

            # as an independent backup, check if we have reached the stop time
            if now > stop_time:
                self.log.info('end of night reached, forcing shutdown')
                break

            # log line
            if now - last_log > 60 * u.second:
                last_log = now
                stop_time.precision = 0
                delta = stop_time - now
                delta_min = delta.to('min').value
                status_str = 'end of night at {} ({:.0f} mins)'.format(stop_time.iso, delta_min)
                if delta_min < 30:
                    self.log.info(status_str)
                else:
                    self.log.debug(status_str)

            await asyncio.sleep(sleep_time)

        # Run the shutdown command
        self.log.info('starting shutdown')
        await self.shutdown()

        self.log.info('finished for tonight')

    # Startup and shutdown commands
    async def startup(self):
        """Start up the system.

        Runs the startup script, sets the startup_complete flag and sends the startup report
        """
        # run startup script
        self.log.debug('running startup script')
        await self.start_script('STARTUP', 'startup.py')

        # flag that startup has finished
        self.startup_complete = True

        # send the startup report
        send_startup_report(msg='*Pilot reports startup complete*')

        self.log.debug('startup process complete')

    async def shutdown(self):
        """Shut down the system.

        Close any running scripts and tasks, run the shutdown script, ensure the
        dome is closed and finish.
        """
        # first shut down all running tasks
        # this is so check_flags doesn't initiate two shutdowns,
        # or we don't end up trying to restart if conditions clear
        # or an "unfixible" hardware error gets fixed
        self.log.info('cancelling running tasks')
        for task in self.running_tasks:
            task.cancel()

        # then cancel any currently running script
        await self.cancel_running_script(why='shutdown')

        # run shutdown script
        self.log.info('running shutdown script')
        await self.start_script('SHUTDOWN', 'shutdown.py')

        # flag that the shutdown script has been run, by un-flagging startup
        self.startup_complete = False

        # next and most important.
        # NEVER STOP WITHOUT CLOSING THE DOME!
        # EMAIL IF DOME WON'T CLOSE
        self.log.info('making sure dome is closed')
        await self.close_dome_confirm()

        self.log.info('shutdown process complete')

    async def emergency_shutdown(self, why):
        """Send a warning and then shut down."""
        if not self.shutdown_now:  # Don't trigger multiple times
            self.log.info('performing emergency shutdown: {}'.format(why))
            send_slack_msg('Pilot is performing an emergency shutdown: {}'.format(why))

            self.log.info('closing dome immediately')
            self.stop_mount()
            self.close_dome()

            # trigger night countdown to shutdown
            self.shutdown_now = True
        else:
            self.log.info('multiple emergency shutdowns triggered: {}'.format(why))

    # Hardware commands
    async def open_dome(self):
        """Open the dome and await until it is finished."""
        # make sure we are parked before moving the dome
        if self.mount_is_tracking:
            self.park_mount()

        self.log.info('opening dome')
        send_slack_msg('Pilot is opening the dome')
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

        if self.startup_complete:
            # If we haven't started then we can't move the covers,
            # because the interfaces are disabled if the cameras are powered down.
            self.log.info('opening mirror covers')
            execute_command('ota open')
            self.hardware['ota'].mode = 'open'
            # wait for mirror covers to open
            sleep_time = 1
            while True:
                cover_status = self.hardware['ota'].get_hardware_status()
                self.log.debug('covers are {}'.format(cover_status))
                if cover_status == 'full_open':
                    break
                await asyncio.sleep(sleep_time)
            self.log.info('mirror covers confirmed open')

    def close_dome(self):
        """Send the dome close command and return immediately."""
        if self.startup_complete:
            # See above: if we haven't started (or, more likely here, have already shutdown)
            # then we can't move the covers.
            self.log.info('closing mirror covers')
            execute_command('ota close')
            self.hardware['ota'].mode = 'closed'

        # make sure we are parked before moving the dome
        if self.mount_is_tracking:
            self.park_mount()

        self.log.info('closing dome')
        dome_status = self.hardware['dome'].get_hardware_status()
        if dome_status not in ['closed', 'in_lockdown']:
            send_slack_msg('Pilot is closing the dome')
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
                msg = 'IMPORTANT: Pilot cannot close dome!'
                send_slack_msg(msg)
                try:
                    send_email(message=msg)
                except Exception:
                    self.log.error('Error sending email')

            await asyncio.sleep(sleep_time)

        self.dome_confirmed_closed = True
        send_slack_msg('Pilot confirmed dome is closed')
        self.log.info('dome confirmed closed')

    async def unpark_mount(self):
        """Unpark the mount (if it's parked), start tracking and await until it is ready."""
        if self.hardware['mnt'].mode == 'parked':
            self.log.info('unparking mount')
            execute_command('mnt unpark')
        self.mount_is_tracking = True
        self.hardware['mnt'].mode = 'tracking'  # skip stopped and go straight to tracking
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

    def stop_mount(self):
        """Tell the mount to stop moving immediately."""
        self.log.info('stopping mount')
        execute_command('mnt stop')
        execute_command('mnt clear')  # clear any target, so it can't resume tracking
        self.mount_is_tracking = False
        self.hardware['mnt'].mode = 'stopped'

    def park_mount(self):
        """Send the mount park command and return immediately."""
        self.log.info('parking mount')
        execute_command('mnt park')
        self.mount_is_tracking = False
        self.hardware['mnt'].mode = 'parked'


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
    print('Pilot started')
    if not restart:
        send_slack_msg('Pilot started')
    else:
        send_slack_msg('Pilot restarted')

    loop = asyncio.get_event_loop()
    loop.set_debug(False)
    pilot = Pilot(testing=test)

    # Start the recurrent tasks
    pilot.running_tasks.extend([
        asyncio.ensure_future(pilot.check_hardware()),  # periodically check hardware
        asyncio.ensure_future(pilot.check_time_paused()),  # keep track of time paused
        asyncio.ensure_future(pilot.check_flags()),  # check conditions and system flags
        asyncio.ensure_future(pilot.check_scheduler()),  # start checking the schedule
        asyncio.ensure_future(pilot.check_dome()),  # keep a close eye on dome
        asyncio.ensure_future(pilot.nightmarshal(restart, late)),  # run through scheduled tasks
    ])

    # Loop until the night countdown finishes (or the pilot exits early)
    if pilot.testing:
        # Force the countdown to finish in 15 minutes
        stop_time = Time.now() + 15 * u.minute
        stop_signal = pilot.night_countdown(stop_time)
    else:
        stop_signal = pilot.night_countdown()
    try:
        # Actually start the event loop - nothing happens until this line is reached!
        loop.run_until_complete(stop_signal)
    except asyncio.CancelledError:
        print('Tasks cancelled')
    finally:
        print('Pilot done')
        send_slack_msg('Pilot done')
        loop.close()
