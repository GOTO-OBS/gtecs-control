"""
Generic G-TeCS daemon classes & functions
"""

import os
import time
import Pyro4

from . import logger
from . import params
from . import misc


class BaseDaemon(object):
    """Base class for TeCS daemons.

    Inherited by HardwareDaemon and InterfaceDaemon, use one of them.
    """

    def __init__(self, daemon_ID):
        self.daemon_ID = daemon_ID
        self.running = True
        self.start_time = time.time()

        # set up logfile
        self.logfile = logger.getLogger(self.daemon_ID,
                                        file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
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


class HardwareDaemon(BaseDaemon):
    """Generic hardware daemon class.

    Hardware daemons have always looping control threads.
    """

    def __init__(self, daemon_ID):
        # initiate daemon
        BaseDaemon.__init__(self, daemon_ID)

        self.time_check = time.time()

    # Common daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS[self.daemon_ID]['PINGLIFE']:
            error_str = 'Last control thread time check was {:.1f}s ago'.format(dt_control)
            raise misc.DaemonConnectionError(error_str)
        else:
            return 'ping'


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


def start_daemon(daemon_ID):
    """Start a daemon (unless it is already running)"""
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']
    depends = params.DAEMONS[daemon_ID]['DEPENDS']

    if depends[0] != 'None':
        failed = []
        for dependency in depends:
            if not misc.daemon_is_alive(dependency):
                failed += [dependency]
        if len(failed) > 0:
            error_str = 'Dependencies are not running ({}), abort start'.format(failed)
            raise misc.DaemonDependencyError(error_str)

    process_path = os.path.join(params.DAEMON_PATH, process)

    process_options = {'in_background': True,
                       'host': host}
    if params.REDIRECT_STDOUT:
        fpipe = open(params.LOG_PATH + daemon_ID + '-stdout.log', 'a')
        process_options.update({'stdout': fpipe, 'stderr': fpipe})

    process_ID = misc.get_process_ID(process, host)
    if len(process_ID) == 0:
        # Run script
        misc.python_command(process_path, '', **process_options)

        # See if it started
        time.sleep(1)
        process_ID_n = misc.get_process_ID(process, host)
        if len(process_ID_n) == 1:
            return 'Daemon started on {} (PID {})'.format(host, process_ID_n[0])
        elif len(process_ID_n) > 1:
            raise misc.MultipleDaemonError('Multiple daemons running on {} (PID {})'.format(host, process_ID_n))
        else:
            raise misc.DaemonConnectionError('Daemon did not start on {}, check logs'.format(host))
    elif len(process_ID) == 1:
        raise misc.DaemonConnectionError('Daemon already running on {} (PID {})'.format(host, process_ID[0]))
    else:
        raise misc.MultipleDaemonError('Multiple daemons already running on {} (PID {})'.format(host, process_ID))


def ping_daemon(daemon_ID):
    """Ping a daemon"""
    address = params.DAEMONS[daemon_ID]['ADDRESS']
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    process_ID = misc.get_process_ID(process, host)
    if len(process_ID) == 1:
        daemon = Pyro4.Proxy(address)
        daemon._pyroTimeout = params.PROXY_TIMEOUT
        try:
            ping = daemon.ping()
            if ping == 'ping':
                return 'Ping received OK, daemon running on {} (PID {})'.format(host, process_ID[0])
            else:
                return ping + ', daemon running on {} (PID {})'.format(host, process_ID[0])
        except misc.DaemonConnectionError:
            raise
        except:
            raise misc.DaemonConnectionError('No response, daemon running on {} (PID {})'.format(host, process_ID[0]))
    elif len(process_ID) == 0:
        raise misc.DaemonConnectionError('Daemon not running on {}'.format(host))
    else:
        raise misc.MultipleDaemonError('Multiple daemons running on {} (PID {})'.format(host, process_ID))


def shutdown_daemon(daemon_ID):
    """Shut a daemon down nicely"""
    address = params.DAEMONS[daemon_ID]['ADDRESS']
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    process_ID = misc.get_process_ID(process, host)
    if len(process_ID) == 1:
        daemon = Pyro4.Proxy(address)
        daemon._pyroTimeout = params.PROXY_TIMEOUT
        try:
            daemon.shutdown()
            # Have to request status again to close loop
            daemon = Pyro4.Proxy(address)
            daemon._pyroTimeout = params.PROXY_TIMEOUT
            daemon.prod()
            daemon._pyroRelease()

            # See if it shut down
            time.sleep(2)
            process_ID_n = misc.get_process_ID(process, host)
            if len(process_ID_n) == 0:
                return 'Daemon shut down on {}'.format(host)
            elif len(process_ID_n) == 1:
                raise misc.DaemonConnectionError('Daemon still running on {} (PID {})'.format(host, process_ID_n[0]))
            else:
                raise misc.MultipleDaemonError('Multiple daemons still running on {} (PID {})'.format(host, process_ID_n))
        except:
            raise misc.DaemonConnectionError('No response, daemon still running on {} (PID {})'.format(host, process_ID[0]))
    elif len(process_ID) == 0:
        raise misc.DaemonConnectionError('Daemon not running on {}'.format(host))
    else:
        raise misc.MultipleDaemonError('Multiple daemons running on {} (PID {})'.format(host, process_ID))


def kill_daemon(daemon_ID):
    """Kill a daemon (should be used as a last resort)"""
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    process_ID = misc.get_process_ID(process, host)
    if len(process_ID) >= 1:
        misc.kill_processes(process, host)

        # See if it is actually dead
        process_ID_n = misc.get_process_ID(process, host)
        if len(process_ID_n) == 0:
            return 'Daemon killed on {}'.format(host)
        elif len(process_ID_n) == 1:
            raise misc.DaemonConnectionError('Daemon still running on {} (PID {})'.format(host, process_ID_n[0]))
        else:
            raise misc.MultipleDaemonError('Multiple daemons still running on {} (PID {})'.format(host, process_ID_n))
    else:
        raise misc.DaemonConnectionError('Daemon not running on {}'.format(host))


def restart_daemon(daemon_ID, wait_time=2):
    """Shut down a daemon and then start it again after `wait_time` seconds"""
    reply = shutdown_daemon(daemon_ID)
    print(reply)

    time.sleep(wait_time)

    reply = start_daemon(daemon_ID)
    print(reply)


def daemon_function(daemon_ID, function_name, args=[], timeout=0.):
    if not misc.daemon_is_running(daemon_ID):
        raise misc.DaemonConnectionError('Daemon not running')
    elif not misc.daemon_is_alive(daemon_ID):
        raise misc.DaemonConnectionError('Daemon running but not responding, check logs')
    elif not misc.dependencies_are_alive(daemon_ID):
        raise misc.DaemonDependencyError('Required dependencies are not responding')
    else:
        address = params.DAEMONS[daemon_ID]['ADDRESS']
        if not timeout:
            timeout = params.PROXY_TIMEOUT
        with Pyro4.Proxy(address) as proxy:
            proxy._pyroTimeout = timeout
            try:
                function = getattr(proxy, function_name)
            except AttributeError:
                raise NotImplementedError('Invalid function')
            return function(*args)
