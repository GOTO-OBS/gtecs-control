"""Observing utilities."""

import time

from astropy.time import Time

from gtecs.common.system import execute_command

import numpy as np

from . import params
from .astronomy import radec_from_altaz, within_mount_limits
from .daemons import daemon_function, daemon_info
from .fits import clear_glance_files, get_glance_data, get_image_data


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


def dome_is_shielding():
    """Check if the dome is in windshield mode."""
    dome_info = daemon_info('dome')
    return dome_info['shielding']


def prepare_for_images(open_covers=True):
    """Make sure the hardware is set up for taking images.

    - ensure any lights in the dome are turned off
    - ensure the exposure queue is empty
    - ensure the filter wheels are homed
    - ensure the cameras are not windowed
    - ensure the cameras are at operating temperature
    - ensure the mount motors are on
    - ensure the mirror covers are open, unless `open_covers` is False (e.g. for darks)
    """
    # Turn off any sources of light in the dome
    if not outlets_are_off(params.OBSERVING_OFF_OUTLETS):
        print('Turning off dome lights')
        for outlet in params.OBSERVING_OFF_OUTLETS:
            execute_command('power off {}'.format(outlet))
            time.sleep(0.5)

    # Empty the exposure queue
    if not exposure_queue_is_empty():
        print('Clearing exposure queue')
        execute_command('exq clear')
        while not exposure_queue_is_empty():
            time.sleep(0.5)

    # Home the filter wheels
    if not filters_are_homed():
        print('Homing filters')
        execute_command('filt home')
        while not filters_are_homed():
            time.sleep(0.5)

    # Set the focusers
    if not focusers_are_set():
        print('Setting focusers')
        execute_command('foc move 10')
        time.sleep(4)
        execute_command('foc move -10')

    # Reset the cameras to full-frame exposures
    if not cameras_are_fullframe():
        print('Setting cameras to full-frame')
        execute_command('cam window full')
        time.sleep(4)

    # Bring the CCDs down to temperature
    if not cameras_are_cool():
        print('Cooling cameras')
        execute_command('cam temp {}'.format(params.CCD_TEMP))
        while not cameras_are_cool():
            time.sleep(0.5)

    # Start the mount motors (but remain parked, or in whatever position we're in)
    if not mount_motors_are_on():
        print('Turning on mount motors')
        execute_command('mnt motors on')
        time.sleep(4)

    if open_covers is True:
        # Open the mirror covers
        if not mirror_covers_are_open():
            print('Opening mirror covers')
            execute_command('ota open')
            while not mirror_covers_are_open():
                time.sleep(0.5)
    else:
        # Close the mirror covers (for darks etc...)
        if not mirror_covers_are_closed():
            print('Closing mirror covers')
            execute_command('ota close')
            while not mirror_covers_are_closed():
                time.sleep(0.5)


def get_mirror_cover_positions():
    """Find the current mirror cover positions."""
    ota_info = daemon_info('ota')
    positions = {}
    for ut in params.UTS_WITH_COVERS:
        positions[ut] = ota_info[ut]['position']
    return positions


def mirror_covers_are_open():
    """Return true if all of the covers are open."""
    positions = get_mirror_cover_positions()

    covers_open = [positions[ut] == 'full_open' for ut in positions]

    return np.all(covers_open)


def mirror_covers_are_closed():
    """Return true if all of the covers are closed."""
    positions = get_mirror_cover_positions()

    covers_closed = [positions[ut] == 'closed' for ut in positions]

    return np.all(covers_closed)


def wait_for_mirror_covers(opening=True, timeout=None):
    """Wait for mirror covers to be fully open or closed.

    Parameters
    ----------
    opening : bool
        if True wait for the covers to be open, if false wait until they are closed
    timeout : float
        time in seconds after which to timeout, None to wait forever

    """
    start_time = time.time()
    reached_position = False
    timed_out = False
    while not reached_position and not timed_out:
        time.sleep(0.5)

        try:
            ota_info = daemon_info('ota', force_update=True)
            positions = [ota_info[ut]['position'] for ut in params.UTS_WITH_COVERS]
            if opening is True and np.all(positions[ut] == 'full_open' for ut in positions):
                reached_position = True
            if opening is False and np.all(positions[ut] == 'closed' for ut in positions):
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Mirror covers timed out')


def get_focuser_positions(uts=None):
    """Find the current focuser positions."""
    if uts is None:
        uts = params.UTS_WITH_FOCUSERS
    foc_info = daemon_info('foc', force_update=True)
    positions = {ut: foc_info[ut]['current_pos'] for ut in uts}
    return positions


def get_focuser_limits(uts=None):
    """Find the maximum focuser position limit."""
    if uts is None:
        uts = params.UTS_WITH_FOCUSERS
    foc_info = daemon_info('foc', force_update=True)
    limits = {ut: foc_info[ut]['limit'] for ut in uts}
    return limits


def focusers_are_ready(uts=None):
    """Return true if none of the focusers are moving."""
    if uts is None:
        uts = params.UTS_WITH_FOCUSERS
    foc_info = daemon_info('foc', force_update=True)
    ready = [foc_info[ut]['status'] == 'Ready' for ut in uts]
    return np.all(ready)


def set_focuser_positions(positions, wait=False, timeout=None):
    """Move each focuser to the requested position.

    Parameters
    ----------
    positions : float, dict
        position to move to, or a dictionary of unit telescope IDs and positions

    wait: bool, default=False
        wait for the focusers to complete their move
    timeout : float, default=None
        time in seconds after which to timeout, None to wait forever
        if `wait` is False and a non-None timeout is given, still wait for that time

    """
    if not isinstance(positions, dict):
        positions = {ut: positions for ut in params.UTS_WITH_FOCUSERS}

    while not focusers_are_ready(uts=positions.keys()):
        time.sleep(0.5)

    ut_list = [str(int(ut)) for ut in sorted(positions.keys())]
    pos_list = [str(int(positions[int(ut)])) for ut in ut_list]
    execute_command('foc set {} {}'.format(','.join(ut_list), ','.join(pos_list)))

    if wait or timeout is not None:
        wait_for_focusers(positions, timeout)


def move_focusers(offsets, wait=False, timeout=None):
    """Move each focuser by the given number of steps.

    Parameters
    ----------
    offsets : float, dict
        offsets in steps to move by, or a dictionary of unit telescope IDs and offsets

    wait: bool, default=False
        wait for the focusers to complete their move
    timeout : float, default=None
        time in seconds after which to timeout, None to wait forever
        if `wait` is False and a non-None timeout is given, still wait for that time

    """
    if not isinstance(offsets, dict):
        offsets = {ut: offsets for ut in params.UTS_WITH_FOCUSERS}

    while not focusers_are_ready(uts=offsets.keys()):
        time.sleep(0.5)

    start_positions = get_focuser_positions()
    finish_positions = {ut: start_positions[ut] + offsets[ut] for ut in offsets}

    ut_list = [str(int(ut)) for ut in sorted(offsets.keys())]
    steps_list = [str(int(offsets[int(ut)])) for ut in ut_list]
    execute_command('foc move {} {}'.format(','.join(ut_list), ','.join(steps_list)))

    if wait or timeout is not None:
        wait_for_focusers(finish_positions, timeout)


def wait_for_focusers(target_positions, timeout=None):
    """Wait until focuser has reached the target position.

    Parameters
    ----------
    target_positions : float, dict
        target position, or a dictionary of unit telescope IDs and positions

    timeout : float, default=None
        time in seconds after which to timeout, None to wait forever

    """
    if not isinstance(target_positions, dict):
        target_positions = {ut: target_positions for ut in params.UTS_WITH_FOCUSERS}

    start_time = time.time()
    reached_position = False
    timed_out = False
    while not reached_position and not timed_out:
        time.sleep(0.2)

        try:
            foc_info = daemon_info('foc', force_update=True)

            # Note we say we're there when we're within 5 steps,
            # because the ASA auto-adjustment means we can't be exact.
            done = [abs(foc_info[ut]['current_pos'] - int(target_positions[ut])) < 5 and
                    foc_info[ut]['status'] == 'Ready'
                    for ut in target_positions]
            if np.all(done):
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Focuser timed out')


def get_focuser_temperatures():
    """Get the current temperature and the temperature when the focusers last moved."""
    foc_info = daemon_info('foc')
    curr_temp = {ut: foc_info[ut]['current_temp'] for ut in params.UTS_WITH_FOCUSERS}
    prev_temp = {ut: foc_info[ut]['last_move_temp'] for ut in params.UTS_WITH_FOCUSERS}
    return curr_temp, prev_temp


def get_mount_position():
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
    ra = ra * 360 / 24  # mount uses RA in hours
    dec = mnt_info['mount_dec']
    return ra, dec


def mount_is_parked():
    """Check if the mount motors are enabled."""
    mnt_info = daemon_info('mnt', force_update=False)
    return mnt_info['status'] == 'parked'


def mount_motors_are_on():
    """Check if the mount motors are enabled."""
    mnt_info = daemon_info('mnt', force_update=False)
    return mnt_info['motors_on']


def slew_to_radec(ra, dec, wait=False, timeout=None):
    """Move mount to given RA/Dec.

    Parameters
    ----------
    ra : float
        J2000 ra in decimal degrees
    dec : float
        J2000 dec in decimal degrees

    wait: bool, default=False
        wait for the mount to complete the move
    timeout : float, default=None
        time in seconds after which to timeout, None to wait forever
        if `wait` is False and a non-None timeout is given, still wait for that time

    """
    if not within_mount_limits(ra, dec, Time.now()):
        raise ValueError('Target is outside of mount limits, cannot slew')

    mnt_info = daemon_info('mnt')
    if mnt_info['status'] == 'Slewing':
        execute_command('mnt stop')
        time.sleep(2)

    execute_command('mnt slew {} {}'.format(ra, dec))

    if wait or timeout is not None:
        wait_for_mount(ra, dec, timeout)


def wait_for_mount(target_ra, target_dec, timeout=None, targ_dist=30):
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
        distance in arcseconds from the target to consider returning after
        default is 30 arcsec

    """
    start_time = time.time()
    reached_position = False
    timed_out = False
    while not reached_position and not timed_out:
        time.sleep(0.5)

        try:
            mnt_info = daemon_info('mnt', force_update=True)

            tolerance = targ_dist / (60 * 60)
            done = (mnt_info['status'] == 'Tracking' and
                    np.isclose(mnt_info['target_ra'] * 360 / 24, target_ra, tolerance) and
                    np.isclose(mnt_info['target_dec'], target_dec, tolerance) and
                    mnt_info['target_dist'] < tolerance)
            if done:
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Mount timed out')


def slew_to_altaz(alt, az, wait=False, timeout=None):
    """Move mount to given Alt/Az.

    Parameters
    ----------
    alt : float
        altitude in decimal degrees
    az : float
        azimuth in decimal degrees

    wait: bool, default=False
        wait for the mount to complete the move
    timeout : float, default=None
        time in seconds after which to timeout, None to wait forever
        if `wait` is False and a non-None timeout is given, still wait for that time

    """
    ra, dec = radec_from_altaz(alt, az, Time.now())
    print('Converting alt={}, az={} to ra/dec'.format(alt, az))
    slew_to_radec(ra, dec, wait=wait, timeout=timeout)


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

            done = mnt_info['status'] in ['Parked', 'IN BLINKY MODE', 'MOTORS OFF']
            if done:
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Mount timed out')


def random_offset(distance):
    """Make an offset of the given distance in a random direction.

    Parameters
    ----------
    distance : float
        offset distance in arcseconds

    """
    direction = np.random.choice(['N', 'E', 'S', 'W'])
    execute_command('mnt offset {} {}'.format(direction, distance))
    # wait a short while for it to move
    time.sleep(2)


def offset(direction, distance):
    """Make a offset of the given distance in the given direction .

    Parameters
    ----------
    direction : string
        compass direction to move, one of ['N', 'E', 'S', 'W']
    distance : float
        offset distance in arcseconds

    """
    execute_command('mnt offset {} {}'.format(direction.upper(), distance))
    # wait a short while for it to move
    time.sleep(2)


def get_analysis_image(exptime, filt, binning, name, imgtype='SCIENCE', glance=False, uts=None,
                       get_headers=False):
    """Take a single exposure set, then open the images and return the image data.

    Parameters
    ----------
    exptime : int
        exposure time for the image
    filt : str
        filter to take the image in
    binning : int
        binning factor to take the image with
    name : str
        target name
    imgtype : str, default='SCIENCE'
        image type
    glance : bool, default=`False`
        take a temporary glance image
    uts : list of ints, default=`None`
        if given, the UTs to take the exposures with
        uts=`None` (the default) will take images on all UTs
    get_headers : bool, default=False
        return the image headers instead of the full data arrays (much faster)

    Returns
    -------
    data : dict
        a dictionary of the image data or headers, with the UT numbers as keys

    """
    # Find the current image count, so we know what to wait for
    img_num = get_image_count()

    # Create the command string
    if uts is not None:
        uts = [str(int(ut)) for ut in uts if ut in params.UTS_WITH_CAMERAS]
        if len(uts) == 0:
            raise ValueError('Invalid UT values (not in {})'.format(params.UTS_WITH_CAMERAS))
        ut_string = ','.join(uts)
        exq_command = 'exq {} {} {:.1f} {} {} "{}" {}'.format('image' if not glance else 'glance',
                                                              ut_string,
                                                              exptime,
                                                              filt,
                                                              binning,
                                                              name,
                                                              imgtype if not glance else '')
    else:
        exq_command = 'exq {} {:.1f} {} {} "{}" {}'.format('image' if not glance else 'glance',
                                                           exptime,
                                                           filt,
                                                           binning,
                                                           name,
                                                           imgtype if not glance else '')

    # Remove old glance files (so we know what to wait for)
    if glance:
        clear_glance_files()

    # Send the command
    execute_command(exq_command)
    execute_command('exq resume')

    # Wait for the camera daemon to finish saving the images
    wait_for_images(img_num + 1, exptime + 60)
    time.sleep(2)

    if get_headers:
        return daemon_function('cam', 'get_latest_headers')

    # Fetch the data
    if not glance:
        # Get the run number, it should be safer than the last modified which has messed up before
        # (perhaps due to the Warwick archiver?)
        run_number = get_run_number()
        data = get_image_data(run_number=run_number, timeout=60)
    else:
        data = get_glance_data(timeout=60)

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


def outlets_are_off(outlets):
    """Check if the given power outlets are off."""
    power_info = daemon_info('power', force_update=False)
    all_status = {outlet: power_info[unit][outlet]
                  for unit in power_info if 'status' in unit
                  for outlet in power_info[unit]}
    return all([all_status[outlet] == 'off' for outlet in outlets if outlet in all_status])


def exposure_queue_is_empty():
    """Check if the image queue is empty."""
    exq_info = daemon_info('exq', force_update=False)
    return exq_info['queue_length'] == 0


def filters_are_homed():
    """Check if all the filter wheels are homed."""
    filt_info = daemon_info('filt', force_update=False)
    return all(filt_info[ut]['homed'] for ut in params.UTS_WITH_FILTERWHEELS)


def focusers_are_set():
    """Check if all the focusers are set."""
    foc_info = daemon_info('foc', force_update=False)
    return all(foc_info[ut]['status'] != 'UNSET' for ut in params.UTS_WITH_FOCUSERS)


def cameras_are_cool(target_temp):
    """Check if all the cameras are below the target temperature."""
    cam_info = daemon_info('cam', force_update=False)
    return all(cam_info[ut]['ccd_temp'] < target_temp + 0.1 for ut in params.UTS_WITH_CAMERAS)


def cameras_are_fullframe():
    """Check if all the cameras are set to take full-frame exposures."""
    cam_info = daemon_info('cam', force_update=False)
    return all(cam_info[ut]['window_area'] == cam_info[ut]['full_area']
               for ut in params.UTS_WITH_CAMERAS)


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


def get_image_count():
    """Find the current camera image number."""
    cam_info = daemon_info('cam')
    return cam_info['num_taken']


def get_run_number():
    """Find the latest exposure run number."""
    cam_info = daemon_info('cam')
    return cam_info['latest_run_number']


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


def get_conditions(timeout=30):
    """Get the current conditions values."""
    conditions_info = daemon_info('conditions', force_update=False, timeout=timeout)
    return conditions_info['weather']


def get_internal_conditions(timeout=30):
    """Get the current internal conditions (temperature and humidity).

    If there are more than one internal sensors then this function
    returns the mean temperature and humidity values.

    """
    conditions_info = daemon_info('conditions', force_update=False, timeout=timeout)
    weather = conditions_info['weather']
    int_sources = [source for source in weather if weather[source]['type'] == 'internal']
    int_temperature = np.mean([weather[source]['temperature'] for source in int_sources])
    int_humidity = np.mean([weather[source]['humidity'] for source in int_sources])
    return {'temperature': int_temperature, 'humidity': int_humidity}
