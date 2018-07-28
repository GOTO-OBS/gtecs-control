"""
Generic G-TeCS daemon classes & functions
"""

import os
import time
import Pyro4

from . import logger
from . import params
from . import misc
from . import errors


class BaseDaemon(object):
    """Base class for TeCS daemons.

    Inherited by HardwareDaemon and InterfaceDaemon, use one of them.
    """

    def __init__(self, daemon_ID):
        self.daemon_ID = daemon_ID
        self.running = True
        self.start_time = time.time()

        self.info = None

        # set up logfile
        self.logfile = logger.getLogger(self.daemon_ID,
                                        log_to_file=params.FILE_LOGGING,
                                        log_to_stdout=params.STDOUT_LOGGING)
        self.logfile.info('Daemon created')

    # Common daemon functions
    def ping(self):
        raise NotImplementedError

    def prod(self):
        return

    def status_function(self):
        return self.running

    def shutdown(self):
        self.logfile.info('Daemon shutting down')
        self.running = False

    def _run(self):
        host = params.DAEMONS[self.daemon_ID]['HOST']
        port = params.DAEMONS[self.daemon_ID]['PORT']

        # Check the Pyro address is available
        try:
            pyro_daemon = Pyro4.Daemon(host=host, port=port)
        except:
            raise
        else:
            pyro_daemon.close()

        # Start the daemon
        with Pyro4.Daemon(host, port) as pyro_daemon:
            uri = pyro_daemon.register(self, objectId=self.daemon_ID)
            Pyro4.config.COMMTIMEOUT = params.PYRO_TIMEOUT

            # Start request loop
            self.logfile.info('Daemon registered at {}'.format(uri))
            pyro_daemon.requestLoop(loopCondition=self.status_function)

        # Loop has closed
        self.logfile.info('Daemon successfully shut down')
        time.sleep(1.)


class HardwareDaemon(BaseDaemon):
    """Generic hardware daemon class.

    Hardware daemons have always looping control threads.
    """

    def __init__(self, daemon_ID):
        # initiate daemon
        BaseDaemon.__init__(self, daemon_ID)

        self.dependency_error = 0
        self.dependency_check_time = 0

        self.time_check = time.time()

    # Common daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS[self.daemon_ID]['PINGLIFE']:
            error_str = 'Last control thread time check was {:.1f}s ago'.format(dt_control)
            raise errors.DaemonConnectionError(error_str)
        else:
            return 'ping'

    @property
    def dependencies_are_alive(self):
        return dependencies_are_alive(self.daemon_ID)


class InterfaceDaemon(BaseDaemon):
    """Generic interface daemon class.

    Interface daemons do not have control threads like Hardware daemons,
    instead they just statically forward functions to the Pyro network.
    """

    def __init__(self, daemon_ID):
        # initiate daemon
        BaseDaemon.__init__(self, daemon_ID)

    # Common daemon functions
    def ping(self):
        return 'ping'


def daemon_is_running(daemon_ID):
    """Check if a daemon is running."""
    host = params.DAEMONS[daemon_ID]['HOST']
    if misc.check_pid(daemon_ID, host):
        return True
    else:
        return False


def daemon_is_alive(daemon_ID):
    """Check if a daemon is alive and responding to pings."""
    # NOTE we can't use daemon_function here - recursion

    with daemon_proxy(daemon_ID) as daemon:
        try:
            ping = daemon.ping()
            if ping == 'ping':
                return True
            else:
                return False
        except:
            return False


def dependencies_are_alive(daemon_ID):
    """Check if a given daemon's dependencies are alive and responding to pings."""
    depends = params.DAEMONS[daemon_ID]['DEPENDS']

    if depends[0] == 'None':
        return True

    for dependency_ID in depends:
        if not daemon_is_alive(dependency_ID):
            return False
    return True


def start_daemon(daemon_ID):
    """Start a daemon (unless it is already running)"""
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']
    depends = params.DAEMONS[daemon_ID]['DEPENDS']

    if not dependencies_are_alive(daemon_ID):
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
        fpipe = open(params.LOG_PATH + daemon_ID + '-stdout.log', 'a')
        process_options.update({'stdout': fpipe, 'stderr': fpipe})

    pid = misc.check_pid(daemon_ID, host)
    if pid:
        return 'Daemon already running on {} (PID {})'.format(host, pid)

    misc.python_command(process_path, '', **process_options)

    start_time = time.time()
    while True:
        pid = misc.check_pid(daemon_ID, host)
        if pid:
            return 'Daemon started on {} (PID {})'.format(host, pid)
        if time.time() - start_time > 4:
            raise errors.DaemonConnectionError('Daemon did not start on {}, check logs'.format(host))
        time.sleep(0.5)


def ping_daemon(daemon_ID):
    """Ping a daemon"""
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    if not daemon_is_running(daemon_ID):
        raise errors.DaemonConnectionError('Daemon not running on {}'.format(host))
    if not daemon_is_alive(daemon_ID):
        raise errors.DaemonConnectionError('Daemon running but not responding, check logs')

    pid = misc.check_pid(daemon_ID, host)
    ping = daemon_function(daemon_ID, 'ping')
    if ping == 'ping':
        return 'Ping received OK, daemon running on {} (PID {})'.format(host, pid)
    else:
        return ping + ', daemon running on {} (PID {})'.format(host, pid)


def shutdown_daemon(daemon_ID):
    """Shut a daemon down nicely"""
    host = params.DAEMONS[daemon_ID]['HOST']

    if not daemon_is_running(daemon_ID):
        return 'Daemon not running on {}'.format(host)
    if not daemon_is_alive(daemon_ID):
        raise errors.DaemonConnectionError('Daemon running but not responding, check logs')

    try:
        with daemon_proxy(daemon_ID) as daemon:
            daemon.shutdown()
        # Have to request status again to close loop
        with daemon_proxy(daemon_ID):
            daemon.prod()
    except:
        pass

    start_time = time.time()
    while True:
        pid = misc.check_pid(daemon_ID, host)
        if not pid:
            return 'Daemon shut down on {}'.format(host)
        if time.time() - start_time > 4:
            raise errors.DaemonConnectionError('Daemon still running on {} (PID {})'.format(host, pid))
        time.sleep(0.5)


def kill_daemon(daemon_ID):
    """Kill a daemon (should be used as a last resort)"""
    host    = params.DAEMONS[daemon_ID]['HOST']

    if not daemon_is_running(daemon_ID):
        return 'Daemon not running on {}'.format(host)

    misc.kill_process(daemon_ID, host)

    start_time = time.time()
    while True:
        pid = misc.check_pid(daemon_ID, host)
        if not pid:
            return 'Daemon killed on {}'.format(host)
        if time.time() - start_time > 4:
            raise errors.DaemonConnectionError('Daemon still running on {} (PID {})'.format(host, pid))
        time.sleep(0.5)


def restart_daemon(daemon_ID, wait_time=2):
    """Shut down a daemon and then start it again after `wait_time` seconds"""
    reply = shutdown_daemon(daemon_ID)
    print(reply)

    time.sleep(wait_time)

    reply = start_daemon(daemon_ID)
    return reply


def daemon_proxy(daemon_ID, timeout=params.PYRO_TIMEOUT):
    """Get a proxy connection to the given daemon."""
    address = params.DAEMONS[daemon_ID]['ADDRESS']
    proxy = Pyro4.Proxy(address)
    proxy._pyroTimeout = timeout
    return proxy


def daemon_info(daemon_ID):
    """Get a daemon's info dict"""
    return daemon_function(daemon_ID, 'get_info')


def daemon_function(daemon_ID, function_name, args=[], timeout=0.):
    if not daemon_is_running(daemon_ID):
        raise errors.DaemonConnectionError('Daemon not running')
    if not daemon_is_alive(daemon_ID):
        raise errors.DaemonConnectionError('Daemon running but not responding, check logs')
    if not dependencies_are_alive(daemon_ID):
        raise errors.DaemonDependencyError('Required dependencies are not responding')

    with daemon_proxy(daemon_ID, timeout) as daemon:
        try:
            function = getattr(daemon, function_name)
        except AttributeError:
            raise NotImplementedError('Invalid function')
        return function(*args)
