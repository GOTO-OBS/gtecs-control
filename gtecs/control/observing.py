"""Observing utilities."""

import time

import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

import numpy as np

from . import params
from .astronomy import radec_from_altaz, within_mount_limits
from .daemons import daemon_proxy
from .fits import clear_glance_files, get_glance_data, get_image_data


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
    with daemon_proxy('power') as daemon:
        info = daemon.get_info(force_update=True)
        all_status = {outlet: info[unit][outlet]
                      for unit in info if 'status' in unit
                      for outlet in info[unit]}
        all_off = all([all_status[outlet] == 'off'
                       for outlet in params.OBSERVING_OFF_OUTLETS
                       if outlet in all_status])
        if not all_off:
            print('Turning off dome lights')
            daemon.off(params.OBSERVING_OFF_OUTLETS)
            # TODO: blocking command with confirmation or timeout in daemon
            time.sleep(0.5)

    # Empty the exposure queue
    with daemon_proxy('exq') as daemon:
        info = daemon.get_info(force_update=True)
        if info['queue_length'] > 0:
            print('Clearing exposure queue')
            daemon.clear()
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                if info['queue_length'] == 0:
                    break
                if (time.time() - start_time) > 30:
                    raise TimeoutError('Exposure queue timed out')

    # Home the filter wheels
    with daemon_proxy('filt') as daemon:
        info = daemon.get_info(force_update=True)
        if not all(info[ut]['homed'] for ut in info['uts']):
            print('Homing filters')
            daemon.home_filters()
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                if all(info[ut]['homed'] for ut in info['uts']):
                    break
                if (time.time() - start_time) > 30:
                    raise TimeoutError('Filter wheels timed out')

    # Set the focusers
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
        if any(info[ut]['status'] == 'UNSET' for ut in info['uts']):
            print('Setting focusers')
            daemon.move_focusers(10)
            time.sleep(4)
            daemon.move_focusers(-10)
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                if not any(info[ut]['status'] == 'UNSET' for ut in info['uts']):
                    break
                if (time.time() - start_time) > 60:
                    raise TimeoutError('Focusers timed out')

    # Make sure the cameras aren't exposing
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
        exposing = (info[ut]['status'] == 'Exposing' or info[ut]['in_queue'] > 0
                    for ut in info['uts'])
        if any(exposing):
            print('Aborting ongoing exposures')
            daemon.abort_exposure()
            time.sleep(3)
            daemon.clear_queue()
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                exposing = (info[ut]['status'] == 'Exposing' or info[ut]['in_queue'] > 0
                            for ut in info['uts'])
                if not any(exposing):
                    break
                if (time.time() - start_time) > 60:
                    raise TimeoutError('Cameras timed out')

    # Reset the cameras to full-frame exposures
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
        if not all(info[ut]['window_area'] == info[ut]['full_area'] for ut in info['uts']):
            print('Setting cameras to full-frame')
            daemon.remove_window()
            # TODO: blocking command with confirmation or timeout in daemon
            time.sleep(4)

    # Bring the CCDs down to temperature
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
        if not all(info[ut]['ccd_temp'] < info[ut]['target_temp'] + 1 for ut in info['uts']):
            print('Cooling cameras')
            daemon.set_temperature('cool')
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                if all(info[ut]['ccd_temp'] < info[ut]['target_temp'] + 1 for ut in info['uts']):
                    break
                if (time.time() - start_time) > 600:
                    raise TimeoutError('Camera cooling timed out')

    # Start the mount motors (but remain parked, or in whatever position we're in)
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
        if not info['motors_on']:
            print('Turning on mount motors')
            daemon.power_motors('on')
            # TODO: blocking command with confirmation or timeout in daemon
            time.sleep(4)

    # Open/close the mirror covers
    with daemon_proxy('ota') as daemon:
        info = daemon.get_info(force_update=True)
        target_position = 'full_open' if open_covers else 'closed'
        if not all([info[ut]['position'] == target_position for ut in info['uts_with_covers']]):
            if open_covers:
                print('Opening mirror covers')
                daemon.open_covers()
            else:
                print('Closing mirror covers')
                daemon.close_covers()
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                if all([info[ut]['position'] == target_position for ut in info['uts_with_covers']]):
                    break
                if (time.time() - start_time) > 60:
                    raise TimeoutError('Mirror covers timed out')


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

    print(f'Slewing to {ra:.2f} {dec:.2f}')
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
        if info['status'] == 'Slewing':
            daemon.halt()
            time.sleep(2)
        coords = SkyCoord(ra * u.deg, dec * u.deg)
        daemon.slew(coords)

        if wait or timeout is not None:
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                tolerance = 30 / (60 * 60)  # 30 arcsec
                on_target = (info['status'] == 'Tracking' and
                             np.isclose(info['target_ra'] * 360 / 24, ra, tolerance) and
                             np.isclose(info['target_dec'], dec, tolerance) and
                             info['target_dist'] < tolerance)
                if on_target:
                    break
                if (time.time() - start_time) > timeout:
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
    with daemon_proxy('cam') as daemon:
        info = daemon.get_info(force_update=True)
    img_num = info['num_taken'] + 1

    # Remove old glance files (so we know what to wait for)
    if glance:
        clear_glance_files()

    # Send the command
    with daemon_proxy('exq') as daemon:
        print(f'Taking {exptime:.0f}s {filt} {"exposure" if not glance else "glance"}')
        daemon.add(exptime, 1, filt, binning,
                   target=name, imgtype=imgtype, glance=glance, uts=uts)
        daemon.resume()
        image_start_time = time.time()

    if not get_data and not get_headers:
        # Wait for exposures to finish, but not to save
        with daemon_proxy('cam') as daemon:
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                if (info['num_taken'] == img_num - 1 and
                        all(info[ut]['status'] == 'Reading' for ut in info['uts'])):
                    break
                if (time.time() - image_start_time) > exptime + 60:
                    raise TimeoutError('Cameras timed out')

        # Just return the run and image numbers to do something else with
        if not glance:
            with daemon_proxy('cam') as daemon:
                info = daemon.get_info(force_update=True)
            run_number = info['latest_run_number']
            return run_number, img_num
        else:
            return 'glance', img_num

    # Otherwise we need to wait for the images to be saved
    with daemon_proxy('cam') as daemon:
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            if (info['num_taken'] == img_num and
                    all(info[ut]['status'] == 'Ready' for ut in info['uts'])):
                break
            if (time.time() - image_start_time) > exptime + 60:
                raise TimeoutError('Cameras timed out')

    if get_data:
        if not glance:
            # Use the run number, should be safer than the last modified which has messed up before
            # (perhaps due to the Warwick archiver?)
            with daemon_proxy('cam') as daemon:
                info = daemon.get_info(force_update=True)
            run_number = info['latest_run_number']
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

    with daemon_proxy('exq') as daemon:
        for filt in filt_list:
            for exptime in exp_list:
                print(f'Taking {exptime:.0f}s {filt} exposure')
                daemon.add(exptime, 1, filt, 1, target=name, imgtype=imgtype)
        # estimate a deliberately pessimistic timeout
        readout = 30 * len(exp_list) * len(filt_list)
        total_exp = sum(exp_list) * len(filt_list)
        total_time = 1.5 * (readout + total_exp)
        daemon.resume()
        # TODO: blocking command with confirmation or timeout in daemon
        start_time = time.time()
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            if (info['queue_length'] == 0 and
                    info['exposing'] is False and
                    info['status'] == 'Ready'):
                break
            if (time.time() - start_time) > total_time:
                raise TimeoutError('Exposure queue timed out')


def get_image_headers(target_image_number, timeout=None):
    """Get the image headers for the given exposure."""
    start_time = time.time()
    finished = False
    timed_out = False
    while not finished and not timed_out:
        time.sleep(0.5)
        # TODO: blocking command with confirmation or timeout in daemon
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
