"""Miscellaneous common functions."""

import abc
import re
import signal
import smtplib
import sys
import time
from contextlib import contextmanager

from . import params
from .style import errortxt


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
        """Catch interrupts."""
        print('{} received kill signal'.format(self.taskname))
        # do things here on interrupt
        self.tidy_up()
        sys.exit(1)

    @abc.abstractmethod
    def tidy_up(self):
        """Must be implemented to define tasks to run when closed before process is over."""
        return


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
    header = 'To:{}\nFrom:{}\nSubject:{}\n'.format(to_address, from_address, subject)
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())
    text = '{}\n\nMessage sent at {}'.format(message, timestamp)

    server = smtplib.SMTP(params.EMAIL_SERVER)
    server.starttls()
    server.login('goto-observatory@gmail.com', 'password')
    server.sendmail(from_address, recipients, header + '\n' + text + '\n\n')
    server.quit()
    print('Sent mail to', recipients)


def ut_list_to_mask(ut_list):
    """Convert a UT list to a mask integer."""
    ut_mask = 0
    for ut in params.UTS:
        if ut in ut_list:
            ut_mask += 2**(ut - 1)
    return ut_mask


def ut_mask_to_string(ut_mask):
    """Convert a UT mask integer to a string of 0s and 1s."""
    total_uts = max(params.UTS)
    bin_str = format(ut_mask, '0{}b'.format(total_uts))
    ut_str = bin_str[-1 * total_uts:]
    return ut_str


def ut_string_to_list(ut_string):
    """Convert a UT string of 0s and 1s to a list."""
    ut_list = []
    for ut in params.UTS:
        if ut_string[-1 * ut] == '1':
            ut_list.append(ut)
    ut_list.sort()
    return ut_list
