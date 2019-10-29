"""Observing utilities."""

import glob
import os
import time
import warnings

from astropy.io import fits
from astropy.time import Time

import numpy as np

from obsdb import get_pointing_by_id, open_session

from . import params
from .astronomy import check_alt_limit
from .daemons import daemon_function, daemon_info
from .misc import execute_command


def check_schedule():
    """Check the schedule."""
    try:
        new_pointing = daemon_function('scheduler', 'check_queue')
        if new_pointing is not None:
            return new_pointing.db_id, new_pointing.mintime
        else:
            return None, None
    except Exception as error:
        print('{} checking scheduler: {}'.format(type(error).__name__, error))
        return None, None


def check_dome_closed():
    """Check the dome, returns True if the dome is closed or False if it's open."""
    dome_info = daemon_info('dome')
    return dome_info['dome'] == 'closed'


def wait_for_dome(target_position, timeout=None):
    """Wait until the dome has reached the target position.

    Parameters
    ----------
    target_position : 'open' or 'closed'
        the final position the dome should be in
    timeout : float
        time in seconds after which to timeout, None to wait forever

    """
    start_time = time.time()
    reached_position = False
    timed_out = False
    while not reached_position and not timed_out:
        time.sleep(0.2)

        try:
            dome_info = daemon_info('dome', force_update=True)

            done = [dome_info['dome'] == target_position.lower() and
                    dome_info['north'] == target_position.lower() and
                    dome_info['south'] == target_position.lower()]
            if np.all(done):
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Dome timed out')


def get_cam_temps():
    """Get a dict of camera temps."""
    cam_info = daemon_info('cam')
    values = {}
    for ut in params.UTS_WITH_CAMERAS:
        values[ut] = cam_info[ut]['ccd_temp']
    return values


def prepare_for_images():
    """Make sure the hardware is set up for taking images.

    - ensure the exposure queue is empty
    - ensure the filter wheels are homed
    - ensure the cameras are at operating temperature
    """
    # Empty the exposure queue
    if not exposure_queue_is_empty():
        execute_command('exq clear')
        while not exposure_queue_is_empty():
            time.sleep(0.5)

    # Home the filter wheels
    if not filters_are_homed():
        execute_command('filt home')
        while not filters_are_homed():
            time.sleep(0.5)

    # Bring the CCDs down to temperature
    if not cameras_are_cool():
        execute_command('cam temp {}'.format(params.CCD_TEMP))
        while not cameras_are_cool():
            time.sleep(0.5)


def set_new_focus(values):
    """Move each unit telescope to the requested focus.

    Parameters
    ----------
    values : float, dict
        a dictionary of unit telescope IDs and focus values

    """
    try:
        # will raise if not a dict (which is why .keys() is there), or if keys not valid
        assert all(ut in params.UTS_WITH_FOCUSERS for ut in values.keys())
    except Exception:
        # same value for all
        values = {ut: values for ut in params.UTS_WITH_FOCUSERS}

    for ut in params.UTS_WITH_FOCUSERS:
        execute_command('foc set {} {}'.format(ut, int(values[ut])))


def get_current_focus():
    """Find the current focus positions."""
    foc_info = daemon_info('foc')
    values = {}
    for ut in params.UTS_WITH_FOCUSERS:
        values[ut] = foc_info[ut]['current_pos']
    return values


def wait_for_focuser(target_values, timeout=None):
    """Wait until focuser has reached the target position.

    Parameters
    ----------
    target_values : float, dict
        a dictionary of unit telescope IDs and focus values
        (see `gtecs.observing.set_new_focus`)
    timeout : float
        time in seconds after which to timeout, None to wait forever

    """
    try:
        # will raise if not a dict (which is why .keys() is there), or if keys not valid
        assert all(ut in params.UTS_WITH_FOCUSERS for ut in target_values.keys())
    except Exception:
        # same value for all
        target_values = {ut: target_values for ut in params.UTS_WITH_FOCUSERS}

    start_time = time.time()
    reached_position = False
    timed_out = False
    while not reached_position and not timed_out:
        time.sleep(0.2)

        try:
            foc_info = daemon_info('foc', force_update=True)

            done = [(foc_info[ut]['current_pos'] == int(target_values[ut]) and
                    foc_info[ut]['status'] == 'Ready')
                    for ut in target_values.keys()]
            if np.all(done):
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Focuser timed out')


def get_current_mount_position():
    """Find the current mount position.

    Returns
    -------
    ra : float
        J2000 ra in decimal degrees
    dec : float
        J2000 dec in decimal degrees

    """
    mnt_info = daemon_info('mnt')
    ra = mnt_info['mount_ra']
    ra = ra * 360 / 24.  # mount uses RA in hours
    dec = mnt_info['mount_dec']
    return ra, dec


def slew_to_radec(ra, dec):
    """Move mount to given RA/Dec.

    Parameters
    ----------
    ra : float
        J2000 ra in decimal degrees
    dec : float
        J2000 dec in decimal degrees

    """
    # Check alt limit
    if check_alt_limit(ra, dec, Time.now()):
        raise ValueError('target too low, cannot set target')

    # Stop any current slews
    mnt_info = daemon_info('mnt')
    if mnt_info['status'] == 'Slewing':
        execute_command('mnt stop')

    # Slew
    execute_command('mnt slew {} {}'.format(ra, dec))


def slew_to_altaz(alt, az):
    """Move mount to given Alt/Az.

    Parameters
    ----------
    alt : float
        altitude in decimal degrees
    az : float
        azimuth in decimal degrees

    """
    # Check alt limit
    if alt < params.MIN_ELEVATION:
        raise ValueError('target too low, cannot set target')

    # Stop any current slews
    mnt_info = daemon_info('mnt')
    if mnt_info['status'] == 'Slewing':
        execute_command('mnt stop')

    # Slew
    execute_command('mnt slew_altaz ' + str(alt) + ' ' + str(az))


def wait_for_mount(target_ra, target_dec,
                   timeout=None, targ_dist=0.003):
    """Wait for mount to be in target position.

    Parameters
    ----------
    target_ra : float
        target J2000 ra in decimal degrees
    target_dec : float
        target J2000 dec in decimal degrees
    timeout : float
        time in seconds after which to timeout, None to wait forever
    targ_dist : float
        distance in degrees from the target to consider returning after
        default is 0.003 degrees

    """
    start_time = time.time()
    reached_position = False
    timed_out = False
    while not reached_position and not timed_out:
        time.sleep(0.5)

        try:
            mnt_info = daemon_info('mnt', force_update=True)

            done = (mnt_info['status'] == 'Tracking' and
                    np.isclose(mnt_info['target_ra'] * 360 / 24, target_ra, atol=0.0001) and
                    np.isclose(mnt_info['target_dec'], target_dec, atol=0.0001) and
                    mnt_info['target_dist'] < targ_dist)
            if done:
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Mount timed out')


def wait_for_mount_parking(timeout=None):
    """Wait for mount to be parked.

    Parameters
    ----------
    timeout : float
        time in seconds after which to timeout, None to wait forever

    """
    start_time = time.time()
    reached_position = False
    timed_out = False
    while not reached_position and not timed_out:
        time.sleep(0.5)

        try:
            mnt_info = daemon_info('mnt', force_update=True)

            done = mnt_info['status'] == 'Parked'
            if done:
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Mount timed out')


def random_offset(offset_size):
    """Make a random offset of the given size.

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
    """Make a offset in the given direction and of the given size.

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


def get_analysis_image(exptime, filt, name, imgtype='SCIENCE', glance=False):
    """Take a single exposure set, then open the images and return the image data.

    Parameters
    ----------
    exptime : int
        exposure time for the image
    filt : str
        filter to take the image in
    name : str
        target name
    imgtype : str, default 'SCIENCE'
        image type
    glance : bool, default `False`
        take a temporary glance image

    Returns
    -------
    data : dict
        a dictionary of the image data, with the UT numbers as keys

    """
    # Fund the current image count, so we know what to wait for
    img_num = get_current_image_count()

    if not glance:
        exq_command = 'exq image {:.1f} {} 1 "{}" {}'.format(exptime, filt, name, imgtype)
    else:
        exq_command = 'exq glance {:.1f} {} 1 "{}" {}'.format(exptime, filt, name, imgtype)
    execute_command(exq_command)
    execute_command('exq resume')  # just in case

    # wait for the camera daemon to finish saving the images
    wait_for_images(img_num + 1, exptime + 30)
    time.sleep(2)  # just in case

    data = get_latest_image_data(glance)

    return data


def get_latest_image_data(glance=False):
    """Open the most recent images and return the data.

    Parameters
    ----------
    glance : bool, default `False`
        read the glance images instead of the latest "normal" images

    Returns
    -------
    data : dict
        a dictionary of the image data, with the UT numbers as keys

    """
    if not glance:
        dirs = [d for d in list(glob.iglob(params.IMAGE_PATH + '*')) if os.path.isdir(d)]
        path = max(dirs, key=os.path.getctime)
        newest = max(glob.iglob(os.path.join(path, '*.fits')), key=os.path.getctime)
        root = newest.split('_UT')[0]

        print('Loading run {}:'.format(root.split('/')[-1]), end=' ')
    else:
        path = os.path.join(params.IMAGE_PATH)
        root = 'glance'

        print('Loading glances:', end=' ')

    # get possible file names
    filenames = {ut: '{}_UT{:d}.fits'.format(root, ut) for ut in params.UTS_WITH_CAMERAS}

    # get full path
    images = {ut: os.path.join(path, filenames[ut]) for ut in filenames}

    # limit it to only existing files
    images = {ut: images[ut] for ut in images if os.path.exists(images[ut])}

    print('{} images'.format(len(images)))

    data = {}
    for ut in images.keys():
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                data[ut] = fits.getdata(images[ut]).astype('float')
        except (TypeError, OSError):
            # Image was still being written, wait a sec and try again
            time.sleep(1)
            data[ut] = fits.getdata(images[ut]).astype('float')

    return data


def take_image_set(exptime, filt, name, imgtype='SCIENCE'):
    """Take a set of images and waits for the exposure queue to finish.

    Parameters
    ----------
    exptime : int or list of int
        exposure time(s) for the images
    filt : str or list of str
        filter(s) to take the images in
    name : str
        target name
    imgtype : str, default 'SCIENCE'
        image type

    """
    if not isinstance(exptime, list):
        exptime = [exptime]
    exp_list = exptime

    if not isinstance(filt, list):
        filt = [filt]
    filt_list = filt

    for filt in filt_list:
        for exptime in exp_list:
            exq_command = 'exq image {} {} 1 "{}" {}'.format(exptime, filt, name, imgtype)
            execute_command(exq_command)
    execute_command('exq resume')  # just in case

    # estimate a deliberately pessimistic timeout
    readout = 30 * len(exp_list) * len(filt_list)
    total_exp = sum(exp_list) * len(filt_list)
    total_time = 1.5 * (readout + total_exp)
    wait_for_exposure_queue(total_time)


def exposure_queue_is_empty():
    """Check if the image queue is empty."""
    exq_info = daemon_info('exq', force_update=False)
    return exq_info['queue_length'] == 0


def filters_are_homed():
    """Check if all the filter wheels are homed."""
    filt_info = daemon_info('filt', force_update=False)
    return all(filt_info[ut]['homed'] for ut in params.UTS_WITH_FILTERWHEELS)


def cameras_are_cool():
    """Check if all the cameras are below the target temperature."""
    target_temp = params.CCD_TEMP
    cam_info = daemon_info('cam', force_update=False)
    return all(cam_info[ut]['ccd_temp'] < target_temp + 0.1 for ut in params.UTS_WITH_CAMERAS)


def wait_for_exposure_queue(timeout=None):
    """With a set of exposures underway, wait for an empty queue.

    Parameters
    ----------
    timeout : float
        time in seconds after which to timeout, None to wait forever

    """
    start_time = time.time()
    finished = False
    timed_out = False
    while not finished and not timed_out:
        time.sleep(0.5)
        try:
            exq_info = daemon_info('exq', force_update=True)
            done = (exq_info['queue_length'] == 0 and
                    exq_info['exposing'] is False and
                    exq_info['status'] == 'Ready')
            if done:
                finished = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Exposure queue timed out')


def get_current_image_count():
    """Find the current camera image number."""
    cam_info = daemon_info('cam')
    return cam_info['num_taken']


def wait_for_images(target_image_number, timeout=None):
    """With a set of exposures underway, wait for the cameras to finish saving.

    Parameters
    ----------
    target_image_number : int
        camera image number to wait for
    timeout : float
        time in seconds after which to timeout. None to wait forever

    """
    start_time = time.time()
    finished = False
    timed_out = False
    while not finished and not timed_out:
        time.sleep(0.5)

        try:
            cam_info = daemon_info('cam', force_update=True)
            done = [(cam_info[ut]['status'] == 'Ready' and
                     int(cam_info['num_taken']) == int(target_image_number))
                    for ut in params.UTS_WITH_CAMERAS]
            if np.all(done):
                finished = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Cameras timed out')


def get_pointing_status(db_id):
    """Get the status of a paticular pointing.

    Parameters
    ----------
    db_id : int
        database ID of the pointing

    """
    with open_session() as session:
        pointing = get_pointing_by_id(session, db_id)
        status = pointing.status
    return status
