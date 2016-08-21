# oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo #
#                             astronomy.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#             G-TeCS module containing observing utilities             #
#                     Stuart Littlefair, Sheffield, 2016               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
# oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo #

#  Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import time
import Pyro4
import glob
import os
import numpy as np

# TeCS modules
from . import params
from .time_date import nightStarting
from .astronomy import tel_str
from .misc import execute_command as cmd


def set_new_focus(values):
    """
    Move each telescope to the requested focus

    Parameters
    ----------
    values : float, list or np.ndarray
    """
    try:
        len(values)
    except:
        values = [values] * len(params.TEL_DICT)
    if len(values) != len(params.TEL_DICT):
        raise ValueError("values should either be a float or same length as number of OTAs")

    current_values = get_current_focus()
    difference = np.array(values) - current_values
    for i, delta in enumerate(difference):
        tel = i+1
        cmd('foc move {} {}'.format(tel, int(delta)))


def get_current_focus():
    """
    Find the current focus positions
    """
    FOC_DAEMON_ADDRESS = params.DAEMONS['foc']['ADDRESS']
    with Pyro4.Proxy(FOC_DAEMON_ADDRESS) as foc:
        foc._pyroTimeout = params.PROXY_TIMEOUT
        foc_info = foc.get_info()
    keys = ['current_pos{}'.format(i+1) for i in range(4)]
    return np.array([foc_info[key] for key in keys])


def wait_for_focuser(timeout):
    """
    Wait until focuser has finished moving

    Parameters
    ----------
    timeout : float
        time in seconds after which to timeout
    """
    FOC_DAEMON_ADDRESS = params.DAEMONS['foc']['ADDRESS']
    start_time = time.time()
    still_moving = True
    timed_out = False
    status_keys = ['status{}'.format(i+1) for i in range(4)]
    while still_moving and not timed_out:
        try:
            with Pyro4.Proxy(FOC_DAEMON_ADDRESS) as foc:
                foc._pyroTimeout = params.PROXY_TIMEOUT
                foc_info = foc.get_info()
        except Pyro4.errors.ConnectionClosedError:
            pass
        if np.all([foc_info[key] == 'Ready' for key in status_keys]):
            still_moving = False
        if time.time() - start_time > timeout:
            timed_out = True
    if timed_out:
        raise TimeoutError('Focuser timed out')


def goto(ra, dec):
    """
    Move telescope and wait until there.

    Parameters
    ----------
    ra : float
        J2000 ra in decimal degrees
    dec : float
        J2000 dec in decimal degrees
    """
    ra_string, dec_string = tel_str(ra, dec)
    cmd("mnt ra " + ra_string)
    cmd("mnt dec " + dec_string)
    time.sleep(1)
    cmd("mnt slew")


def wait_for_telescope(timeout=None):
    """
    Wait for telescope to be ready

    Parameters
    ----------
    timeout : float
        time in seconds after which to timeout. None to wait forever
    """
    start_time = time.time()
    MNT_DAEMON_ADDRESS = params.DAEMONS['mnt']['ADDRESS']
    still_moving = True
    timed_out = False
    while still_moving and not timed_out:
        try:
            with Pyro4.Proxy(MNT_DAEMON_ADDRESS) as mnt:
                mnt._pyroTimeout = params.PROXY_TIMEOUT
                mnt_info = mnt.get_info()
        except Pyro4.errors.ConnectionClosedError:
            pass
        if mnt_info['status'] == 'Tracking' and mnt_info['target_dist'] < 0.1:
            still_moving = False
        print(time.time(), start_time, (time.time() - start_time) > timeout)
        if timeout and (time.time() - start_time) > timeout:
            print('Bum')
            timed_out = True

        # don't hammer the daemons
        time.sleep(5)
    if timed_out:
        raise TimeoutError('Telescope timed out')


def random_offset(offset_size):
    """
    Make a random offset of the given size

    Parameters
    ----------
    offset_size : float
        offset size in arcseconds
    """
    compass = ['n', 's', 'e', 'w']
    dirn = np.random.choice(compass)
    cmd("mnt step {}".format(offset_size))
    time.sleep(0.2)
    cmd("mnt {}".format(dirn))
    # wait a short while for it to move
    time.sleep(2)


# utility functions for taking images
def last_written_image():
    """
    Returns the last written image files

    Returns
    -------
    files : list
        a list of the image files
    """
    path = os.path.join(params.IMAGE_PATH + nightStarting())
    newest = max(glob.iglob(os.path.join(path,'*.fits')), key=os.path.getctime)
    root = newest.split('_ut')[0]

    fnames = [root+'_ut{}.fits'.format(key) for key in params.TEL_DICT.keys()]
    return [os.path.join(path, fname) for fname in fnames]


def wait_for_exposure_queue(timeout=None):
    """
    With a set of exposures underway, wait for an empty queue

    Parameters
    ----------
    timeout : float
        time in seconds after which to timeout. None to wait forever
    """
    # we should not return straight away, but wait until queue is empty
    EXQ_DAEMON_ADDRESS = params.DAEMONS['exq']['ADDRESS']
    start_time = time.time()
    still_working = True
    timed_out = False
    while still_working and not timed_out:
        time.sleep(10)
        try:
            with Pyro4.Proxy(EXQ_DAEMON_ADDRESS) as exq:
                exq._pyroTimeout = params.PROXY_TIMEOUT
                exq_info = exq.get_info()

            nexp = exq_info['queue_length']
            status = exq_info['status']
            if nexp == 0 and status == 'Ready':
                still_working = False
        except Pyro4.errors.ConnectionClosedError:
            # for now, silently pass failures to contact exq daemon
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True
    if timed_out:
        raise TimeoutError('Exposure queue timed out')