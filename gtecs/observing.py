"""
Observing utilities
"""

import os
import time
import Pyro4
import glob

import numpy as np

from astropy.time import Time

from . import params
from .astronomy import tel_str, check_alt_limit, nightStarting
from .misc import execute_command as cmd
from .daemons import daemon_function


def check_schedule(time, write_html):
    """
    Check the schedule
    """
    SCHEDULER_DAEMON_ADDRESS = params.DAEMONS['scheduler']['ADDRESS']
    with Pyro4.Proxy(SCHEDULER_DAEMON_ADDRESS) as scheduler:
        scheduler._pyroTimeout = params.PROXY_TIMEOUT
        new_pointing = scheduler.check_queue(time, write_html)
        if new_pointing is not None:
            return new_pointing.id, new_pointing.priority_now, new_pointing.mintime
        else:
            return None, None, None


def check_dome_closed():
    """
    Check the dome, returns True if the dome is closed or False if it's open
    """
    dome_info = daemon_function('dome', 'get_info')
    return dome_info['dome'] == 'closed'


def get_cam_temps():
    """
    Get a dict of camera temps
    """
    CAM_DAEMON_ADDRESS = params.DAEMONS['cam']['ADDRESS']
    with Pyro4.Proxy(CAM_DAEMON_ADDRESS) as cam:
        cam._pyroTimeout = params.PROXY_TIMEOUT
        cam_info = cam.get_info()
    values = {}
    for tel in params.TEL_DICT:
        key = 'ccd_temp{}'.format(tel)
        values[tel] = cam_info[key]
    return values


def prepare_for_images():
    """
    Make sure the hardware is set up for taking images:
      - ensure the exposure queue is empty
      - ensure the filter wheels are homed
      - ensure the cameras are at operating temperature
    """

    # Empty the exposure queue
    if not exposure_queue_is_empty():
        cmd('exq pause')
        time.sleep(1)
        cmd('exq clear')
        while not exposure_queue_is_empty():
            time.sleep(1)
    cmd('exq resume')

    # Home the filter wheels
    if not filters_are_homed():
        cmd('filt home')
        while not filters_are_homed():
            time.sleep(1)

    # Bring the CCDs down to temperature
    if not cameras_are_cool():
        cmd('cam temp {}'.format(params.CCD_TEMP))
        while not cameras_are_cool():
            time.sleep(1)


def set_new_focus(values):
    """
    Move each telescope to the requested focus

    Parameters
    ----------
    values : float, dict
        a dictionary of telescope IDs and focus values
    """
    try:
        # will raise if not a dict, or keys not valid
        assert all(key in params.TEL_DICT for key in values.keys())
    except:
        # same value for all
        values = {key: values for key in params.TEL_DICT}

    for tel in params.TEL_DICT:
        cmd('foc set {} {}'.format(tel, int(values[tel])))


def get_current_focus():
    """
    Find the current focus positions
    """
    FOC_DAEMON_ADDRESS = params.DAEMONS['foc']['ADDRESS']
    with Pyro4.Proxy(FOC_DAEMON_ADDRESS) as foc:
        foc._pyroTimeout = params.PROXY_TIMEOUT
        foc_info = foc.get_info()
    values = {}
    for tel in params.TEL_DICT:
        key = 'current_pos{}'.format(tel)
        values[tel] = foc_info[key]
    return values


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
    status_keys = ['status{}'.format(tel) for tel in params.TEL_DICT]
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
    Move telescope to given RA/Dec.

    Parameters
    ----------
    ra : float
        J2000 ra in decimal degrees
    dec : float
        J2000 dec in decimal degrees
    """
    if check_alt_limit(ra, dec, Time.now()):
        raise ValueError('target too low, cannot set target')
    ra_string, dec_string = tel_str(ra, dec)
    cmd("mnt ra " + ra_string)
    cmd("mnt dec " + dec_string)
    time.sleep(1)
    cmd("mnt slew")


def goto_altaz(alt, az):
    """
    Move telescope to given Alt/Az.

    Parameters
    ----------
    alt : float
        altitude in decimal degrees
    az : float
        azimuth in decimal degrees
    """
    if alt < params.MIN_ELEVATION:
        raise ValueError('target too low, cannot set target')
    cmd('mnt slew_altaz ' + str(alt) + ' ' + str(az))


def wait_for_telescope(timeout=None, targ_dist=0.003):
    """
    Wait for telescope to be ready

    Parameters
    ----------
    timeout : float
        time in seconds after which to timeout. None to wait forever
    targ_dist : float
        distance in degrees from the target to consider returning after
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
        if mnt_info['status'] == 'Tracking' and mnt_info['target_dist'] < targ_dist:
            still_moving = False

        if timeout and (time.time() - start_time) > timeout:
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


def last_written_image():
    """
    Returns the last written image files

    Returns
    -------
    files : list
        a list of the image files
    """
    path = os.path.join(params.IMAGE_PATH + nightStarting())
    newest = max(glob.iglob(os.path.join(path, '*.fits')), key=os.path.getctime)
    root = newest.split('_UT')[0]

    fnames = {key: root+'_UT{}.fits'.format(key) for key in params.TEL_DICT.keys()}
    return {key: os.path.join(path, fnames[key]) for key in params.TEL_DICT.keys()}


def exposure_queue_is_empty():
    """Check if the image queue is empty"""
    EXQ_DAEMON_ADDRESS = params.DAEMONS['exq']['ADDRESS']
    with Pyro4.Proxy(EXQ_DAEMON_ADDRESS) as exq:
        exq._pyroTimeout = params.PROXY_TIMEOUT
        exq_info = exq.get_info()
    return exq_info['queue_length'] == 0


def filters_are_homed():
    """Check if all the filter wheels are homed"""
    FILT_DAEMON_ADDRESS = params.DAEMONS['filt']['ADDRESS']
    with Pyro4.Proxy(FILT_DAEMON_ADDRESS) as filt:
        filt._pyroTimeout = params.PROXY_TIMEOUT
        filt_info = filt.get_info()
    return all([filt_info[key] for key in filt_info if key.startswith('homed')])


def cameras_are_cool():
    """Check if all the cameras are below the target temperature"""
    target_temp = params.CCD_TEMP
    CAM_DAEMON_ADDRESS = params.DAEMONS['cam']['ADDRESS']
    with Pyro4.Proxy(CAM_DAEMON_ADDRESS) as cam:
        cam._pyroTimeout = params.PROXY_TIMEOUT
        cam_info = cam.get_info()
    return all([cam_info[key] < target_temp + 0.1
                for key in cam_info
                if key.startswith('ccd_temp')])


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
