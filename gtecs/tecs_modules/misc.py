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

    if host == get_hostname():
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

    if local_host == host:
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

def python_command(filename, command, host='localhost'):
    '''Send a command to a control script as if using the terminal'''
    if host == 'localhost' or host == get_hostname():
        command_string = ' '.join((sys.executable, filename, command))
    else:
        command_string = ' '.join(('ssh', host, sys.executable, filename, command))
    proc = subprocess.Popen(command_string, shell=True, stdout=subprocess.PIPE)
    output = proc.communicate()[0]
    return output.decode()

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

def loopback_test(serialport='/dev/ttyS3', message='bob', chances=3):
    '''Send a message to a serial port and try to read it back'''
    s = serial.Serial(serialport, 9600, parity='N', bytesize=8, stopbits=1, rtscts=0, xonxoff=1, timeout=1)
    for i in range(chances):
        s.write(message + '\n')
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
    address = params.DAEMONS[daemon_ID]['ADDRESS']

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

########################################################################
# Core Daemon functions
def start_daemon(daemon_ID):
    '''Start a daemon (unless it is already running)'''
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']
    pyroid  = params.DAEMONS[daemon_ID]['PYROID']
    if params.REDIRECT_STDOUT:
        output = params.LOG_PATH + pyroid + '-stdout.log'
    else:
        output = '/dev/stdout'

    process_path = os.path.join(params.DAEMON_PATH, process)
    out_cmd = ''.join(('>', output, '2>&1 &'))

    process_ID = get_process_ID(process, host)
    if len(process_ID) == 0:
        # Run script
        python_command(process_path, out_cmd, host)

        # See if it started
        process_ID_n = get_process_ID(process, host)
        if len(process_ID_n) == 1:
            print('Daemon started on {} (PID {})'.format(host, process_ID_n[0]))
        elif len(process_ID_n) > 1:
            print('ERROR: Multiple daemons running on {} (PID {})'.format(host, process_ID_n))
        else:
            print('ERROR: Daemon did not start on {}, check logs'.format(host))
    elif len(process_ID) == 1:
        print('ERROR: Daemon already running on {} (PID {})'.format(host, process_ID[0]))
    else:
        print('ERROR: Multiple daemons already running on {} (PID {})'.format(host, process_ID_n))


def ping_daemon(daemon_ID):
    '''Ping a daemon'''
    address = params.DAEMONS[daemon_ID]['ADDRESS']
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    process_ID = get_process_ID(process, host)
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
        print('ERROR: Multiple daemons running on {} (PID {})'.format(host, process_ID_n))


def shutdown_daemon(daemon_ID):
    '''Shut a daemon down nicely'''
    address = params.DAEMONS[daemon_ID]['ADDRESS']
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    process_ID = get_process_ID(process, host)
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
            process_ID_n = get_process_ID(process, host)
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
        print('ERROR: Multiple daemons running on {} (PID {})'.format(host, process_ID_n))


def kill_daemon(daemon_ID):
    '''Kill a daemon (should be used as a last resort)'''
    process = params.DAEMONS[daemon_ID]['PROCESS']
    host    = params.DAEMONS[daemon_ID]['HOST']

    process_ID = get_process_ID(process, host)
    if len(process_ID) >= 1:
        kill_processes(process, host)

        # See if it is actually dead
        process_ID_n = get_process_ID(process, host)
        if len(process_ID_n) == 0:
            print('Daemon killed on {}'.format(host))
        elif len(process_ID_n) == 1:
            print('ERROR: Daemon still running on {} (PID {})'.format(host, process_ID_n[0]))
        else:
            print('ERROR: Multiple daemons still running on {} (PID {})'.format(host, process_ID_n))
    else:
        print('ERROR: Daemon not running on {}'.format(host))


def start_win(process, host, stdout='/dev/null'):
    os.system('ssh goto@'+host+' '+params.CYGWIN_PYTHON_PATH+' "'+params.WIN_PATH+process+' >'+stdout+' 2>&1 &"')


def daemon_function(daemon_ID, function_name, args=[]):
    if not daemon_is_running(daemon_ID):
        print(ERROR('Daemon not running'))
    elif not daemon_is_alive(daemon_ID):
        print(ERROR('Daemon running but not responding, check logs'))
    else:
        address = params.DAEMONS[daemon_ID]['ADDRESS']
        with Pyro4.Proxy(address) as proxy:
            proxy._pyroTimeout = params.PROXY_TIMEOUT
            try:
                function = getattr(proxy, function_name)
            except AttributeError:
                raise NotImplementedError('Invalid function')
            try:
                return function(*args)
            except Exception as e:
                print(ERROR('Daemon returned {}: "{}"'.format(type(e).__name__, e)))

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
        elif i not in [str(x) for x in list(params.TEL_DICT.keys())]:
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
