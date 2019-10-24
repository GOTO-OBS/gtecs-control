"""Miscellaneous common functions."""

import abc
import os
import re
import signal
import smtplib
import subprocess
import sys
import time
from contextlib import contextmanager

import pid

from . import errors
from . import params
from .style import errortxt


def kill_process(pidname, host='127.0.0.1'):
    """Kill any specified processes."""
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
    """Send a command to a control script as if using the terminal."""
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
        p = subprocess.Popen(command_string,
                             shell=True,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT)
        ret_str, _ = p.communicate(timeout=timeout)
        print('> ' + ret_str.strip().decode().replace('\n', '\n> '))
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


def signal_handler(signal, frame):
    """Trap ctrl-c and exit cleanly."""
    print('...ctrl+c detected - closing ({} {})...'.format(signal, frame))
    sys.exit(0)


class NeatCloser(object, metaclass=abc.ABCMeta):
    """Neatly handles closing down of processes.

    This is an abstract class.

    Implement the tidy_up method to set the commands which
    get run after receiving an instruction to stop
    before the task shuts down.

    Once you have a concrete class based on this abstract class,
    simply create an instance of it and the tidy_up function will
    be caused on SIGINT and SIGTERM signals before closing.
    """

    def __init__(self, taskname):
        self.taskname = taskname
        # redirect SIGTERM, SIGINT to us
        signal.signal(signal.SIGTERM, self.interrupt)
        signal.signal(signal.SIGINT, self.interrupt)

    def interrupt(self, sig, handler):
        """Catch interupts."""
        print('{} received kill signal'.format(self.taskname))
        # do things here on interrupt, for example, stop exposing
        # update queue DB.
        self.tidy_up()
        sys.exit(1)

    @abc.abstractmethod
    def tidy_up(self):
        """Must be implemented to define tasks to run when closed before process is over."""
        return


def get_pid(pidname, host=None):
    """Check if a pid file exists with the given name.

    Returns the pid if it is found, or None if not.
    """
    # pid.PidFile(pidname, piddir=params.PID_PATH).check() is nicer,
    # but won't work with remote machines

    if pidname in params.DAEMONS:
        new_host = params.DAEMONS[pidname]['HOST']
        if host and new_host != host:
            raise ValueError('Given host ({}) does not match host defined in params ({})'.format(
                             host, new_host))
        else:
            host = new_host
    elif not host:
        host = '127.0.0.1'

    pidfile = pidname + '.pid'
    pidpath = os.path.join(params.PID_PATH, pidfile)
    if host in ['127.0.0.1', params.LOCAL_HOST]:
        command_string = 'cat {}'.format(pidpath)
    else:
        # NOTE this assumes the pid path is the same on the remote machine
        command_string = 'ssh {} cat {}'.format(host, pidpath)
    output = subprocess.getoutput(command_string)
    if 'No such file or directory' in output:
        return None
    else:
        return int(output)


def clear_pid(pidname, host='127.0.0.1'):
    """Clear a pid in case we've killed the process."""
    pidfile = pidname + '.pid'
    pidpath = os.path.join(params.PID_PATH, pidfile)
    if host in ['127.0.0.1', params.LOCAL_HOST]:
        command_string = 'rm {}'.format(pidpath)
    else:
        # NOTE this assumes the pid path is the same on the remote machine
        command_string = 'ssh {} rm {}'.format(host, pidpath)
    output = subprocess.getoutput(command_string)
    if not output or 'No such file or directory' in output:
        return 0
    else:
        print(output)
        return 1


@contextmanager
def print_errors():
    """Catch exceptions and print them nicely.

    Used within the control scripts to handle errors from daemons.
    """
    try:
        yield
    except Exception as error:
        print(errortxt('"{}: {}"'.format(type(error).__name__, error)))
        pass


@contextmanager
def make_pid_file(pidname):
    """Create a pidfile."""
    try:
        with pid.PidFile(pidname, piddir=params.PID_PATH):
            yield
    except pid.PidFileError:
        # there can only be one
        raise errors.MultipleProcessError('Process "{}" already running'.format(pidname))


def valid_ints(array, allowed):
    """Return valid ints from a list."""
    valid = []
    for i in array:
        if i == '':
            pass
        elif not i.isdigit():
            print(errortxt('"{}" is invalid, must be in {}'.format(i, allowed)))
        elif i not in [str(x) for x in allowed]:
            print(errortxt('"{}" is invalid, must be in {}'.format(i, allowed)))
        elif int(i) not in valid:
            valid += [int(i)]
    valid.sort()
    return valid


def valid_strings(array, allowed):
    """Return valid strings from a list."""
    valid = []
    for i in array:
        if i == '':
            pass
        elif i.upper() not in [str(x) for x in allowed]:
            print(errortxt('"{}" is invalid, must be in {}'.format(i, allowed)))
        elif i.upper() not in valid:
            valid += [i.upper()]
    return valid


def is_num(value):
    """Return if a value is a valid number."""
    try:
        float(value)
        return True
    except ValueError:
        return False


def remove_html_tags(data):
    """Remove html tags from a given line."""
    p = re.compile(r'<.*?>')
    return p.sub('', data).strip()


def send_email(recipients=params.EMAIL_LIST, subject='GOTO', message='Test'):
    """Send an email.

    TODO: I'm pretty sure this is broken.
    """
    to_address = ', '.join(recipients)
    from_address = params.EMAIL_ADDRESS
    header = 'To:%s\nFrom:%s\nSubject:%s\n' % (to_address, from_address, subject)
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())
    text = '%s\n\nMessage sent at %s' % (message, timestamp)

    server = smtplib.SMTP(params.EMAIL_SERVER)
    server.starttls()
    server.login('goto-observatory@gmail.com', 'password')
    server.sendmail(from_address, recipients, header + '\n' + text + '\n\n')
    server.quit()
    print('Sent mail to', recipients)


def ut_list_to_mask(ut_list):
    """Convert a UT list to a mask integer."""
    ut_mask = 0
    all_uts = sorted(params.UT_DICT)
    for i in all_uts:
        if i in ut_list:
            ut_mask += 2**(i - 1)
    return ut_mask


def ut_mask_to_string(ut_mask):
    """Convert a UT mask integer to a string of 0s and 1s."""
    total_uts = max(sorted(params.UT_DICT))
    bin_str = format(ut_mask, '0{}b'.format(total_uts))
    ut_str = bin_str[-1 * total_uts:]
    return ut_str


def ut_string_to_list(ut_string):
    """Convert a UT string of 0s and 1s to a list."""
    ut_list = []
    all_uts = sorted(params.UT_DICT)
    for i in all_uts:
        if ut_string[-1 * i] == '1':
            ut_list.append(i)
    ut_list.sort()
    return ut_list
