"""Generic G-TeCS daemon classes & functions."""

import os
import time
from abc import ABC, abstractmethod

from gtecs.common.logging import get_logger

import Pyro4

from . import errors
from . import misc
from . import params


# Pyro configuration
Pyro4.config.SERIALIZER = 'pickle'  # IMPORTANT - Can serialize numpy arrays for images
Pyro4.config.SERIALIZERS_ACCEPTED.add('pickle')
Pyro4.config.REQUIRE_EXPOSE = False


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
        if daemon_id not in params.DAEMONS:
            raise ValueError('daemon_id not defined in params')
        self.daemon_id = daemon_id
        self.params = params.DAEMONS[self.daemon_id].copy()

        self.running = True
        self.start_time = time.time()

        self.pinglife = self.params['PINGLIFE'] if 'PINGLIFE' in self.params else -1
        self.loop_time = self.start_time  # should be updated within the main control loop

        self.info = None

        self.check_period = params.DAEMON_CHECK_PERIOD
        self.check_time = 0
        self.force_check_flag = True

        self.dependencies = set()
        self.dependency_error = False
        self.bad_dependencies = set()

        self.hardware_error = False
        self.bad_hardware = set()

        # set up logfile
        self.log = get_logger(self.daemon_id, params.LOG_PATH,
                              log_to_file=params.FILE_LOGGING,
                              log_to_stdout=params.STDOUT_LOGGING)
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

    def _run(self):
        """Start the daemon as a Pyro daemon, and run until shutdown."""
        host = self.params['HOST']
        port = int(self.params['PORT'])

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
            Pyro4.config.COMMTIMEOUT = params.PYRO_TIMEOUT

            # Start request loop
            self.log.info('Daemon registered at {}'.format(uri))
            pyro_daemon.requestLoop(loopCondition=self._running_function)

        # Loop has closed
        self.log.info('Daemon successfully shut down')
        time.sleep(1.)

    def _check_dependencies(self):
        """Check if the daemon's dependencies are alive (if any).

        This function will set the dependency_error flag if any dependencies are not responding,
        and save them to bad_dependencies.
        Alternatively if all the dependencies are responding it will clear the error flag.
        """
        for dependency_id in self.dependencies:
            if not daemon_is_alive(dependency_id):
                if dependency_id not in self.bad_dependencies:
                    self.log.error('Dependency {} not responding'.format(dependency_id))
                    self.bad_dependencies.add(dependency_id)
            else:
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


def daemon_is_running(daemon_id):
    """Check if a daemon is running."""
    return misc.get_pid(daemon_id) is not None


def daemon_proxy(daemon_id=None, host=None, port=None, timeout=params.PYRO_TIMEOUT):
    """Get a proxy connection to the given daemon."""
    if daemon_id in params.DAEMONS:
        host = params.DAEMONS[daemon_id]['HOST']
        port = params.DAEMONS[daemon_id]['PORT']
    if host is None or port is None:
        raise ValueError('Daemon "{}" not found, no host/port given'.format(daemon_id))
    address = 'PYRO:{}@{}:{}'.format(daemon_id, host, port)
    proxy = Pyro4.Proxy(address)
    proxy._pyroTimeout = timeout
    return proxy


def get_daemon_status(daemon_id):
    """Get a daemon's current status."""
    if not daemon_is_running(daemon_id):
        host = params.DAEMONS[daemon_id]['HOST']
        raise errors.DaemonConnectionError('Daemon not running on {}'.format(host))

    # Can't use daemon_function due to recursion
    with daemon_proxy(daemon_id) as daemon:
        try:
            return daemon.get_status()
        except Exception:
            return 'status_error'


def daemon_is_alive(daemon_id):
    """Quickly check if a daemon is running and reports no errors."""
    try:
        with daemon_proxy(daemon_id) as daemon:
            return bool(daemon.get_status() == 'running')
    except Exception:
        return False


def check_daemon(daemon_id):
    """Check the status of a daemon."""
    host = params.DAEMONS[daemon_id]['HOST']
    pid = misc.get_pid(daemon_id)
    runstr = 'Daemon running on {} (PID {})'.format(host, pid)

    status = get_daemon_status(daemon_id)

    if status == 'running':
        return runstr

    elif status == 'status_error':
        errstr = runstr + ' but cannot read status.'
    elif status == 'running_error':
        errstr = runstr + ' but is not active. (?)'
    elif status.split(':')[0] == 'dependency_error':
        bad_dependencies = status.split(':')[1]
        errstr = runstr + ' but cannot connect to dependencies: {}.'.format(bad_dependencies)
    elif status.split(':')[0] == 'hardware_error':
        bad_hardware = status.split(':')[1]
        errstr = runstr + ' but cannot connect to hardware: {}.'.format(bad_hardware)
    elif status.split(':')[0] == 'ping_error':
        pingtime = status.split(':')[1]
        errstr = runstr + ' but last ping was {} ago.'.format(pingtime)
    else:
        errstr = runstr + ' but reports unknown status: {}.'.format(status)

    raise errors.DaemonStatusError(errstr)


def daemon_function(daemon_id, function_name, args=None, timeout=30):
    """Run a given function on a daemon."""
    check_daemon(daemon_id)  # Will raise an error if one occurs

    with daemon_proxy(daemon_id, timeout=timeout) as daemon:
        try:
            function = getattr(daemon, function_name)
        except AttributeError:
            raise NotImplementedError('Invalid function')
        if args is None:
            args = []
        return function(*args)


def daemon_info(daemon_id, force_update=True, timeout=30):
    """Get a daemon's info dict."""
    return daemon_function(daemon_id, 'get_info', args=[force_update], timeout=timeout)


def start_daemon(daemon_id, args=None):
    """Start a daemon (unless it is already running)."""
    host = params.DAEMONS[daemon_id]['HOST']
    if daemon_is_running(daemon_id):
        try:
            check_daemon(daemon_id)  # Will raise status error if found
            return 'Daemon already running on {} (PID {})'.format(host, misc.get_pid(daemon_id))
        except Exception:
            print('Daemon already running but reports error:')
            raise

    process_path = params.DAEMONS[daemon_id]['PROCESS_PATH']
    process_options = {'in_background': True,
                       'host': host}
    if params.REDIRECT_STDOUT:
        logfile = daemon_id + '-stdout.log'
        pipe = open(os.path.join(params.LOG_PATH, logfile), 'a')
        process_options.update({'stdout': pipe, 'stderr': pipe})

    if args is not None:
        args = ' '.join([str(arg) for arg in args])
    else:
        args = ''

    misc.python_command(process_path, args, **process_options)

    time.sleep(1)
    start_time = time.time()
    while True:
        pid = misc.get_pid(daemon_id, host)
        if pid:
            try:
                check_daemon(daemon_id)  # Will raise status error if found
                return 'Daemon started on {} (PID {})'.format(host, pid)
            except Exception:
                print('Daemon started but reports error:')
                raise
        if time.time() - start_time > 4:
            raise errors.DaemonConnectionError('Daemon did not start, check logs')
        time.sleep(0.5)


def shutdown_daemon(daemon_id):
    """Shut a daemon down nicely."""
    host = params.DAEMONS[daemon_id]['HOST']
    if not daemon_is_running(daemon_id):
        return 'Daemon not running on {}'.format(host)

    try:
        with daemon_proxy(daemon_id) as daemon:
            daemon.shutdown()
        # Have to connect again to close loop for some reason
        with daemon_proxy(daemon_id):
            daemon.prod()
    except Exception:
        pass

    time.sleep(1)
    start_time = time.time()
    while True:
        if not daemon_is_running(daemon_id):
            return 'Daemon shut down on {}'.format(host)
        if time.time() - start_time > 4:
            pid = misc.get_pid(daemon_id)
            errstr = 'Daemon still running on {} (PID {})'.format(host, pid)
            raise errors.DaemonConnectionError(errstr)
        time.sleep(0.5)


def kill_daemon(daemon_id):
    """Kill a daemon (should be used as a last resort)."""
    host = params.DAEMONS[daemon_id]['HOST']
    if not daemon_is_running(daemon_id):
        return 'Daemon not running on {}'.format(host)

    try:
        misc.kill_process(daemon_id, host)
    except Exception:
        pass

    time.sleep(1)
    start_time = time.time()
    while True:
        if not daemon_is_running(daemon_id):
            return 'Daemon killed on {}'.format(host)
        if time.time() - start_time > 4:
            pid = misc.get_pid(daemon_id, host)
            errstr = 'Daemon still running on {} (PID {})'.format(host, pid)
            raise errors.DaemonConnectionError(errstr)
        time.sleep(0.5)


def restart_daemon(daemon_id, args=None, wait_time=1):
    """Shut down a daemon and then start it again after `wait_time` seconds."""
    reply = shutdown_daemon(daemon_id)
    print(reply)

    time.sleep(wait_time)

    reply = start_daemon(daemon_id, args=args)
    return reply
