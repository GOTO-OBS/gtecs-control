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

def get_process_ID_windows(process_name, host, username=None):
    '''Retrieve ID numbers of python processes from a remote Windows machine'''
    process_ID = []
    if username:
        all_processes = getoutput('ssh {}@{}'.format(username, host)
                                 +' wmic process get ProcessId,CommandLine'
                                 +' | grep -i python')
    else:
        all_processes = getoutput('ssh {}'.format(host)
                                 +' wmic process get ProcessId,CommandLine'
                                 +' | grep -i python')

    for line in all_processes.split('\n'):
        if process_name in line:
            process_ID.append(line.split()[2])

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

def kill_processes_windows(process, host, username=None):
    '''Kill any specified processes on a remote Windows machine'''
    process_ID_list = get_process_ID_windows(process, host, username)

    for process_ID in process_ID_list:
        getoutput('ssh {}@{}'.format(username, host)
                 +' taskkill /F /PID {}'.format(process_ID))

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
    subprocess.Popen(cmd, shell=True, close_fds=True).wait()

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

class MultipleDaemonError(Exception):
    pass

def daemon_is_running(daemon_ID):
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    process_ID = get_process_ID(process, host)
    if len(process_ID) == 1:
        return True
    elif len(process_ID) == 0:
        return False
    else:
        error_str = 'Multiple instances of {} detected on {}, PID {}'.format(process, host, process_ID)
        raise MultipleDaemonError(error_str)

def daemon_is_alive(daemon_ID):
    '''
    Will check if a daemon or interface is alive and responding to pings
    '''
    if daemon_ID in params.DAEMONS:
        address = params.DAEMONS[daemon_ID]['ADDRESS']
    elif daemon_ID in params.FLI_INTERFACES:
        address = params.FLI_INTERFACES[daemon_ID]['ADDRESS']
    elif daemon_ID in params.WIN_INTERFACES:
        address = params.WIN_INTERFACES[daemon_ID]['ADDRESS']
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

    Returns `True` if it's OK to start, `False` if there is annother instance
    of this daemon already running.
    '''

    if daemon_ID in params.DAEMONS:
        host = params.DAEMONS[daemon_ID]['HOST']
        port = params.DAEMONS[daemon_ID]['PORT']
        process = params.DAEMONS[daemon_ID]['PROCESS']
    elif daemon_ID in params.FLI_INTERFACES:
        host = params.FLI_INTERFACES[daemon_ID]['HOST']
        port = params.FLI_INTERFACES[daemon_ID]['PORT']
        process = params.FLI_INTERFACES[daemon_ID]['PROCESS']
    elif daemon_ID in params.WIN_INTERFACES:
        host = params.WIN_INTERFACES[daemon_ID]['HOST']
        port = params.WIN_INTERFACES[daemon_ID]['PORT']
        process = params.FLI_INTERFACES[daemon_ID]['PROCESS']
    else:
        raise ValueError('Invalid daemon ID')

    # Check if daemon process is already running
    if daemon_ID in params.WIN_INTERFACES:
        process_ID = get_process_ID_windows(process, host, params.WIN_USER)
    else:
        process_ID = get_process_ID(process, host)
    if len(process_ID) > 1:
        print('ERROR: Daemon already running')
        return False

    # Also check the Pyro address is available
    try:
        pyro_daemon = Pyro4.Daemon(host=host, port=port)
    except IOError as err:
        if err.args[1] == 'Address already in use':
            print('ERROR: Daemon tried to start but was already registered')
            return False
        else:
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
        if params.FLI_INTERFACES[intf]['HOST'] == hostname:
            return intf
    raise ValueError('Host {} does not have an associated interface'.format(hostname))

########################################################################
## Text formatting functions
def rtxt(text):
    return '\033[31;1m' + str(text) + '\033[0m'
def gtxt(text):
    return '\033[32;1m' + str(text) + '\033[0m'
def ytxt(text):
    return '\033[33;1m' + str(text) + '\033[0m'
def btxt(text):
    return '\033[34;1m' + str(text) + '\033[0m'
def ptxt(text):
    return '\033[35;1m' + str(text) + '\033[0m'
def bold(text):
    return '\033[1m' + str(text) + '\033[0m'
def undl(text):
    return '\033[4m' + str(text) + '\033[0m'

########################################################################
# Misc functions
def ERROR(message):
    return rtxt(bold('ERROR')) + ': ' + str(message)

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
            print('ERROR: "' + str(i) + '" is invalid, must be in',allowed)
        elif i not in [str(x) for x in allowed]:
            print('ERROR: "' + str(i) + '" is invalid, must be in',allowed)
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
    p = re.compile(r'<.*?>')
    return p.sub('', data)

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


def ut_mask_to_list(ut_mask):
    """Converts a UT mask integer to a list of telescope numbers"""
    ut_list = []
    all_tels = sorted(list(params.TEL_DICT))
    for i in reversed(all_tels):
        if ut_mask - 2**(i-1) >= 0:
            ut_list.append(i)
            ut_mask -= 2**(i-1)
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
