#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                                misc.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#   G-TeCS module containing common functions used by TeCS processes   #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import os, sys
import six
import abc
import signal
if six.PY2:
    from commands import getoutput
else:
    from subprocess import getoutput
import time
import Pyro4
import subprocess
import serial
import re
import smtplib
from contextlib import contextmanager

# TeCS modules
from . import params
from . import flags
from six.moves import range

########################################################################
## Command functions
def get_hostname():
    '''Get the hostname of this machine'''
    if 'HOSTNAME' in os.environ:
        return os.environ['HOSTNAME']
    else:
        tmp = getoutput('hostname')
        return tmp.strip()

def get_process_ID(process_name, host):
    '''Retrieve ID numbers of python processes with specified name'''
    process_ID = []
    if 'USER' in os.environ:
        username = os.environ['USER']
    elif 'USERNAME' in os.environ:
        username = os.environ['USERNAME']
    elif 'LOGNAME' in os.environ:
        username = os.environ['LOGNAME']

    if host == 'localhost' or host == get_hostname():
        all_processes = getoutput('ps -fwwu %s | grep -i python' % username)
    else:
        all_processes = getoutput('ssh ' + host + ' ps -fwwu %s | grep -i python' % username)

    for line in all_processes.split('\n'):
        if line.endswith(process_name):
            process_ID.append(line.split()[1])

    return process_ID

def cmd_timeout(command, timeout, bufsize=-1):
    """
    Execute command and limit execution time to 'timeout' seconds.
    Found online and slightly modified
    """

    p = subprocess.Popen(command, bufsize=bufsize, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    start_time = time.time()
    seconds_passed = 0

    while p.poll() is None and seconds_passed < timeout:
        time.sleep(0.1)
        seconds_passed = time.time() - start_time

    if seconds_passed >= timeout:
        try:
            p.stdout.close()
            p.stderr.close()
            p.terminate()
            p.kill()
        except:
            pass
        out = None
    else:
        out = p.stdout.read().strip().decode()
        err = p.stderr.read().decode()
    returncode = p.returncode
    return out #(returncode, err, out)

def kill_processes(process, host):
    '''Kill any specified processes'''
    local_host = get_hostname()
    process_ID_list = get_process_ID(process, host)

    if local_host == host or host == 'localhost':
        for process_ID in process_ID_list:
            os.system('kill -9 ' + process_ID)
            print('Killed process', process_ID)
    else:
        for process_ID in process_ID_list:
            os.system('ssh ' + host + ' kill -9 ' + process_ID)
            print('Killed remote process', process_ID)

def python_command(filename, command, host='localhost',
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   in_background=False):
    '''Send a command to a control script as if using the terminal'''
    if host == 'localhost' or host == get_hostname():
        command_string = ' '.join((sys.executable, filename, command))
    else:
        command_string = ' '.join(('ssh', host, sys.executable, filename, command))
    if not in_background:
        proc = subprocess.Popen(command_string, shell=True, stdout=stdout, stderr=stderr)
        output = proc.communicate()[0]
        return output.decode()
    else:
        proc = subprocess.Popen(command_string, shell=True, stdout=stdout, stderr=stderr)
        return ''

def execute_command(cmd):
    print(cmd)
    p = subprocess.Popen(cmd, shell=True, close_fds=True)
    try:
        p.wait()
    except KeyboardInterrupt:
        print('...ctrl+c detected - closing...')
        try:
           p.terminate()
        except OSError:
           pass
        p.wait()

def ping_host(hostname,count=1,ttl=1):
    '''Ping a network address and return the number of responses'''
    ping = getoutput('ping -q -t ' + str(int(ttl)) + ' -c ' + str(count) + ' ' + hostname)
    out = ping.split('\n')
    packets_received = 0
    for line in range(len(out)):
        if 'ping statistics' in out[line]:
            stats_line = out[line + 1].split()
            packets_received = int(stats_line[3])
            break
    return packets_received

def check_hosts(hostlist):
    '''Ping list of hosts until one responds or the list is exhausted'''
    for hostname in hostlist:
        if ping_host(hostname) > 0:
            return 0 # success
    return 1 # failure

def loopback_test(serialport='/dev/ttyS3', message=b'bob', chances=3):
    '''Send a message to a serial port and try to read it back'''
    s = serial.Serial(serialport, 9600, parity='N', bytesize=8, stopbits=1, rtscts=0, xonxoff=1, timeout=1)
    for i in range(chances):
        s.write(message + b'\n')
        reply = s.readlines()
        for x in reply:
            if x.find(message) >= 0:
                s.close()
                return 0   # success
    s.close()
    return 1   # failure

def signal_handler(signal, frame):
    '''Trap ctrl-c and exit cleanly'''
    print('...ctrl+c detected - closing...')
    sys.exit(0)

class neatCloser:
    """
    Neatly handles closing down of processes.

    This is an abstract class.

    Implement the tidyUp method to set the jobs which
    get run before the task shuts down after receiving
    an instruction to stop.

    Once you have a concrete class based on this abstract class,
    simply create an instance of it and the tidyUp function will
    be caused on SIGINT and SIGTERM signals before closing.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, taskName):
        self.taskName = taskName
        # redirect SIGTERM, SIGINT to us
        signal.signal(signal.SIGTERM, self.interrupt)
        signal.signal(signal.SIGINT, self.interrupt)

    def interrupt(self, sig, handler):
        print('{} received kill signal'.format(self.taskName))
        # do things here on interrupt, for example, stop exposing
        # update queue DB.
        self.tidyUp()
        sys.exit(1)

    @abc.abstractmethod
    def tidyUp(self):
        """
        Must be implemented to define tasks to run when closed
        before process is over.
        """
        return

def daemon_is_running(daemon_ID):
    '''Check if a daemon process is running.'''
    if daemon_ID in params.DAEMONS:
        process = params.DAEMONS[daemon_ID]['PROCESS']
        host    = params.DAEMONS[daemon_ID]['HOST']
    else:
        raise ValueError('Invalid daemon ID')

    process_ID = get_process_ID(process, host)
    if len(process_ID) == 1:
        return True
    elif len(process_ID) == 0:
        return False
    else:
        error_str = 'Multiple instances of {} detected on {}, PID {}'.format(process, host, process_ID)
        raise MultipleDaemonError(error_str)

def daemon_is_alive(daemon_ID):
    '''Check if a daemon is alive and responding to pings.'''
    if daemon_ID in params.DAEMONS:
        address = params.DAEMONS[daemon_ID]['ADDRESS']
    else:
        raise ValueError('Invalid daemon ID')

    daemon = Pyro4.Proxy(address)
    daemon._pyroTimeout = params.PROXY_TIMEOUT
    try:
        ping = daemon.ping()
        if ping == 'ping':
            return True
        else:
            return False
    except:
        return False

def dependencies_are_alive(daemon_ID):
    '''Check if a given daemon's dependencies are alive and responding to pings.'''
    depends = params.DAEMONS[daemon_ID]['DEPENDS']

    if depends[0] != 'None':
        fail = 0
        for dependency in depends:
            if not daemon_is_alive(dependency):
                fail += 1
        if fail > 0:
            return False
        else:
            return True
    else:
        return True

def there_can_only_be_one(daemon_ID):
    '''Ensure the current daemon script isn't already running.

    Returns `True` if it's OK to start.
    '''

    if daemon_ID in params.DAEMONS:
        host = params.DAEMONS[daemon_ID]['HOST']
        port = params.DAEMONS[daemon_ID]['PORT']
        process = params.DAEMONS[daemon_ID]['PROCESS']
    else:
        raise ValueError('Invalid daemon ID')

    # Check if daemon process is already running
    process_ID = get_process_ID(process, host)
    if len(process_ID) > 1:
        raise MultipleDaemonError('Daemon already running')

    # Also check the Pyro address is available
    try:
        pyro_daemon = Pyro4.Daemon(host=host, port=port)
    except:
        raise
    else:
        pyro_daemon.close()

    return True

def find_interface_ID(hostname):
    '''Find what interface should be running on a given host.

    Used by the FLI interfaces to find which interface it should identify as.

    NOTE it will only return the first match, as there should only be one
        interface per host.
        For testing the fli_interfaceB file will be used.
    '''
    for intf in params.FLI_INTERFACES:
        if params.DAEMONS[intf]['HOST'] == hostname:
            return intf
    raise ValueError('Host {} does not have an associated interface'.format(hostname))

########################################################################
## Text formatting functions
def rtxt(text):
    if params.FANCY_OUTPUT:
        return '\033[31;1m' + str(text) + '\033[0m'
    else:
        return text
def gtxt(text):
    if params.FANCY_OUTPUT:
        return '\033[32;1m' + str(text) + '\033[0m'
    else:
        return text
def ytxt(text):
    if params.FANCY_OUTPUT:
        return '\033[33;1m' + str(text) + '\033[0m'
    else:
        return text
def btxt(text):
    if params.FANCY_OUTPUT:
        return '\033[34;1m' + str(text) + '\033[0m'
    else:
        return text
def ptxt(text):
    if params.FANCY_OUTPUT:
        return '\033[35;1m' + str(text) + '\033[0m'
    else:
        return text
def bold(text):
    if params.FANCY_OUTPUT:
        return '\033[1m' + str(text) + '\033[0m'
    else:
        return text
def undl(text):
    if params.FANCY_OUTPUT:
        return '\033[4m' + str(text) + '\033[0m'
    else:
        return text

########################################################################
# Errors and exceptions

class DaemonConnectionError(Exception):
    '''To be used when a command to a daemon fails.
    e.g. if the Daemon is not running or is not responding
    '''
    pass


class DaemonDependencyError(Exception):
    '''To be used if a daemons's dependendecneis are not responding.'''
    pass


class MultipleDaemonError(Exception):
    '''To be used if multiple instances of a daemon are detected.'''
    pass


class InputError(Exception):
    '''To be used if an input command or arguments aren't valid.'''
    pass


class HardwareStatusError(Exception):
    '''To be used if a command isn't possible due to the hardware status.
    e.g. trying to start an exposure when the cameras are already exposing
    '''
    pass


class HorizonError(Exception):
    '''To be used if a slew command would bring the mount below the limit.'''
    pass


def ERROR(message):
    return rtxt(bold('ERROR')) + ': ' + str(message)


@contextmanager
def print_errors():
    '''A context manager to catch exceptions and print them nicely.
    Used within the control scripts to handle errors from daemons.
    '''
    try:
        yield
    except Exception as error:
        print(ERROR('"{}: {}"'.format(type(error).__name__, error)))
        pass


########################################################################
# Misc functions
def adz(num):
    num = repr(num)
    if len(num) == 1:
        num = '0' + num
    return num

def valid_ints(array, allowed):
    valid = []
    for i in array:
        if i == '':
            pass
        elif not i.isdigit():
            print(ERROR('"{}" is invalid, must be in {}'.format(i,allowed)))
        elif i not in [str(x) for x in allowed]:
            print(ERROR('"{}" is invalid, must be in {}'.format(i,allowed)))
        elif int(i) not in valid:
            valid += [int(i)]
    valid.sort()
    return valid

def is_num(value):
    try:
        float(value)
        return True
    except ValueError:
        return False

def remove_html_tags(data):
    '''Remove html tags from a given line'''
    p = re.compile(r'<.*?>')
    return p.sub('', data).strip()

def send_email(recipients=params.EMAIL_LIST, subject='GOTO', message='Test'):
    to_address = ', '.join(recipients)
    from_address = params.EMAIL_ADDRESS
    header = 'To:%s\nFrom:%s\nSubject:%s\n' % (to_address,from_address,subject)
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S',time.gmtime())
    text = '%s\n\nMessage sent at %s' % (message,timestamp)

    server = smtplib.SMTP(EMAIL_SERVER)
    server.starttls()
    server.login('goto-observatory@gmail.com', 'password')
    server.sendmail(fromaddr, recipients, header + '\n' + text + '\n\n')
    server.quit()
    print('Sent mail to',recipients)


def ut_list_to_mask(ut_list):
    """Converts a UT list to a mask integer"""
    ut_mask = 0
    all_tels = sorted(list(params.TEL_DICT))
    for i in all_tels:
        if i in ut_list:
            ut_mask += 2**(i-1)
    return ut_mask


def ut_mask_to_string(ut_mask):
    """Converts a UT mask integer to a string of 0s and 1s"""
    total_tels = max(list(params.TEL_DICT))
    bin_str = format(ut_mask, '0{}b'.format(total_tels))
    ut_str = bin_str[-1*total_tels:]
    return ut_str


def ut_string_to_list(ut_string):
    """Converts a UT string of 0s and 1s to a list"""
    ut_list = []
    all_tels = sorted(list(params.TEL_DICT))
    for i in all_tels:
        if ut_string[-1*i] == '1':
            ut_list.append(i)
    ut_list.sort()
    return ut_list


def get_observer():
    """Find the name of the current observer"""
    override_flags = flags.Overrides()
    if not override_flags.robotic:
        # The pilot is in control
        return 'GOTO-pilot'
    elif os.path.exists(params.CONFIG_PATH + 'observer'):
        with open(params.CONFIG_PATH + 'observer', 'r') as f:
            lines = f.readlines()
            return lines[0]
    else:
        return 'Unknown'
