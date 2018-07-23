"""
Observing utilities
"""

import os
import time
import Pyro4
import glob

import numpy as np

from astropy.time import Time

from obsdb import open_session, get_pointing_by_id

from . import params
from .astronomy import tel_str, check_alt_limit, nightStarting
from .misc import execute_command
from .daemons import daemon_function, daemon_info


def check_schedule(time, write_html):
    """
    Check the schedule
    """
    try:
        new_pointing = daemon_function('scheduler', 'check_queue', [time, write_html])
        if new_pointing is not None:
            return new_pointing.id, new_pointing.priority_now, new_pointing.mintime
        else:
            return None, None, None
    except Exception as error:
        print('{} checking scheduler: {}'.format(type(error).__name__, error))
        return None, None, None


def check_dome_closed():
    """
    Check the dome, returns True if the dome is closed or False if it's open
    """
    dome_info = daemon_info('dome')
    return dome_info['dome'] == 'closed'


def get_cam_temps():
    """
    Get a dict of camera temps
    """
    cam_info = daemon_info('cam')
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
        execute_command('exq pause')
        time.sleep(1)
        execute_command('exq clear')
        while not exposure_queue_is_empty():
            time.sleep(1)
    execute_command('exq resume')

    # Home the filter wheels
    if not filters_are_homed():
        execute_command('filt home')
        while not filters_are_homed():
            time.sleep(1)

    # Bring the CCDs down to temperature
    if not cameras_are_cool():
        execute_command('cam temp {}'.format(params.CCD_TEMP))
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

    for tel in sorted(params.TEL_DICT):
        execute_command('foc set {} {}'.format(tel, int(values[tel])))


def get_current_focus():
    """
    Find the current focus positions
    """
    foc_info = daemon_info('foc')
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
    start_time = time.time()
    still_moving = True
    timed_out = False
    status_keys = ['status{}'.format(tel) for tel in params.TEL_DICT]
    while still_moving and not timed_out:
        try:
            foc_info = daemon_info('foc')
        except:
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
    execute_command("mnt ra " + ra_string)
    execute_command("mnt dec " + dec_string)
    time.sleep(1)
    execute_command("mnt slew")


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
    execute_command('mnt slew_altaz ' + str(alt) + ' ' + str(az))


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
    still_moving = True
    timed_out = False
    while still_moving and not timed_out:
        try:
            mnt_info = daemon_info('mnt')
        except:
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
    execute_command("mnt step {}".format(offset_size))
    time.sleep(0.2)
    execute_command("mnt {}".format(dirn))
    # wait a short while for it to move
    time.sleep(2)


def offset(direction, size):
    """
    Make a offset in the given direction and of the given size

    Parameters
    ----------
    direction : string
        compass direction to move, one of ['n', 's', 'e', 'w']
    size : float
        offset size in arcseconds
    """
    execute_command("mnt {} {}".format(direction, size))
    # wait a short while for it to move
    time.sleep(2)


def take_image_set(expT, filt, name, imgtype='SCIENCE'):
    """
    Takes a set of images and waits for the exposure queue to finish.

    Parameters
    ----------
    expT : int or list of int
        exposure time(s) for the images
    filt : str or list of str
        filter(s) to take the images in
    name : str
        target name
    imgtype : str, default 'SCIENCE'
        image type
    """
    if not isinstance(expT, list):
        expT = [expT]
    exp_list = expT

    if not isinstance(filt, list):
        filt = [filt]
    filt_list = filt

    for filt in filt_list:
        for expT in exp_list:
            exq_command = 'exq image {} {} 1 "{}" {}'.format(expT, filt, name, imgtype)
            execute_command(exq_command)
    execute_command('exq resume')  # just in case

    # estimate a deliberately pessimistic timeout
    readout = 30*len(exp_list)*len(filt_list)
    total_exp = sum(exp_list)*len(filt_list)
    total_time = 1.5*(readout + total_exp)
    wait_for_exposure_queue(total_time)


def get_latest_images():
    """
    Returns the last written image files

    Returns
    -------
    files : dict
        a dictionary of the image files, with the UT numbers as keys
    """
    path = os.path.join(params.IMAGE_PATH + nightStarting())
    newest = max(glob.iglob(os.path.join(path, '*.fits')), key=os.path.getctime)
    root = newest.split('_UT')[0]

    fnames = {key: root+'_UT{}.fits'.format(key) for key in params.TEL_DICT.keys()}

    print('Loading run {}: {} images'.format(root.split('/')[-1], len(fnames)))
    return {key: os.path.join(path, fnames[key]) for key in params.TEL_DICT.keys()}


def get_glances():
    """
    Returns the last written glance files

    Returns
    -------
    files : dict
        a dictionary of the image files, with the UT numbers as keys
    """
    path = os.path.join(params.IMAGE_PATH)
    root = 'glance'

    fnames = {key: root+'_UT{}.fits'.format(key) for key in params.TEL_DICT.keys()}

    print('Loading glances: {} images'.format(len(fnames)))
    return {key: os.path.join(path, fnames[key]) for key in params.TEL_DICT.keys()}


def exposure_queue_is_empty():
    """Check if the image queue is empty"""
    exq_info = daemon_info('exq')
    return exq_info['queue_length'] == 0


def filters_are_homed():
    """Check if all the filter wheels are homed"""
    filt_info = daemon_info('filt')
    return all([filt_info[key] for key in filt_info if key.startswith('homed')])


def cameras_are_cool():
    """Check if all the cameras are below the target temperature"""
    target_temp = params.CCD_TEMP
    cam_info = daemon_info('cam')
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
    start_time = time.time()
    still_working = True
    timed_out = False
    while still_working and not timed_out:
        time.sleep(10)
        try:
            exq_info = daemon_info('exq')

            nexp = exq_info['queue_length']
            status = exq_info['status']
            if nexp == 0 and status == 'Ready':
                still_working = False
        except:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True
    if timed_out:
        raise TimeoutError('Exposure queue timed out')


def get_pointing_status(pointingID):
    """
    Get the status of a paticular pointing

    Parameters
    ----------
    pointingID : int
        database ID of the pointing (aka job ID in the pilot)
    """
    with open_session() as session:
        pointing = get_pointing_by_id(session, pointingID)
        status = pointing.status
    return status
