"""
Miscellaneous common functions
"""

import os
import sys
import time
import pid
import abc
import signal
import Pyro4
import subprocess
import serial
import re
import smtplib
from contextlib import contextmanager

from . import params
from . import errors
from . import flags
from .style import ERROR


def get_hostname():
    """Get the hostname of this machine"""
    if 'HOSTNAME' in os.environ:
        return os.environ['HOSTNAME']
    else:
        tmp = subprocess.getoutput('hostname')
        return tmp.strip()


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


def kill_process(pidname, host):
    """Kill any specified processes"""
    pid = get_pid(pidname, host)

    if host in ['127.0.0.1', params.LOCAL_HOST]:
        os.system('kill -9 {}'.format(pid))
    else:
        os.system('ssh {} kill -9 {}'.format(host, pid))

    clear_pid(pidname, host)

    print('Killed process {} on {}'.format(pid, host))


def python_command(filename, command, host='127.0.0.1',
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                   in_background=False):
    """Send a command to a control script as if using the terminal"""
    if host in ['127.0.0.1', params.LOCAL_HOST]:
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


def execute_command(command_string, timeout=30):
    """For commands that should return quickly."""
    print('{}:'.format(command_string))
    try:
        ret_str = subprocess.check_output(command_string,
                                          shell=True,
                                          stderr=subprocess.STDOUT,
                                          timeout=timeout)
        print('> '+ret_str.strip().decode().replace('\n','\n> '))
        return 0
    except subprocess.TimeoutExpired:
        print('Command {} timed out after {}s'.format(command_string, timeout))
        return 1


def execute_long_command(command_string):
    """For commands that might not return immediately.

    Examples:
        The tail command for logs, because you can use tail's -f param
        obs_scripts
    """
    print(command_string)
    p = subprocess.Popen(command_string, shell=True, close_fds=True)
    try:
        p.wait()
    except KeyboardInterrupt:
        print('...ctrl+c detected - closing ({})...'.format(command_string))
        try:
            p.terminate()
        except OSError:
            pass
        p.wait()


def ping_host(hostname, count=1, ttl=1):
    """Ping a network address and return the number of responses"""
    ping = subprocess.getoutput('ping -q -t ' + str(int(ttl)) + ' -c ' + str(count) + ' ' + hostname)
    out = ping.split('\n')
    packets_received = 0
    for line in range(len(out)):
        if 'ping statistics' in out[line]:
            stats_line = out[line + 1].split()
            packets_received = int(stats_line[3])
            break
    return packets_received


def check_hosts(hostlist):
    """Ping list of hosts until one responds or the list is exhausted"""
    for hostname in hostlist:
        if ping_host(hostname) > 0:
            return 0 # success
    return 1 # failure


def signal_handler(signal, frame):
    """Trap ctrl-c and exit cleanly"""
    print('...ctrl+c detected - closing ({} {})...'.format(signal, frame))
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


def get_pid(pidname, host='127.0.0.1'):
    """Check if a pid file exists with the given name.

    Returns the pid if it is found, or None if not.
    """
    # pid.PidFile(pidname, piddir=params.PID_PATH).check() is nicer,
    # but won't work with remote machines
    pidpath = os.path.join(params.PID_PATH, pidname+'.pid')
    if host in ['127.0.0.1', params.LOCAL_HOST]:
        command_string = 'cat {}'.format(pidpath)
    else:
        # NOTE this assumes the config path is the same on the remote machine
        command_string = 'ssh {} cat {}'.format(host, pidpath)
    output = subprocess.getoutput(command_string)
    if 'No such file or directory' in output:
        return None
    else:
        return int(output)


def clear_pid(pidname, host='127.0.0.1'):
    """Clear a pid in case we've killed the process."""
    pidpath = os.path.join(params.PID_PATH, pidname+'.pid')
    if host in ['127.0.0.1', params.LOCAL_HOST]:
        command_string = 'rm {}'.format(pidpath)
    else:
        # NOTE this assumes the config path is the same on the remote machine
        command_string = 'ssh {} rm {}'.format(host, pidpath)
    output = subprocess.getoutput(command_string)
    if not output or 'No such file or directory' in output:
        return 0
    else:
        print(output)
        return 1


@contextmanager
def print_errors():
    """A context manager to catch exceptions and print them nicely.
    Used within the control scripts to handle errors from daemons.
    """
    try:
        yield
    except Exception as error:
        print(ERROR('"{}: {}"'.format(type(error).__name__, error)))
        pass


@contextmanager
def make_pid_file(pidname):
    """A context manager create a pidfile for the daemons."""
    try:
        with pid.PidFile(pidname, piddir=params.PID_PATH):
            yield
    except pid.PidFileError:
        # there can only be one
        raise errors.MultipleDaemonError('Daemon already running')


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


def valid_strings(array, allowed):
    valid = []
    for i in array:
        if i == '':
            pass
        elif i.upper() not in [str(x) for x in allowed]:
            print(ERROR('"{}" is invalid, must be in {}'.format(i,allowed)))
        elif i.upper() not in valid:
            valid += [i.upper()]
    return valid


def is_num(value):
    try:
        float(value)
        return True
    except ValueError:
        return False


def remove_html_tags(data):
    """Remove html tags from a given line"""
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
    all_tels = sorted(params.TEL_DICT)
    for i in all_tels:
        if i in ut_list:
            ut_mask += 2**(i-1)
    return ut_mask


def ut_mask_to_string(ut_mask):
    """Converts a UT mask integer to a string of 0s and 1s"""
    total_tels = max(sorted(params.TEL_DICT))
    bin_str = format(ut_mask, '0{}b'.format(total_tels))
    ut_str = bin_str[-1*total_tels:]
    return ut_str


def ut_string_to_list(ut_string):
    """Converts a UT string of 0s and 1s to a list"""
    ut_list = []
    all_tels = sorted(params.TEL_DICT)
    for i in all_tels:
        if ut_string[-1*i] == '1':
            ut_list.append(i)
    ut_list.sort()
    return ut_list
