"""Master control program for the observatory."""

import abc
import asyncio
import functools
import importlib.resources as pkg_resources
import sys
import time

from astropy import units as u
from astropy.time import Time

from gtecs.common import logging
from gtecs.common.system import execute_command

from . import monitors
from . import params
from .astronomy import get_sunalt, local_midnight, sunalt_time
from .flags import Conditions, Status
from .scheduling import update_schedule_pyro, update_schedule_server_async
from .slack import send_slack_msg, send_startup_report, send_timing_report


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
    process output and stores the result in the `done` Future.
    """

    FD_NAMES = ['stdin', 'stdout', 'stderr']

    def __init__(self, name, done, log_name=None, debug=False):
        """Create the protocol.

        Parameters
        ----------
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
        self.log = logging.get_logger(log_name)
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
    """Handle exceptions within the main pilot coroutines."""
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
                # An interrupt has already killed the logger
                # See https://stackoverflow.com/questions/64679139/
                print('finished {} routine'.format(func.__name__))
    return wrapper


class Pilot:
    """Operate the telescope autonomously.

    The Pilot uses asyncio to run several tasks concurrently,
    including checking the scheduler for the best pointing to
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
        self.log = logging.get_logger('pilot')
        self.log.info('Pilot started')

        # flag for daytime testing
        self.testing = testing

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
        self.midnight = local_midnight()
        self.assign_tasks()

        # status flags, set during the night
        self.initial_hardware_check_complete = False
        self.initial_flags_check_complete = False
        self.startup_complete = False
        self.night_operations = False
        self.tasks_pending = False
        self.observing = False
        self.mount_is_tracking = False   # should the mount be tracking?
        self.dome_is_open = False        # should the dome be open?
        self.shutdown_now = False

        # current pointing details
        self.current_pointing = None
        self.current_start_time = None
        self.current_status = None
        self.scheduler_updating = False

        # hardware to keep track of and fix if necessary
        self.hardware = {
            'dome': monitors.DomeMonitor('closed', log=self.log),
            'mnt': monitors.MntMonitor(params.MOUNT_CLASS, 'parked', log=self.log),
            'power': monitors.PowerMonitor(params.POWER_UNITS, log=self.log),
            'cam': monitors.CamMonitor(params.UTS_WITH_CAMERAS, 'cool', log=self.log),
            'ota': monitors.OTAMonitor(params.UTS_WITH_COVERS, 'closed', log=self.log),
            'filt': monitors.FiltMonitor(params.UTS_WITH_FILTERWHEELS, log=self.log),
            'foc': monitors.FocMonitor(params.UTS_WITH_FOCUSERS, log=self.log),
            'exq': monitors.ExqMonitor(log=self.log),
            'conditions': monitors.ConditionsMonitor(log=self.log),
        }
        self.current_errors = {k: set() for k in self.hardware.keys()}

        # store system mode
        self.system_mode = 'robotic'

        # reasons to pause and timers
        self.whypause = {'hardware': False, 'conditions': False, 'manual': False}
        self.time_paused = {'hardware': 0, 'conditions': 0, 'manual': 0}
        self.bad_conditions_tasks_timer = 0
        self.bad_hardware = None
        self.bad_flags = None

        # dome check flags
        self.dome_confirmed_closed = False
        self.close_command_time = 0.

    # Check routines
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

            if self.running_script == 'BADCOND':
                self.log.debug('hardware checks suspended during BADCOND routine')
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
                    except monitors.RecoveryError as error:
                        # Uh oh, we're out of options
                        send_slack_msg(str(error))
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
                    await self.close_dome()
            await asyncio.sleep(sleep_time)

    @task_handler
    async def check_time_paused(self):
        """Keep track of the time the pilot has been paused."""
        self.log.info('pause check routine initialised')

        sleep_time = 10
        while True:
            if self.paused:
                reasons = [k for k in self.whypause if self.whypause[k]]

                # Log why we're paused
                self.log.info('pilot paused ({})'.format(', '.join(reasons)))
                self.log.debug('pause times: {}'.format(self.time_paused))

                # Track the time paused for each reason
                # (this is reset whenever the system unpauses)
                for reason in reasons:
                    self.time_paused[reason] += sleep_time

                # Check if we're paused due to bad conditions, and how long
                if self.whypause['conditions']:
                    delta = self.time_paused['conditions'] - self.bad_conditions_tasks_timer
                    if delta > params.BAD_CONDITIONS_TASKS_PERIOD:
                        # We've been paused for long enough to start the bad conditions tasks
                        paused_hours = self.time_paused['conditions'] / (60 * 60)
                        self.log.debug('bad conditions timer: {:.2f}h'.format(paused_hours))

                        if self.whypause['hardware'] or self.whypause['manual']:
                            # We don't want to start the script if we're paused for another reason
                            self.log.debug('bad conditions tasks suspended while otherwise paused')
                        elif self.tasks_pending or self.running_script:
                            # We don't want to start the script if we're running something else
                            # (though since we're paused this should only be another BADCOND,
                            #  but if it's been this long something is very wrong...)
                            self.log.debug('bad conditions tasks suspended until script finished')
                        elif not self.startup_complete:
                            # We don't want to start the script until after startup
                            self.log.debug('bad conditions tasks suspended until after startup')
                        elif get_sunalt(Time.now()) > self.obs_start_sunalt:
                            # We don't want to start the script until after dark
                            self.log.debug('bad conditions tasks suspended until dark time')
                        else:
                            # Finally we should now be safe to start the script
                            self.log.info('running bad conditions tasks')
                            # Note hardware checks are disabled while BADCOND is running,
                            # otherwise we'd have trouble with moving the mount or the mirror
                            # covers because they'd be in the "wrong" position
                            asyncio.ensure_future(self.start_script('BADCOND',
                                                                    'badConditionsTasks.py',
                                                                    args=['3']))
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

        # 1) Startup (even if we're restarting, it shouldn't take long but skip the report)
        if not self.startup_complete:
            await self.startup(send_report=not restart)
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
        args : list of str, optional
            Arguments to the script (must be strings).
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

        # create the process coroutine
        loop = asyncio.get_event_loop()
        with pkg_resources.path('gtecs.control._obs_scripts', script) as path:
            cmd = [str(path), *[str(a) for a in args]] if args is not None else [str(path)]
            proc = loop.subprocess_exec(factory, params.PYTHON_EXE, '-u', *cmd,
                                        stdin=None)

        # start the process and get transport and protocol for control of it
        self.log.info('starting {}'.format(name))
        self.log.debug('> {}'.format(' '.join(cmd)))
        self.running_script = name
        self.running_script_transport, self.running_script_protocol = await proc

        # process started, await completion
        retcode, result = await self.running_script_result

        # done
        if retcode != 0:
            # process finished abnormally
            self.log.warning('{} ended abnormally'.format(name))
            if isinstance(result, str) and ('Error' in result or 'Exception' in result):
                msg = 'Pilot {} task ended abnormally ("{}")'.format(name, result)
                send_slack_msg(msg)
            elif name not in ['OBS', 'BADCOND']:
                # It's not uncommon for OBS and BADCOND to be canceled early
                msg = 'Pilot {} task ended abnormally'.format(name)
                send_slack_msg(msg)

        self.log.info('finished {}'.format(name))
        self.running_script = None

        # cleanup for specific scripts
        if name == 'OBS':
            # If it was an observation that just finished then update the status.
            # It should then be sent to the database in the next scheduler update.
            # First we need to wait just in case the scheduler is currently being updated,
            # otherwise things get messed up.
            while self.scheduler_updating:
                self.log.debug('waiting for scheduler to finish updating')
                await asyncio.sleep(0.5)
            if retcode == 0:
                self.current_status = 'completed'
            else:
                self.current_status = 'interrupted'
            self.log.debug('pointing {} was {}'.format(self.current_pointing['id'],
                                                       self.current_status,
                                                       ))

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
        if params.PILOT_TAKE_EXTRA_DARKS:
            darks['args'].append('-x')

        self.daytime_tasks = [darks]

        # open
        self.open_sunalt = -4

        # evening tasks: done after opening the dome, before observing starts
        target_n = int(Time.now().jd) % len(params.FLATS_TARGET_COUNTS)
        target_counts = params.FLATS_TARGET_COUNTS[target_n]
        flats_e = {'name': 'FLATS',
                   'sunalt': -4.5,
                   'late_sunalt': -7,
                   'script': 'takeFlats.py',
                   'args': ['EVE',
                            '-c', str(target_counts),
                            '-n', str(params.NUM_FLATS),
                            '-f', str(params.FLATS_FILTERS),
                            ],
                   }
        autofoc = {'name': 'FOC',
                   'sunalt': -11,
                   'late_sunalt': None,  # Always autofocus if opening late
                   'script': 'autoFocus.py',
                   'args': ['-n', '1',
                            '-t', '5',
                            ],
                   }
        if not params.AUTOFOCUS_SLACK_REPORTS:  # This is ugly, these should all be in a config file
            autofoc['args'].append('--no-report')
        focrun_e = {'name': 'FOCRUN',
                    'sunalt': -13,
                    'late_sunalt': -14,
                    'script': 'takeFocusRun.py',
                    'args': ['4',
                             '-r', '0.02',
                             '-n', '1',
                             '-t', '5',
                             '--zenith',
                             '--no-analysis',
                             '--no-confirm',
                             ],
                    }

        if not params.PILOT_TAKE_FOCRUNS:
            self.evening_tasks = [flats_e, autofoc]
        else:
            self.evening_tasks = [flats_e, autofoc, focrun_e]

        # observing
        if not params.PILOT_TAKE_FOCRUNS:
            self.obs_start_sunalt = -12
            self.obs_stop_sunalt = -12
        else:
            # Need extra time at start and end
            self.obs_start_sunalt = -15
            self.obs_stop_sunalt = -15

        # morning tasks: done after observing, before closing the dome
        focrun_m = {'name': 'FOCRUN',
                    'sunalt': -14,
                    'late_sunalt': -13,
                    'script': 'takeFocusRun.py',
                    'args': ['4',
                             '-r', '0.02',
                             '-n', '1',
                             '-t', '5',
                             '--zenith',
                             '--no-analysis',
                             '--no-confirm',
                             ],
                    }
        flats_m = {'name': 'FLATS',
                   'sunalt': -10,
                   'late_sunalt': -7.55,
                   'script': 'takeFlats.py',
                   'args': ['MORN',
                            '-c', str(target_counts),
                            '-n', str(params.NUM_FLATS),
                            '-f', str(params.FLATS_FILTERS),
                            ],
                   }

        if not params.PILOT_TAKE_FOCRUNS:
            self.morning_tasks = [flats_m]
        else:
            self.morning_tasks = [focrun_m, flats_m]

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
            # TODO: if we haven't reached the late sunalt we should retry if it fails, esp autofocus
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
        -------
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
            if (not rising and now > self.midnight) or (rising and now < self.midnight):
                too_late = True

            # check if we've missed the late sunalt
            if (not rising and sunalt_now < late_sunalt) or (rising and sunalt_now > late_sunalt):
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
            if (rising and sunalt_now > sunalt) or (not rising and sunalt_now < sunalt):
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
        if last_obs_sunalt > until_sunalt:
            self.log.warning('limiting last_obs_sunalt to {}'.format(until_sunalt))
            last_obs_sunalt = until_sunalt

        last_focrun_time = time.time()
        focrun_count = 0
        focrun_positions = [(70, 0), (70, 90), (70, 180), (70, 270), (89.9, 0)]

        request_pointing = True
        finishing = False

        self.log.info('observing')
        self.observing = True
        while self.observing:
            # Check if we're paused
            if self.paused:
                # We still want the scheduler report to happen, just don't request anything.
                request_pointing = False
            else:
                # When we unpause we need to start requesting again.
                request_pointing = True

            # Should we stop?
            now = Time.now()
            sunalt_now = get_sunalt(now)
            if now > self.midnight:
                if sunalt_now > last_obs_sunalt:
                    # At this point we stop asking for new pointings, but keep observing.
                    if not finishing:
                        self.log.info('sunalt={:.1f}, stopping scheduler checks'.format(sunalt_now))
                        finishing = True
                    request_pointing = False
                if sunalt_now > until_sunalt:
                    # We've reached the limit and we're still observing, so we need to abort
                    # any current observation.
                    # This should set current_status, and then we'll update the database and
                    # exit the loop below.
                    self.log.info('sunalt={:.1f}, finished observing'.format(sunalt_now))
                    await self.cancel_running_script('obs finished')
                    request_pointing = False
                    self.observing = False
            if self.shutdown_now:
                self.log.info('shutdown triggered, stopping observing')
                await self.cancel_running_script('shutdown')
                request_pointing = False
                self.observing = False

            # First, log what the current pointing is (if anything)
            if self.current_pointing is not None:
                running_time = time.time() - self.current_start_time
                self.log.debug('current pointing: {} ({}, {:.0f}s/~{:.0f}s)'.format(
                               self.current_pointing['id'],
                               self.current_status,
                               running_time,
                               self.current_pointing['obstime']),
                               )
                # Check if we have been running for way too long
                if (self.current_status == 'running' and
                        (running_time > 120) and  # Give a minimum time for short observations
                        (running_time > self.current_pointing['obstime'] * 5)):
                    # Either something odd is going on with the exposures, or the OBS task died
                    # and failed to change the status to interrupted.
                    self.log.warning('timeout exceeded, killing observation')
                    await self.cancel_running_script(why='observing timeout')
                    self.current_status = 'interrupted'  # Just to be sure
            elif request_pointing:
                # Don't spam None if we didn't want anything
                self.log.debug('current pointing: None')

            # Now update the database and get the latest pointing from the scheduler
            self.scheduler_updating = True
            attempts_remaining = 3
            while attempts_remaining:
                try:
                    if request_pointing:
                        self.log.debug('checking scheduler')
                    else:
                        self.log.debug('updating scheduler')
                    if self.current_pointing is not None:
                        current_pointing_id = self.current_pointing['id']
                    else:
                        current_pointing_id = None
                    dome_shielding = self.hardware['dome'].shielding_active

                    if params.SCHEDULER_CHECK_METHOD == 'pyro':
                        future_pointing = update_schedule_pyro(
                            current_pointing_id,
                            self.current_status,
                            dome_shielding,
                            request_pointing=request_pointing,
                            asynchronous=True,
                            force_update=self.current_status != 'running',
                        )
                        while not future_pointing.ready:
                            await asyncio.sleep(0.2)
                        new_pointing = future_pointing.value
                    elif params.SCHEDULER_CHECK_METHOD == 'server':
                        new_pointing = await update_schedule_server_async(
                            current_pointing_id,
                            self.current_status,
                            dome_shielding,
                            request_pointing=request_pointing,
                            force_update=self.current_status != 'running',
                        )
                    else:
                        msg = f'Unknown scheduler check method: {params.SCHEDULER_CHECK_METHOD}'
                        raise ValueError(msg)

                    if request_pointing:
                        self.log.debug('scheduler returns {}'.format(
                            new_pointing['id'] if new_pointing is not None else 'None'))
                    break

                except Exception as error:
                    self.log.warning('{} checking scheduler: {}'.format(
                        type(error).__name__, error))
                    self.log.debug('', exc_info=True)
                    attempts_remaining -= 1
                    if attempts_remaining > 0:
                        self.log.warning('Remaining tries: {}'.format(attempts_remaining))
                        await asyncio.sleep(0.5)
                    else:
                        self.log.error('Could not communicate with the scheduler, parking')
                        new_pointing = None

            # Now that we've updated the database we can clear the current Pointing
            if self.current_status in ['completed', 'interrupted']:
                self.current_pointing = None
                self.current_status = None
                if finishing:
                    # That was the last Pointing for the night, no reason to continue this loop
                    self.observing = False
                    break

            self.scheduler_updating = False

            if params.PILOT_TAKE_FOCRUNS:
                # NB we could use this same logic to periodically refocus, if necessary
                time_since_last_run = time.time() - last_focrun_time
                if (time_since_last_run > params.FOCRUN_PERIOD and
                        not (self.tasks_pending or self.running_script)):
                    self.log.debug('focus run timer: {:.2f}h'.format(time_since_last_run / 60 / 60))
                    self.log.info('taking focus run')
                    # loop through positions
                    position = focrun_positions[focrun_count]
                    execute_command(f'mnt slew_altaz {position[0]:d} {position[1]:d}')
                    # wait for mount to slew
                    while True:
                        await asyncio.sleep(5)
                        mount_status = self.hardware['mnt'].get_hardware_status()
                        self.log.debug('mount is {}'.format(mount_status))
                        if mount_status == 'tracking':
                            break
                    # wait for the script to finish, blocking the observing loop
                    focrun_args = ['4',
                                   '-r', '0.02',
                                   '-n', '1',
                                   '-t', '5',
                                   '--no-slew',
                                   '--no-analysis',
                                   '--no-confirm',
                                   ]
                    await self.start_script('FOCRUN-X', 'takeFocusRun.py', args=focrun_args)
                    # done
                    last_focrun_time = time.time()
                    focrun_count += 1

            # Exit the loop if we didn't request a pointing.
            # We still want the above scheduler communication to happen if we're paused.
            # If we were observing then pausing should have killed OBS, which will have flagged the
            # pointing as interrupted. So we need the database update to happen above.
            # Likewise if it's the end of the night we need to loop until observing=False.
            # But we can skip everything below.
            if request_pointing is False:
                msg = 'observing suspended'
                if self.paused:
                    msg += ' while paused'
                elif finishing:
                    msg += ', waiting for current obs to finish'
                self.log.info(msg)
                await asyncio.sleep(10)
                continue

            # See if a new pointing has arrived and react appropriately
            # There are 6 options (technically 5, bottom left & bottom right
            # are the same...):
            #               | |    NEW is    |    NEW is    |    NEW is    |
            #               | |   same as    | different to |     None     |
            #               | |   CURRENT    |   CURRENT    |              |
            #  -------------+-+--------------+--------------+--------------+
            #  -------------+-+--------------+--------------+--------------+
            #     CURRENT   | |   continue   | stop CURRENT | stop CURRENT |
            #   is not None | |   CURRENT    |     then     |     then     |
            #   (observing) | |              |   start NEW  |     park     |
            #  -------------+-+--------------+--------------+--------------+
            #     CURRENT   | |     stay     |    unpark    |     stay     |
            #     is None   | |    parked    |     then     |    parked    |
            #     (parked)  | |              |   start NEW  |              |
            #  -------------+-+--------------+--------------+--------------+

            if new_pointing is not None:
                # We have a new Pointing, although it might be what we're already observing
                if (self.current_pointing is None or
                        new_pointing['id'] != self.current_pointing['id']):
                    self.log.info('got new pointing from scheduler: {}'.format(new_pointing['id']))

                    if self.current_pointing is not None:
                        # We're already observing something, so we have to cancel it.
                        # If the scheduler has returned this then it should have already marked the
                        # current pointing as interrupted, because even though we'll set the
                        # current_status here it will be overwritten immediately below.
                        await self.cancel_running_script(why='new pointing')
                    else:
                        # We weren't doing anything, either we just finished one or we were parked
                        if not self.mount_is_tracking:
                            send_slack_msg('Pilot is resuming observations')
                            await self.unpark_mount()

                    # Start the new pointing
                    self.log.debug('starting pointing {}'.format(new_pointing['id']))
                    args = [str(new_pointing['id'])]
                    if params.OBS_ADJUST_FOCUS:
                        args.append('--refocus')
                    elif params.OBS_FOCUS_TEMP_COMPENSATION:
                        args.append('--temp-compensation')
                    asyncio.ensure_future(self.start_script('OBS', 'observe.py', args=args))
                    self.current_start_time = time.time()
                    self.current_pointing = new_pointing
                    self.current_status = 'running'

            else:
                # Nothing to do!
                self.log.warning('nothing to observe!')
                if self.current_pointing is not None:
                    # Stop what we're doing, which will update the status and it should be sent to
                    # the scheduler on the next loop.
                    await self.cancel_running_script('obs parking')
                    self.park_mount()
                    # send_slack_msg('Pilot has nothing to observe!')

            await asyncio.sleep(5)

        # the loop has broken, so we've reached sunrise and finished the final Pointing
        self.observing = False
        self.log.info('observing completed')

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
                    await self.cancel_running_script('bad conditions')

                # always make sure we're closed and parked
                # (though the dome should already be closing itself)
                await self.close_dome()

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
                await self.cancel_running_script('hardware fault')
                if self.mount_is_tracking:
                    self.stop_mount()

            elif reason == 'manual':
                self.log.warning('Pausing (system in manual mode)')
                send_slack_msg('Pilot is pausing (system in manual mode)')

                # kill the current script, we usually do it manually anyway
                await self.cancel_running_script('system to manual mode')
                if self.mount_is_tracking:
                    self.stop_mount()

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
            stop_time = sunalt_time(self.close_sunalt * u.deg, eve=False)
        self.log.info('setting end of night for {}'.format(stop_time.iso))

        last_log = Time.now()
        log_period = 60
        if self.testing:
            log_period = 10
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
            if now - last_log > log_period * u.second:
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
        self.shutdown_now = True  # Force in case we stopped another way
        await self.shutdown()

        self.log.info('finished for tonight')

    # Startup and shutdown commands
    async def startup(self, send_report=True):
        """Start up the system.

        Runs the startup script, sets the startup_complete flag and sends the startup report
        """
        # run startup script
        self.log.debug('running startup script')
        await self.start_script('STARTUP', 'startup.py')

        # flag that startup has finished
        self.startup_complete = True

        # send the startup report
        if send_report:
            send_startup_report(msg='*Pilot reports startup complete*')

        self.log.debug('startup process complete')

    async def shutdown(self):
        """Shut down the system.

        - Cancel any running scripts (including updating the current pointing).
        - Run the shutdown script.
        - Ensure the dome is closed.
        - Quit.
        """
        self.log.info('shutdown process begun')

        # Cancel any currently running script.
        # Do this first so the scheduler marks any current pointing as interrupted,
        # before the night marshal `observe()` function is killed below.
        await self.cancel_running_script(why='shutdown')
        if self.observing:
            # Give time for a scheduler check to complete before shutting down,
            # so that it has the chance to mark the pointing as interrupted.
            await asyncio.sleep(10)

        # Now cancel all running tasks.
        # This is so check_flags doesn't initiate two shutdowns,
        # or we don't end up trying to restart if conditions clear,
        # or an "unfixable" hardware error gets fixed.
        self.log.info('cancelling running tasks')
        for task in self.running_tasks:
            task.cancel()

        # Run shutdown script
        self.log.info('running shutdown script')
        await self.start_script('SHUTDOWN', 'shutdown.py')

        # Flag that the shutdown script has been run, by un-flagging startup
        self.startup_complete = False

        # Finally, and most important: NEVER STOP WITHOUT CLOSING THE DOME!
        self.log.info('making sure dome is closed')
        await self.close_dome(confirm=True)

        self.log.info('shutdown process complete')

    async def emergency_shutdown(self, why):
        """Send a warning and then shut down."""
        if not self.shutdown_now:  # Don't trigger multiple times
            self.log.info('performing emergency shutdown: {}'.format(why))
            send_slack_msg('Pilot is performing an emergency shutdown: {}'.format(why))

            self.log.info('closing dome immediately')
            self.stop_mount()
            await self.close_dome()

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
        start_time = time.time()
        while True:
            dome_status = self.hardware['dome'].get_hardware_status()
            self.log.debug('dome is {}'.format(dome_status))
            if dome_status == 'full_open':
                break
            await asyncio.sleep(5)
            if time.time() - start_time > 300:
                self.log.error('dome opening timed out')
                asyncio.ensure_future(self.emergency_shutdown('Could not open the dome'))
        self.log.info('dome confirmed open')

        if self.startup_complete:
            # If we haven't started then we can't move the covers,
            # because the interfaces are disabled if the cameras are powered down.
            self.log.info('opening mirror covers')
            execute_command('ota open')
            self.hardware['ota'].mode = 'open'

            # wait for mirror covers to open
            start_time = time.time()
            while True:
                cover_status = self.hardware['ota'].get_hardware_status()
                self.log.debug('covers are {}'.format(cover_status))
                if cover_status == 'full_open':
                    break
                await asyncio.sleep(5)
                if time.time() - start_time > 300:
                    self.log.error('cover opening timed out')
                    asyncio.ensure_future(self.emergency_shutdown('Could not open mirror covers'))
            self.log.info('mirror covers confirmed open')

    async def close_dome(self, confirm=False):
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

        if confirm:
            # wait for dome to close
            start_time = time.time()
            while True:
                dome_status = self.hardware['dome'].get_hardware_status()
                self.log.debug('dome is {}'.format(dome_status))
                if dome_status in ['closed', 'in_lockdown']:
                    break
                await asyncio.sleep(5)
                if time.time() - start_time > 300:
                    self.log.error('dome closing timed out')
                    send_slack_msg('ERROR: Pilot could not close the dome!')
                    asyncio.ensure_future(self.emergency_shutdown('Could not close the dome'))

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
        if mount_status != 'tracking':
            # slew to above horizon, to stop errors
            execute_command('mnt slew_altaz 50 0')
            # wait for mount to slew
            start_time = time.time()
            while True:
                mount_status = self.hardware['mnt'].get_hardware_status()
                self.log.debug('mount is {}'.format(mount_status))
                if mount_status == 'tracking':
                    break
                await asyncio.sleep(5)
                if time.time() - start_time > 300:
                    self.log.error('mount unparking timed out')
                    asyncio.ensure_future(self.emergency_shutdown('Could not unpark mount'))
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
        asyncio.ensure_future(pilot.check_dome()),  # keep a close eye on dome
        asyncio.ensure_future(pilot.nightmarshal(restart, late)),  # run through scheduled tasks
    ])

    # Loop until the night countdown finishes (or the pilot exits early)
    if pilot.testing:
        # Force the countdown to finish in 5 minutes
        stop_time = Time.now() + 5 * u.minute
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
