"""Generic G-TeCS daemon classes & functions."""

import os
import time

import Pyro4

from . import errors
from . import logger
from . import misc
from . import params


class BaseDaemon(object):
    """Base class for TeCS daemons.

    Inherited by HardwareDaemon and InterfaceDaemon, use one of them.
    """

    def __init__(self, daemon_id):
        self.daemon_id = daemon_id
        self.running = True
        self.start_time = time.time()

        self.info = None

        # set up logfile
        self.log = logger.get_logger(self.daemon_id,
                                     log_to_file=params.FILE_LOGGING,
                                     log_to_stdout=params.STDOUT_LOGGING)
        self.log.info('Daemon created')

    # Common daemon functions
    def ping(self):
        """Ping the daemon."""
        raise NotImplementedError

    def prod(self):
        """Prod the daemon to make sure it closes."""
        return

    def status_function(self):
        """Check if the daemon is running or not."""
        return self.running

    def shutdown(self):
        """Shutdown the daemon."""
        self.log.info('Daemon shutting down')
        self.running = False

    def _run(self):
        """Start the daemon as a Pyro daemon, and run until shutdown."""
        host = params.DAEMONS[self.daemon_id]['HOST']
        port = params.DAEMONS[self.daemon_id]['PORT']

        # Check the Pyro address is available
        try:
            pyro_daemon = Pyro4.Daemon(host=host, port=port)
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
            pyro_daemon.requestLoop(loopCondition=self.status_function)

        # Loop has closed
        self.log.info('Daemon successfully shut down')
        time.sleep(1.)


class HardwareDaemon(BaseDaemon):
    """Generic hardware daemon class.

    Hardware daemons have always looping control threads.
    """

    def __init__(self, daemon_id):
        # initiate daemon
        BaseDaemon.__init__(self, daemon_id)

        self.dependency_error = 0
        self.dependency_check_time = 0

        self.time_check = time.time()

    # Common daemon functions
    def ping(self):
        """Ping the daemon."""
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS[self.daemon_id]['PINGLIFE']:
            error_str = 'Last control thread time check was {:.1f}s ago'.format(dt_control)
            raise errors.DaemonConnectionError(error_str)
        else:
            return 'ping'

    @property
    def dependencies_are_alive(self):
        """Check if the daemon's dependencies are alive (if any)."""
        return dependencies_are_alive(self.daemon_id)

    def check_dependency_error(self):
        """Check if the daemon is currently in an error state."""
        return self.dependency_error


class InterfaceDaemon(BaseDaemon):
    """Generic interface daemon class.

    Interface daemons do not have control threads like Hardware daemons,
    instead they just statically forward functions to the Pyro network.
    """

    def __init__(self, daemon_id):
        # initiate daemon
        BaseDaemon.__init__(self, daemon_id)

    # Common daemon functions
    def ping(self):
        """Ping the daemon."""
        return 'ping'

    def check_dependency_error(self):
        """Check if the daemon is currently in an error state."""
        return False


def daemon_is_running(daemon_id):
    """Check if a daemon is running."""
    host = params.DAEMONS[daemon_id]['HOST']
    return misc.get_pid(daemon_id, host) is not None


def daemon_is_alive(daemon_id):
    """Check if a daemon is alive and responding to pings."""
    # NOTE we can't use daemon_function here - recursion

    with daemon_proxy(daemon_id) as daemon:
        try:
            ping = daemon.ping()
            return bool(ping == 'ping')
        except Exception:
            return False


def dependencies_are_alive(daemon_id):
    """Check if a given daemon's dependencies are alive and responding to pings."""
    depends = params.DAEMONS[daemon_id]['DEPENDS']

    if depends[0] == 'None':
        return True

    for dependency_id in depends:
        if not daemon_is_alive(dependency_id):
            return False
    return True


def start_daemon(daemon_id):
    """Start a daemon (unless it is already running)."""
    process = params.DAEMONS[daemon_id]['PROCESS']
    host = params.DAEMONS[daemon_id]['HOST']
    depends = params.DAEMONS[daemon_id]['DEPENDS']

    if not dependencies_are_alive(daemon_id):
        failed = []
        for dependency in depends:
            if not daemon_is_running(dependency):
                failed += [dependency]
        if len(failed) > 0:
            error_str = 'Dependencies are not running ({}), abort start'.format(failed)
            raise errors.DaemonDependencyError(error_str)

    process_path = os.path.join(params.DAEMON_PATH, process)

    process_options = {'in_background': True,
                       'host': host}
    if params.REDIRECT_STDOUT:
        fpipe = open(params.LOG_PATH + daemon_id + '-stdout.log', 'a')
        process_options.update({'stdout': fpipe, 'stderr': fpipe})

    pid = misc.get_pid(daemon_id, host)
    if pid:
        return 'Daemon already running on {} (PID {})'.format(host, pid)

    misc.python_command(process_path, '', **process_options)

    start_time = time.time()
    while True:
        pid = misc.get_pid(daemon_id, host)
        if pid:
            return 'Daemon started on {} (PID {})'.format(host, pid)
        if time.time() - start_time > 4:
            raise errors.DaemonConnectionError('Daemon did not start, check logs')
        time.sleep(0.5)


def ping_daemon(daemon_id):
    """Ping a daemon."""
    host = params.DAEMONS[daemon_id]['HOST']

    if not daemon_is_running(daemon_id):
        raise errors.DaemonConnectionError('Daemon not running on {}'.format(host))
    if not daemon_is_alive(daemon_id):
        raise errors.DaemonConnectionError('Daemon running but not responding, check logs')

    pid = misc.get_pid(daemon_id, host)
    ping = daemon_function(daemon_id, 'ping')
    if ping == 'ping':
        return 'Ping received OK, daemon running on {} (PID {})'.format(host, pid)
    else:
        return ping + ', daemon running on {} (PID {})'.format(host, pid)


def shutdown_daemon(daemon_id):
    """Shut a daemon down nicely."""
    host = params.DAEMONS[daemon_id]['HOST']

    if not daemon_is_running(daemon_id):
        return 'Daemon not running on {}'.format(host)
    if not daemon_is_alive(daemon_id):
        raise errors.DaemonConnectionError('Daemon running but not responding, check logs')

    try:
        with daemon_proxy(daemon_id) as daemon:
            daemon.shutdown()
        # Have to request status again to close loop
        with daemon_proxy(daemon_id):
            daemon.prod()
    except Exception:
        pass

    start_time = time.time()
    while True:
        pid = misc.get_pid(daemon_id, host)
        if not pid:
            return 'Daemon shut down on {}'.format(host)
        if time.time() - start_time > 4:
            compstr = '{} (PID {})'.format(host, pid)
            raise errors.DaemonConnectionError('Daemon still running on {}'.format(compstr))
        time.sleep(0.5)


def kill_daemon(daemon_id):
    """Kill a daemon (should be used as a last resort)."""
    host = params.DAEMONS[daemon_id]['HOST']

    if not daemon_is_running(daemon_id):
        return 'Daemon not running on {}'.format(host)

    misc.kill_process(daemon_id, host)

    start_time = time.time()
    while True:
        pid = misc.get_pid(daemon_id, host)
        if not pid:
            return 'Daemon killed on {}'.format(host)
        if time.time() - start_time > 4:
            compstr = '{} (PID {})'.format(host, pid)
            raise errors.DaemonConnectionError('Daemon still running on {}'.format(compstr))
        time.sleep(0.5)


def restart_daemon(daemon_id, wait_time=2):
    """Shut down a daemon and then start it again after `wait_time` seconds."""
    reply = shutdown_daemon(daemon_id)
    print(reply)

    time.sleep(wait_time)

    reply = start_daemon(daemon_id)
    return reply


def daemon_proxy(daemon_id, timeout=params.PYRO_TIMEOUT):
    """Get a proxy connection to the given daemon."""
    address = params.DAEMONS[daemon_id]['ADDRESS']
    proxy = Pyro4.Proxy(address)
    proxy._pyroTimeout = timeout
    return proxy


def daemon_info(daemon_id):
    """Get a daemon's info dict."""
    return daemon_function(daemon_id, 'get_info')


def daemon_function(daemon_id, function_name, args=None, timeout=0.):
    """Run a given function on a daemon, after checking it's running."""
    if not daemon_is_running(daemon_id):
        raise errors.DaemonConnectionError('Daemon not running')
    if not daemon_is_alive(daemon_id):
        raise errors.DaemonConnectionError('Daemon running but not responding, check logs')
    if not dependencies_are_alive(daemon_id):
        raise errors.DaemonDependencyError('Required dependencies are not responding')

    with daemon_proxy(daemon_id, timeout) as daemon:
        try:
            function = getattr(daemon, function_name)
        except AttributeError:
            raise NotImplementedError('Invalid function')
        if args is None:
            args = []
        return function(*args)
