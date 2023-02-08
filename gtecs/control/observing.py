"""Observing utilities."""

import time

from astropy.time import Time

import numpy as np

from . import params
from .astronomy import radec_from_altaz, within_mount_limits
from .daemons import daemon_proxy
from .fits import clear_glance_files, get_glance_data, get_image_data


def check_dome_closed():
    """Check the dome, returns True if the dome is closed or False if it's open."""
    with daemon_proxy('dome') as daemon:
        info = daemon.get_info(force_update=True)
    return info['dome'] == 'closed'


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
            with daemon_proxy('dome') as daemon:
                info = daemon.get_info(force_update=True)

            done = [info['dome'] == target_position.lower() and
                    info['north'] == target_position.lower() and
                    info['south'] == target_position.lower()]
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
    with daemon_proxy('dome') as daemon:
        info = daemon.get_info(force_update=True)
    return info['shielding']


def prepare_for_images(open_covers=True):
    """Make sure the hardware is set up for taking images.

    - ensure any lights in the dome are turned off
    - ensure the exposure queue is empty
    - ensure the filter wheels are homed
    - ensure the cameras are not exposing and have no images to read out
    - ensure the cameras are not windowed
    - ensure the cameras are at operating temperature
    - ensure the mount motors are on
    - ensure the mirror covers are open, unless `open_covers` is False (e.g. for darks)
    """
    # Turn off any sources of light in the dome
    if not outlets_are_off(params.OBSERVING_OFF_OUTLETS):
        print('Turning off dome lights')
        for outlet in params.OBSERVING_OFF_OUTLETS:
            with daemon_proxy('power') as daemon:
                daemon.off(outlet)
            time.sleep(0.5)

    # Empty the exposure queue
    if not exposure_queue_is_empty():
        print('Clearing exposure queue')
        with daemon_proxy('exq') as daemon:
            daemon.clear()
        start_time = time.time()
        while not exposure_queue_is_empty():
            time.sleep(0.5)
            if (time.time() - start_time) > 30:
                raise TimeoutError('Exposure queue timed out')

    # Home the filter wheels
    if not filters_are_homed():
        print('Homing filters')
        with daemon_proxy('filt') as daemon:
            daemon.home_filters()
        start_time = time.time()
        while not filters_are_homed():
            time.sleep(0.5)
            if (time.time() - start_time) > 30:
                raise TimeoutError('Filter wheels timed out')

    # Set the focusers
    if not focusers_are_set():
        print('Setting focusers')
        with daemon_proxy('foc') as daemon:
            daemon.move_focusers(10)
            time.sleep(4)
            daemon.move_focusers(-10)
        start_time = time.time()
        while not focusers_are_set():
            time.sleep(0.5)
            if (time.time() - start_time) > 60:
                raise TimeoutError('Focusers timed out')

    # Make sure the cameras aren't exposing
    if not cameras_are_empty():
        print('Aborting ongoing exposures')
        with daemon_proxy('cam') as daemon:
            daemon.abort_exposure()
            time.sleep(3)
            daemon.clear_queue()
        start_time = time.time()
        while not cameras_are_empty():
            time.sleep(0.5)
            if (time.time() - start_time) > 30:
                raise TimeoutError('Cameras timed out')

    # Reset the cameras to full-frame exposures
    if not cameras_are_fullframe():
        print('Setting cameras to full-frame')
        with daemon_proxy('cam') as daemon:
            daemon.remove_window()
        time.sleep(4)

    # Bring the CCDs down to temperature
    if not cameras_are_cool():
        print('Cooling cameras')
        with daemon_proxy('cam') as daemon:
            daemon.set_temperature('cool')
        start_time = time.time()
        while not cameras_are_cool():
            time.sleep(0.5)
            if (time.time() - start_time) > 600:
                raise TimeoutError('Camera cooling timed out')

    # Start the mount motors (but remain parked, or in whatever position we're in)
    if not mount_motors_are_on():
        print('Turning on mount motors')
        with daemon_proxy('mnt') as daemon:
            daemon.power_motors('on')
        time.sleep(4)

    if open_covers is True:
        # Open the mirror covers
        if not mirror_covers_are_open():
            print('Opening mirror covers')
            with daemon_proxy('ota') as daemon:
                daemon.open_covers()
            start_time = time.time()
            while not mirror_covers_are_open():
                time.sleep(0.5)
                if (time.time() - start_time) > 60:
                    raise TimeoutError('Mirror covers timed out')
    else:
        # Close the mirror covers (for darks etc...)
        if not mirror_covers_are_closed():
            print('Closing mirror covers')
            with daemon_proxy('ota') as daemon:
                daemon.close_covers()
            start_time = time.time()
            while not mirror_covers_are_closed():
                time.sleep(0.5)
                if (time.time() - start_time) > 60:
                    raise TimeoutError('Mirror covers timed out')


def get_mirror_cover_positions():
    """Find the current mirror cover positions."""
    with daemon_proxy('ota') as daemon:
        info = daemon.get_info(force_update=True)
    positions = {}
    for ut in info['uts_with_covers']:
        positions[ut] = info[ut]['position']
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
            if opening and mirror_covers_are_open():
                reached_position = True
            if not opening and mirror_covers_are_closed():
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Mirror covers timed out')


def get_focuser_positions(uts=None):
    """Find the current focuser positions."""
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    if uts is None:
        uts = info['uts']
    positions = {ut: info[ut]['current_pos'] for ut in uts}
    return positions


def get_focuser_limits(uts=None):
    """Find the maximum focuser position limit."""
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    if uts is None:
        uts = info['uts']
    limits = {ut: info[ut]['limit'] for ut in uts}
    return limits


def focusers_are_ready(uts=None):
    """Return true if none of the focusers are moving."""
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    if uts is None:
        uts = info['uts']
    ready = [info[ut]['status'] == 'Ready' for ut in uts]
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

    print('Setting focusers:', positions)
    with daemon_proxy('foc') as daemon:
        daemon.set_focusers(positions)

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

    print('Moving focusers:', offsets)
    with daemon_proxy('foc') as daemon:
        daemon.move_focusers(offsets)

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
            with daemon_proxy('foc') as daemon:
                info = daemon.get_info(force_update=True)

            # Note we say we're there when we're within 5 steps,
            # because the ASA auto-adjustment means we can't be exact.
            done = [abs(info[ut]['current_pos'] - int(target_positions[ut])) < 5 and
                    info[ut]['status'] == 'Ready'
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
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    curr_temp = {ut: info[ut]['current_temp'] for ut in info['uts']}
    prev_temp = {ut: info[ut]['last_move_temp'] for ut in info['uts']}
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
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
    ra = info['mount_ra']
    ra = ra * 360 / 24  # mount uses RA in hours
    dec = info['mount_dec']
    return ra, dec


def mount_is_parked():
    """Check if the mount motors are enabled."""
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
    return info['status'] == 'Parked'


def mount_motors_are_on():
    """Check if the mount motors are enabled."""
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
    return info['motors_on']


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

    print('Slewing to {ra:.2f} {dec:.2f}')
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
        if info['status'] == 'Slewing':
            daemon.full_stop()
            time.sleep(2)
            daemon.slew_to_radec(ra * 24 / 360, dec)  # TODO: really should use SkyCoord

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
            with daemon_proxy('mnt') as daemon:
                info = daemon.get_info(force_update=True)

            tolerance = targ_dist / (60 * 60)
            done = (info['status'] == 'Tracking' and
                    np.isclose(info['target_ra'] * 360 / 24, target_ra, tolerance) and
                    np.isclose(info['target_dec'], target_dec, tolerance) and
                    info['target_dist'] < tolerance)
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
            with daemon_proxy('mnt') as daemon:
                info = daemon.get_info(force_update=True)

            done = info['status'] in ['Parked', 'IN BLINKY MODE', 'MOTORS OFF']
            if done:
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Mount timed out')


def offset(direction, distance):
    """Offset the mount by the given distance in the given direction.

    Parameters
    ----------
    direction : string
        compass direction to move, one of ['N', 'E', 'S', 'W']
    distance : float
        offset distance in arcseconds

    """
    print(f'Offsetting mount {distance:.1f} deg {direction}')
    with daemon_proxy('mnt') as daemon:
        daemon.offset(direction.upper(), distance)
    # wait a short while for it to move
    time.sleep(2)


def get_analysis_image(exptime, filt, binning, name, imgtype='SCIENCE', glance=False, uts=None,
                       get_data=True, get_headers=False):
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
    get_data : bool, default=True
        return the image data arrays (takes time)
    get_headers : bool, default=False
        return the image headers instead of the full data arrays (much faster)

    Returns
    -------
    data : dict
        a dictionary of the image data or headers, with the UT numbers as keys
        if both get_data and get_headers are False then just return the run number and image number
        if both are True return a tuple (data_dict, header_dict)

    """
    if uts is not None:
        uts = [int(ut) for ut in uts if ut in params.UTS_WITH_CAMERAS]
        if len(uts) == 0:
            raise ValueError('Invalid UT values (not in {})'.format(params.UTS_WITH_CAMERAS))
    else:
        uts = params.UTS_WITH_CAMERAS

    # Find the current image count, so we know what to wait for
    img_num = get_image_count() + 1

    # Remove old glance files (so we know what to wait for)
    if glance:
        clear_glance_files()

    # Send the command
    with daemon_proxy('exq') as daemon:
        print(f'Taking {exptime:.0f}s {filt} {"exposure" if not glance else "glance"}')
        daemon.add(uts, exptime, 1, filt, binning,
                   target=name, imgtype=imgtype, glance=glance)
        daemon.resume()

    if not get_data and not get_headers:
        # Wait for exposures to finish, but not to save
        wait_for_readout(img_num, exptime + 60)

        # Just return the run and images numbers to do something else with
        if not glance:
            return get_run_number(), img_num
        else:
            return 'glance', img_num

    # Otherwise we need to wait for the images to be saved
    wait_for_images(img_num, exptime + 60)

    if get_data:
        if not glance:
            # Use the run number, should be safer than the last modified which has messed up before
            # (perhaps due to the Warwick archiver?)
            run_number = get_run_number()
            image_data = get_image_data(run_number, uts=uts, timeout=90)
        else:
            image_data = get_glance_data(uts=uts, timeout=90)
        if not get_headers:
            return image_data
    if get_headers:
        # Get the headers from the camera daemon
        headers = get_image_headers(img_num, 30)
        if uts is not None:
            headers = {ut: headers[ut] for ut in uts}  # Filter out Nones for UTs we didn't use
        if not get_data:
            return headers
        else:
            # Return both
            return image_data, headers


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

    ut_list = params.UTS_WITH_CAMERAS

    with daemon_proxy('exq') as daemon:
        for filt in filt_list:
            for exptime in exp_list:
                print(f'Taking {exptime:.0f}s {filt} exposure')
                daemon.add(ut_list, exptime, 1, filt, 1, target=name, imgtype=imgtype)
        daemon.resume()

    # estimate a deliberately pessimistic timeout
    readout = 30 * len(exp_list) * len(filt_list)
    total_exp = sum(exp_list) * len(filt_list)
    total_time = 1.5 * (readout + total_exp)
    wait_for_exposure_queue(total_time)


def outlets_are_off(outlets):
    """Check if the given power outlets are off."""
    with daemon_proxy('power') as daemon:
        info = daemon.get_info(force_update=True)
    all_status = {outlet: info[unit][outlet]
                  for unit in info if 'status' in unit
                  for outlet in info[unit]}
    return all([all_status[outlet] == 'off' for outlet in outlets if outlet in all_status])


def exposure_queue_is_empty():
    """Check if the image queue is empty."""
    with daemon_proxy('exq') as daemon:
        info = daemon.get_info(force_update=True)
    return info['queue_length'] == 0


def filters_are_homed():
    """Check if all the filter wheels are homed."""
    with daemon_proxy('filt') as daemon:
        info = daemon.get_info(force_update=True)
    return all(info[ut]['homed'] for ut in info['uts'])


def focusers_are_set():
    """Check if all the focusers are set."""
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    return all(info[ut]['status'] != 'UNSET' for ut in info['uts'])


def cameras_are_empty():
    """Check if all of the cameras are ready to expose."""
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
    return all((info[ut]['status'] != 'Exposing') and (info[ut]['in_queue'] == 0)
               for ut in info['uts'])


def cameras_are_cool():
    """Check if all the cameras are below the target temperature."""
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
    return all(info[ut]['ccd_temp'] < info[ut]['target_temp'] + 1 for ut in info['uts'])


def cameras_are_fullframe():
    """Check if all the cameras are set to take full-frame exposures."""
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
    return all(info[ut]['window_area'] == info[ut]['full_area'] for ut in info['uts'])


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
            with daemon_proxy('exq') as daemon:
                info = daemon.get_info(force_update=True)
            done = (info['queue_length'] == 0 and
                    info['exposing'] is False and
                    info['status'] == 'Ready')
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
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
    return info['num_taken']


def get_run_number():
    """Find the latest exposure run number."""
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
    return info['latest_run_number']


def get_image_headers(target_image_number, timeout=None):
    """Get the image headers for the given exposure."""
    start_time = time.time()
    finished = False
    timed_out = False
    while not finished and not timed_out:
        time.sleep(0.5)

        try:
            with daemon_proxy('cam', timeout=timeout) as daemon:
                image_num, headers = daemon.get_latest_headers()
            if image_num >= target_image_number:
                # Either these are the headers we wanted, or we missed them
                finished = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Cameras timed out')
    if image_num > target_image_number:
        raise ValueError('A new exposure has already overriden the image headers, open the file')
    return headers


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
            with daemon_proxy('cam') as daemon:
                info = daemon.get_info(force_update=True)
            done = [(info[ut]['status'] == 'Ready' and
                     int(info['num_taken']) == int(target_image_number))
                    for ut in info['uts']]
            if np.all(done):
                finished = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Cameras timed out')


def wait_for_readout(target_image_number, timeout=None):
    """With a set of exposures underway, wait for the cameras to finish exposing (but not saving).

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
            with daemon_proxy('cam') as daemon:
                info = daemon.get_info(force_update=True)
            done = [(info[ut]['status'] == 'Reading' and
                     int(info['num_taken']) == int(target_image_number) - 1)  # not finished yet
                    for ut in info['uts']]
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
    with daemon_proxy('conditions', timeout=timeout) as daemon:
        info = daemon.get_info(force_update=False)
    return info
