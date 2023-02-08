"""Generic G-TeCS daemon classes & functions."""

import importlib.resources as pkg_resources
import os
import subprocess
import time
from abc import ABC, abstractmethod

from gtecs.common import logging
from gtecs.common.system import get_pid, kill_process

from . import params

# Pyro configuration
if params.PYRO_LOGFILE != 'none':
    # Save Pyro logs to the given file (needs to be done *before* Pyro4 is imported)
    os.environ['PYRO_LOGFILE'] = params.PYRO_LOGFILE
    os.environ['PYRO_LOGLEVEL'] = 'DEBUG'
import Pyro4  # noqa: I100
Pyro4.config.SERIALIZER = 'pickle'  # IMPORTANT - Can serialize numpy arrays for images
Pyro4.config.SERIALIZERS_ACCEPTED.add('pickle')
Pyro4.config.REQUIRE_EXPOSE = False


class DaemonError(Exception):
    """Base class to be raised when a command to a daemon fails."""

    pass


class DaemonNotRunningError(DaemonError):
    """To be raised when a daemon is not running."""

    pass


class DaemonStillRunningError(DaemonError):
    """To be raised when a daemon is running and shouldn't be (e.g. we tried to kill it)."""

    pass


class DaemonStatusError(DaemonError):
    """To be raised when a daemon status is not normal."""

    pass


class DaemonDependencyError(DaemonError):
    """To be raised when a daemon is running but its dependencies are not responding."""

    pass


class HardwareError(DaemonError):
    """To be raised when a daemon hardware has some invalid status."""

    pass


class BaseDaemon(ABC):
    """Base class for hardware daemons.

    Daemons can be put into two catagories:
        - those dependent on hardware (e.g. dome, ut)
        - those dependent on other daemons (e.g. cam, exq)

    Each daemon should implement a master control loop, which includes a status check routine.

    Hardware-dependent daemons will attempt to connect to their hardware and enter a hardware
    error if they can not. They should implement a _connect() function to connect to each piece of
    hardware.

    Daemon-dependent daemons will attempt to connect to their dependencies and enter a dependency
    error if they can not. They should run the built-in _check_dependencies function to ensure the
    dependency daemons are still running.

    This is an abstract class and must be subtyped.
    Needed methods to implement:
        - _control_thread()
        - _get_info()
    """

    def __init__(self, daemon_id):
        self.daemon_id = daemon_id
        self.running = True
        self.start_time = time.time()

        self.info = None

        self.dependencies = set()
        self.dependency_error = False
        self.pending_bad_dependencies = dict()
        self.bad_dependencies = set()

        self.hardware_error = False
        self.bad_hardware = set()

        # set up logfile
        self.log = logging.get_logger(self.daemon_id)
        self.log.info('Daemon created')

    # Primary control thread
    @abstractmethod
    def _control_thread(self):
        """Primary control loop.

        This abstract method must be implemented by all daemons to add hardware-specific functions.
        """
        return

    # Base daemon functions
    def _running_function(self):
        """Check if the daemon is running or not.

        Used for the Pyro loop condition, it needs a function so you can't just
        give it self.running.
        """
        return self.running

    def _run(self, host, port, pinglife=10, timeout=5):
        """Start the daemon as a Pyro daemon, and run until shutdown."""
        self.pinglife = pinglife

        # Check the Pyro address is available
        try:
            pyro_daemon = Pyro4.Daemon(host, port)
        except Exception:
            raise
        else:
            pyro_daemon.close()

        # Start the daemon
        with Pyro4.Daemon(host, port) as pyro_daemon:
            uri = pyro_daemon.register(self, objectId=self.daemon_id)
            Pyro4.config.COMMTIMEOUT = timeout

            # Start request loop
            self.log.info('Daemon registered at {}'.format(uri))
            pyro_daemon.requestLoop(loopCondition=self._running_function)

        # Loop has closed
        self.log.info('Daemon successfully shut down')
        time.sleep(1.)

    def _check_dependencies(self, timeout=5):
        """Check if the daemon's dependencies are alive (if any).

        This function will set the dependency_error flag if any dependencies are not responding,
        and save them to bad_dependencies.
        Alternatively if all the dependencies are responding it will clear the error flag.
        """
        timestamp = time.time()
        for dependency_id in self.dependencies:
            try:
                with daemon_proxy(dependency_id) as daemon:
                    is_alive = bool(daemon.get_status() == 'running')
            except Exception:
                is_alive = False

            if not is_alive:
                if dependency_id not in self.bad_dependencies:
                    if dependency_id not in self.pending_bad_dependencies:
                        # Require two failed checks until we go into error state
                        self.log.warning('Dependency {} not responding?'.format(dependency_id))
                        self.pending_bad_dependencies[dependency_id] = timestamp
                    elif (timestamp - self.pending_bad_dependencies[dependency_id]) > timeout:
                        self.log.error('Dependency {} not responding'.format(dependency_id))
                        del self.pending_bad_dependencies[dependency_id]
                        self.bad_dependencies.add(dependency_id)
            else:
                if dependency_id in self.pending_bad_dependencies:
                    self.log.warning('Dependency {} responding!'.format(dependency_id))
                    del self.pending_bad_dependencies[dependency_id]
                if dependency_id in self.bad_dependencies:
                    self.log.info('Dependency {} responding'.format(dependency_id))
                    self.bad_dependencies.remove(dependency_id)

        if len(self.bad_dependencies) > 0 and not self.dependency_error:
            self.log.warning('Dependency error detected')
            self.dependency_error = True
        elif len(self.bad_dependencies) == 0 and self.dependency_error:
            self.log.warning('Dependency error cleared')
            self.dependency_error = False

        # Finally check if we need to report an error
        self._check_errors()

    def _check_errors(self):
        """Check for any errors."""
        # Hardware errors
        if len(self.bad_hardware) > 0 and not self.hardware_error:
            self.log.warning('Hardware error detected')
            self.hardware_error = True
        elif len(self.bad_hardware) == 0 and self.hardware_error:
            self.log.warning('Hardware error cleared')
            self.hardware_error = False

        # Dependency errors
        if len(self.bad_dependencies) > 0 and not self.dependency_error:
            self.log.warning('Dependency error detected')
            self.dependency_error = True
        elif len(self.bad_dependencies) == 0 and self.dependency_error:
            self.log.warning('Dependency error cleared')
            self.dependency_error = False

    @abstractmethod
    def _get_info(self):
        """Get the latest status info from the hardware.

        This abstract method must be implemented by all daemons to add hardware-specific checks.
        """
        return

    def _get_client_ip(self):
        """Get the current Pyro client IP."""
        return Pyro4.current_context.client.sock.getpeername()[0]

    # Common daemon functions
    def prod(self):
        """Prod the daemon to make sure it closes."""
        return

    def get_status(self):
        """Check the current state of the daemon."""
        if not self.running:
            # The daemon has been shutdown but is still here somehow?
            return 'running_error'

        elif self.dependency_error:
            # Any dependencies (if the daemon has them) aren't responding.
            return 'dependency_error:{}'.format(','.join(sorted(self.bad_dependencies)))

        elif self.hardware_error:
            # Can not connect to the hardware.
            return 'hardware_error:{}'.format(','.join(sorted(self.bad_hardware)))

        elif self.pinglife > 0 and abs(time.time() - self.loop_time) > self.pinglife:
            # Control thread has hung
            return 'ping_error:{:.1f}s'.format(abs(time.time() - self.loop_time))

        else:
            # No error
            return 'running'

    def wait_for_info(self):
        """Force an info check and wait until the dictionary has been updated."""
        self.force_check_flag = True
        while self.info['time'] < self.loop_time:
            time.sleep(0.01)
        return

    def get_info(self, force_update=True):
        """Return hardware information."""
        if force_update:
            self.wait_for_info()
        return self.info

    def shutdown(self):
        """Shutdown the daemon."""
        self.log.info('Daemon shutting down')
        self.running = False


def get_daemon_host(daemon_id):
    """Get the host (and port) for the given daemon."""
    if daemon_id in params.DAEMONS:
        host = params.DAEMONS[daemon_id]['HOST']
        port = params.DAEMONS[daemon_id]['PORT']
    elif daemon_id in params.INTERFACES:
        host = params.INTERFACES[daemon_id]['HOST']
        port = params.INTERFACES[daemon_id]['PORT']
    else:
        raise ValueError('Daemon "{}" not found'.format(daemon_id))

    return host, port


def daemon_proxy(daemon_id=None, host=None, port=None, timeout=params.PYRO_TIMEOUT):
    """Get a proxy connection to the given daemon."""
    try:
        host, port = get_daemon_host(daemon_id)
    except ValueError:
        if host is None or port is None:
            raise ValueError('Daemon "{}" not found, no host/port given'.format(daemon_id))
    address = 'PYRO:{}@{}:{}'.format(daemon_id, host, port)
    proxy = Pyro4.Proxy(address)
    proxy._pyroTimeout = timeout
    return proxy


def check_daemon(daemon_id):
    """Check the status of a daemon."""
    host, port = get_daemon_host(daemon_id)
    pid = get_pid(daemon_id, host)
    if pid is None:
        raise DaemonNotRunningError(f'Daemon {daemon_id} not running on {host}:{port}')

    with daemon_proxy(daemon_id) as daemon:
        try:
            status = daemon.get_status()
        except Exception:
            status = 'status_error'

    if status == 'running':
        return pid

    error_str = f'Daemon {daemon_id} running on {host}:{port} (PID {pid})'

    if status.split(':')[0] == 'dependency_error':
        bad_dependencies = status.split(':')[1]
        error_str += f' but cannot connect to dependencies: {bad_dependencies}.'
        raise DaemonDependencyError(error_str)
    if status.split(':')[0] == 'hardware_error':
        bad_hardware = status.split(':')[1]
        error_str += f' but cannot connect to hardware: {bad_hardware}.'
        raise HardwareError(error_str)

    if status == 'status_error':
        error_str += ' but cannot read status.'
    elif status == 'running_error':
        error_str += ' but is not active. (?)'
    elif status.split(':')[0] == 'ping_error':
        ping_time = status.split(':')[1]
        error_str += f' but last ping was {ping_time:.1f}s ago.'
    else:
        error_str += f' but reports unknown status: {status}.'
    raise DaemonStatusError(error_str)


def start_daemon(daemon_id, timeout=4):
    """Start a daemon (unless it is already running)."""
    host, port = get_daemon_host(daemon_id)
    try:
        pid = check_daemon(daemon_id)
        # If it's already running with no errors then that's fine
        return pid
    except DaemonNotRunningError:
        # That's what we want!
        pass
    except Exception:
        raise

    if daemon_id in params.DAEMONS:
        ut = None
        script = f'{daemon_id}_daemon.py'
    elif daemon_id.startswith('cam'):
        ut = int(daemon_id.split('cam')[1])
        script = 'cam_interface.py'
    elif daemon_id.startswith('foc'):
        ut = int(daemon_id.split('foc')[1])
        script = 'foc_interface.py'
    elif daemon_id.startswith('filt'):
        ut = int(daemon_id.split('filt')[1])
        script = 'filt_interface.py'
    else:
        raise ValueError(f'Daemon {daemon_id} not found')

    with pkg_resources.path('gtecs.control._daemon_scripts', script) as path:
        command_string = f'{params.PYTHON_EXE} {str(path)}'
    if ut is not None:
        command_string += f' {ut}'
    if host not in ['127.0.0.1', params.LOCAL_HOST]:
        command_string = f"ssh {host} '{command_string}'"  # TODO: use fabric?
    if params.COMMAND_DEBUG:
        print(command_string)

    # Also redirect the process stdout to a log file
    # The logger stdout will be included in the log,
    # but this is handy in case of errors outside of the logger.
    # Also for remote processes this file will be stored locally.
    log_file = daemon_id + '-stdout.log'
    log_path = logging.get_log_path() / log_file
    pipe = open(log_path, 'a')

    subprocess.Popen(command_string, shell=True, stdout=pipe, stderr=pipe)

    time.sleep(1)
    start_time = time.time()
    while True:
        try:
            pid = check_daemon(daemon_id)  # Will raise status error if found
            return pid
        except DaemonError:
            if time.time() - start_time > timeout:
                raise DaemonNotRunningError(f'Daemon {daemon_id} failed to start on {host}:{port}')
            else:
                time.sleep(0.5)
        except Exception:
            raise


def shutdown_daemon(daemon_id, kill=False, timeout=4):
    """Shut a daemon down nicely."""
    host, port = get_daemon_host(daemon_id)
    try:
        pid = check_daemon(daemon_id)
    except DaemonNotRunningError:
        # Great, saves us the trouble of shutting it down
        return
    except (DaemonStatusError, DaemonDependencyError):
        pass  # we don't care if there's an error, we're shutting down anyway
    except Exception:
        raise

    try:
        if not kill:
            with daemon_proxy(daemon_id) as daemon:
                daemon.shutdown()
            # Have to connect again to close loop for some reason
            with daemon_proxy(daemon_id):
                daemon.prod()
        else:
            kill_process(daemon_id, host, verbose=params.COMMAND_DEBUG)
    except Exception:
        pass

    time.sleep(1)
    start_time = time.time()
    while True:
        try:
            pid = check_daemon(daemon_id)
            # If it hasn't raised an error that means it's running fine. That's bad!
            err_str = f'Daemon {daemon_id} still running on {host}:{port} (PID {pid})'
            raise DaemonStillRunningError(err_str)
        except DaemonNotRunningError:
            # That's what we want!
            return
        except (DaemonStillRunningError, DaemonStatusError, DaemonDependencyError):
            # It's still running, and may or may not have an error
            if time.time() - start_time > timeout:
                raise
            else:
                time.sleep(0.5)
        except Exception:
            raise


def restart_daemon(daemon_id, wait_time=1, timeout=4):
    """Shut down a daemon and then start it again after `wait_time` seconds."""
    shutdown_daemon(daemon_id, timeout=timeout)
    time.sleep(wait_time)
    pid = start_daemon(daemon_id, timeout=timeout)
    return pid
