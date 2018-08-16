"""Generic G-TeCS daemon classes & functions."""

import os
import time

import Pyro4

from . import errors
from . import logger
from . import misc
from . import params


class HardwareDaemon(object):
    """Base class for hardware daemons."""

    def __init__(self, daemon_id):
        if daemon_id not in params.DAEMONS:
            raise ValueError('daemon_id not defined in params')
        self.daemon_id = daemon_id
        self.params = params.DAEMONS[self.daemon_id].copy()

        self.running = True
        self.start_time = time.time()

        self.pinglife = self.params['PINGLIFE'] if 'PINGLIFE' in self.params else -1
        self.time_check = self.start_time  # should be updated within the main control loop

        self.info = None

        self.dependency_check_time = 0
        self.dependency_error = False
        self.bad_dependencies = []

        self.connection_error = False
        self.bad_hardware = []

        # set up logfile
        self.log = logger.get_logger(self.daemon_id,
                                     log_to_file=params.FILE_LOGGING,
                                     log_to_stdout=params.STDOUT_LOGGING)
        self.log.info('Daemon created')

    # Base daemon functions
    def _running_check(self):
        """Check if the daemon is running or not.

        Used for the Pyro loop condition, it needs a function so you can't just
        give it self.running.
        """
        return self.running

    def _run(self):
        """Start the daemon as a Pyro daemon, and run until shutdown."""
        host = self.params['HOST']
        port = self.params['PORT']

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
            pyro_daemon.requestLoop(loopCondition=self._running_check)

        # Loop has closed
        self.log.info('Daemon successfully shut down')
        time.sleep(1.)

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
            return 'dependency_error:{}'.format(','.join(self.bad_dependencies))

        elif self.connection_error:
            # Can not connect to the hardware.
            return 'hardware_error:{}'.format(','.join(self.bad_hardware))

        elif self.pinglife > 0 and abs(time.time() - self.time_check) > self.pinglife:
            # Control thread has hung
            return 'ping_error:{:.1f}s'.format(abs(time.time() - self.time_check))

        else:
            # No error
            return 'running'

    def check_dependencies(self):
        """Check if the daemon's dependencies are alive (if any).

        Save a list of any dependencies that aren't responding.
        """
        if self.params['DEPENDS'][0] == 'None':
            return []

        bad_dependencies = []
        for dependency_id in self.params['DEPENDS']:
            if not daemon_is_alive(dependency_id):
                bad_dependencies += [dependency_id]
        self.bad_dependencies = bad_dependencies

    def shutdown(self):
        """Shutdown the daemon."""
        self.log.info('Daemon shutting down')
        self.running = False


def daemon_is_running(daemon_id):
    """Check if a daemon is running."""
    return misc.get_pid(daemon_id) is not None


def daemon_proxy(daemon_id, timeout=params.PYRO_TIMEOUT):
    """Get a proxy connection to the given daemon."""
    address = params.DAEMONS[daemon_id]['ADDRESS']
    proxy = Pyro4.Proxy(address)
    proxy._pyroTimeout = timeout
    return proxy


def daemon_status(daemon_id):
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
    """Check if a daemon is running and reports no errors."""
    try:
        return bool(daemon_status(daemon_id) == 'running')
    except Exception:
        return False


def check_daemon(daemon_id):
    """Check the status of a daemon."""
    host = params.DAEMONS[daemon_id]['HOST']
    pid = misc.get_pid(daemon_id)
    runstr = 'Daemon running on {} (PID {})'.format(host, pid)

    status = daemon_status(daemon_id)

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


def daemon_function(daemon_id, function_name, args=None, timeout=0.):
    """Run a given function on a daemon."""
    check_daemon(daemon_id)  # Will raise an error if one occurs

    with daemon_proxy(daemon_id, timeout) as daemon:
        try:
            function = getattr(daemon, function_name)
        except AttributeError:
            raise NotImplementedError('Invalid function')
        if args is None:
            args = []
        return function(*args)


def daemon_info(daemon_id):
    """Get a daemon's info dict."""
    return daemon_function(daemon_id, 'get_info')


def start_daemon(daemon_id):
    """Start a daemon (unless it is already running)."""
    host = params.DAEMONS[daemon_id]['HOST']
    if daemon_is_running(daemon_id):
        return 'Daemon already running on {} (PID {})'.format(host, misc.get_pid(daemon_id))

    process_path = os.path.join(params.DAEMON_PATH, params.DAEMONS[daemon_id]['PROCESS'])
    process_options = {'in_background': True,
                       'host': host}
    if params.REDIRECT_STDOUT:
        fpipe = open(params.LOG_PATH + daemon_id + '-stdout.log', 'a')
        process_options.update({'stdout': fpipe, 'stderr': fpipe})

    misc.python_command(process_path, '', **process_options)

    start_time = time.time()
    while True:
        pid = misc.get_pid(daemon_id, host)
        if pid:
            check_daemon(daemon_id)  # Will raise status error if found
            return 'Daemon started on {} (PID {})'.format(host, pid)
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

    start_time = time.time()
    while True:
        if not daemon_is_running(daemon_id):
            return 'Daemon killed on {}'.format(host)
        if time.time() - start_time > 4:
            pid = misc.get_pid(daemon_id, host)
            errstr = 'Daemon still running on {} (PID {})'.format(host, pid)
            raise errors.DaemonConnectionError(errstr)
        time.sleep(0.5)


def restart_daemon(daemon_id, wait_time=2):
    """Shut down a daemon and then start it again after `wait_time` seconds."""
    reply = shutdown_daemon(daemon_id)
    print(reply)

    time.sleep(wait_time)

    reply = start_daemon(daemon_id)
    return reply
