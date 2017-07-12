#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                              daemons.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#      G-TeCS module containing generic daemon classes & functions     #
#                     Martin Dyer, Sheffield, 2017                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import time
import os
import Pyro4

# TeCS modules
from . import logger
from . import params
from . import misc

########################################################################
# Super classes

class HardwareDaemon(object):
    """
    Generic hardware daemon class
    """

    def __init__(self, daemon_ID):
        self.daemon_ID = daemon_ID
        self.running = True
        self.start_time = time.time()
        self.time_check = time.time()

        # set up logfile
        self.logfile = logger.getLogger(self.daemon_ID,
                                        file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
        self.logfile.info('Daemon created')

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Common daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS[self.daemon_ID]['PINGLIFE']:
            return 'ERROR: Last control thread time check was '\
                   '%.1f seconds ago' %dt_control
        else:
            return 'ping'

    def prod(self):
        return

    def status_function(self):
        return self.running

    def shutdown(self):
        self.logfile.info('Daemon shutting down')
        self.running = False


class InterfaceDaemon(object):
    """
    Generic interface daemon class
    """

    def __init__(self, interface_ID):
        self.interface_ID = interface_ID
        self.running = True
        self.start_time = time.time()

        # set up logfile
        self.logfile = logger.getLogger(self.interface_ID,
                                        file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
        self.logfile.info('Daemon created')

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Common daemon functions
    def ping(self):
        return 'ping'

    def prod(self):
        return

    def status_function(self):
        return self.running

    def shutdown(self):
        self.logfile.info('Daemon shutting down')
        self.running = False


########################################################################
# Core Daemon control functions
def start_daemon(daemon_ID):
    '''Start a daemon (unless it is already running)'''
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']
    pyroid  = params.DAEMONS[daemon_ID]['PYROID']
    depends = params.DAEMONS[daemon_ID]['DEPENDS']
    if params.REDIRECT_STDOUT:
        output = params.LOG_PATH + pyroid + '-stdout.log'
    else:
        output = '/dev/stdout'

    if depends[0] != 'None':
        fail = 0
        for dependency in depends:
            if not misc.daemon_is_alive(dependency):
                print('ERROR: Dependency "{}" is not running, abort start'.format(dependency))
                fail += 1
        if fail > 0:
            return

    process_path = os.path.join(params.DAEMON_PATH, process)
    out_cmd = ' '.join(('>', output, '2>&1 &'))

    process_ID = misc.get_process_ID(process, host)
    if len(process_ID) == 0:
        # Run script
        misc.python_command(process_path, out_cmd, host)

        # See if it started
        process_ID_n = misc.get_process_ID(process, host)
        if len(process_ID_n) == 1:
            print('Daemon started on {} (PID {})'.format(host, process_ID_n[0]))
        elif len(process_ID_n) > 1:
            print('ERROR: Multiple daemons running on {} (PID {})'.format(host, process_ID_n))
        else:
            print('ERROR: Daemon did not start on {}, check logs'.format(host))
    elif len(process_ID) == 1:
        print('ERROR: Daemon already running on {} (PID {})'.format(host, process_ID[0]))
    else:
        print('ERROR: Multiple daemons already running on {} (PID {})'.format(host, process_ID))


def ping_daemon(daemon_ID):
    '''Ping a daemon'''
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
                print('Ping received OK, daemon running on {} (PID {})'.format(host, process_ID[0]))
            else:
                print(ping + ', daemon running on {} (PID {})'.format(host, process_ID[0]))
        except:
            print('ERROR: No response, daemon running on {} (PID {})'.format(host, process_ID[0]))
    elif len(process_ID) == 0:
        print('ERROR: No response, daemon not running on {}'.format(host))
    else:
        print('ERROR: Multiple daemons running on {} (PID {})'.format(host, process_ID))


def shutdown_daemon(daemon_ID):
    '''Shut a daemon down nicely'''
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
                print('Daemon shut down on {}'.format(host))
            elif len(process_ID_n) == 1:
                print('ERROR: Daemon still running on {} (PID {})'.format(host, process_ID_n[0]))
            else:
                print('ERROR: Multiple daemons still running on {} (PID {})'.format(host, process_ID_n))
        except:
            print('ERROR: No response, daemon still running on {} (PID {})'.format(host, process_ID[0]))
    elif len(process_ID) == 0:
        print('ERROR: No response, daemon not running on {}'.format(host))
    else:
        print('ERROR: Multiple daemons running on {} (PID {})'.format(host, process_ID))


def kill_daemon(daemon_ID):
    '''Kill a daemon (should be used as a last resort)'''
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    process_ID = misc.get_process_ID(process, host)
    if len(process_ID) >= 1:
        misc.kill_processes(process, host)

        # See if it is actually dead
        process_ID_n = misc.get_process_ID(process, host)
        if len(process_ID_n) == 0:
            print('Daemon killed on {}'.format(host))
        elif len(process_ID_n) == 1:
            print('ERROR: Daemon still running on {} (PID {})'.format(host, process_ID_n[0]))
        else:
            print('ERROR: Multiple daemons still running on {} (PID {})'.format(host, process_ID_n))
    else:
        print('ERROR: Daemon not running on {}'.format(host))


########################################################################
# Generic daemon function wrapper
def daemon_function(daemon_ID, function_name, args=[], timeout=0.):
    if not misc.daemon_is_running(daemon_ID):
        print(misc.ERROR('Daemon not running'))
    elif not misc.daemon_is_alive(daemon_ID):
        print(misc.ERROR('Daemon running but not responding, check logs'))
    elif not misc.dependencies_are_alive(daemon_ID):
        print(misc.ERROR('Required dependencies are not responding'))
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
            try:
                return function(*args)
            except Exception as e:
                print(misc.ERROR('Daemon returned {}: "{}"'.format(type(e).__name__, e)))
