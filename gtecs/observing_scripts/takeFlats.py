#!/usr/bin/env python3
"""Script to take flat frames in the morning or evening."""

import sys
import time
from argparse import ArgumentParser

from astropy import units as u
from astropy.time import Time

from gtecs import params
from gtecs.astronomy import night_startdate, sunalt_time
from gtecs.catalogs import antisun_flat, exposure_sequence, extrapolate_from_filters
from gtecs.observing import (get_analysis_image, get_mount_position,
                             prepare_for_images, slew_to_radec)

import numpy as np


def take_sky(exptime, current_filter, name, glance=False):
    """Offset the telescope then take an image and return the mean sky brightness."""
    # Make an offset to move the stars
    step = params.FLATS_STEPSIZE * u.arcsec
    current_ra, current_dec = get_mount_position()
    new_ra = current_ra + step.to(u.deg).value
    if new_ra >= 360:
        new_ra -= 360
    new_dec = current_dec + step.to(u.deg).value
    if new_dec > 90:
        new_dec = current_dec - step.to(u.deg).value

    # Move to the new position and wait until we're there
    slew_to_radec(new_ra, new_dec, timeout=120)

    # Take the image and load the image data
    if current_filter != 'C':
        uts = params.UTS_WITH_FILTERWHEELS
    else:
        uts = [ut for ut in params.UTS if ut not in params.UTS_WITH_FILTERWHEELS]
    image_data = get_analysis_image(exptime, current_filter, name, 'FLAT', glance, uts=uts)

    # Get the mean value for the images
    sky_medians = {ut: int(np.median(image_data[ut])) for ut in sorted(image_data)}
    print('Median counts:', sky_medians)
    sky_mean = np.mean([sky_medians[ut] for ut in sky_medians])

    # Delete the image data for good measure, to save memory
    del image_data

    return sky_mean


def run(eve, alt, late=False):
    """Take flats just after sunset or just after start of twilight."""
    # make sure hardware is ready
    prepare_for_images()

    print('Starting flats')

    # Wait until we reach correct sunAlt
    today = night_startdate()
    start_time = sunalt_time(today, alt, eve)

    time_to_go = start_time - Time.now()
    if time_to_go < -10 * u.min and not late:
        print('Too late for flats!')
        sys.exit(1)

    print('Flats starting at {}'.format(str(start_time.datetime.time())[:8]))
    while True:
        time_to_go = start_time - Time.now()
        print('Flats starting in {:.1f}'.format(time_to_go.to(u.min)))
        if time_to_go.value < 0:
            break
        time.sleep(30)
    print('Ready to start flats')

    # Ready to go
    # Find flat field target
    skyflat = antisun_flat(Time.now())
    print('Found target', skyflat)

    # Slew to target
    print('Slewing to target')
    field_name = skyflat.name
    coordinate = skyflat.coord
    slew_to_radec(coordinate.ra.deg, coordinate.dec.deg, timeout=120)

    # Set exposure order and check for sky brightness
    sky_mean_target = params.FLATS_SKYMEANTARGET
    nflats = params.FLATS_NUM
    if eve:
        start_exptime = 3.0
        filt_list = ['B', 'G', 'R', 'L', 'C']
        sky_mean = 40000.0
    else:
        start_exptime = 20.0
        filt_list = ['C', 'L', 'R', 'G', 'B']
        sky_mean = 2.0

    # start taking exposures (glances) and wait for sky
    print('~~~~~~')
    print('Taking initial exposures')
    current_filter = filt_list.pop(0)
    while True:
        time.sleep(1)
        sky_mean = take_sky(start_exptime, current_filter, field_name, glance=True)
        print('{} image sky mean: {:.1f} counts'.format(current_filter, sky_mean))
        if eve:
            if sky_mean > sky_mean_target:
                print('Waiting until below {:.1f} counts'.format(sky_mean_target))
            else:
                break
        else:
            if sky_mean < sky_mean_target:
                print('Waiting until above {:.1f} counts'.format(sky_mean_target))
            else:
                break
    print('Reached target sky brightness ({:.1f} counts)'.format(sky_mean_target))

    # Start in the first filter
    print('~~~~~~')
    print('Taking flats in {} filter'.format(current_filter))
    exp_list = exposure_sequence(today, 1, start_exptime, nflats=nflats, eve=eve)

    for i, exptime in enumerate(exp_list):
        print('Taking {} filter flat {}/{}'.format(current_filter, i + 1, len(exp_list)))

        if exptime > params.FLATS_MAXEXPTIME:
            print('Limiting exposure time to {:.1f}'.format(params.FLATS_MAXEXPTIME))
            exptime = params.FLATS_MAXEXPTIME

        sky_mean = take_sky(exptime, current_filter, field_name)
        print('{} image sky mean: {:.1f} counts'.format(current_filter, sky_mean))

    # Run through the rest of the filter list
    while filt_list:
        print('~~~~~~')
        # Guess starting exposure time based on the previous filter
        exptime_dict = extrapolate_from_filters(exptime, current_filter, sky_mean, sky_mean_target)

        # Select the new filter
        current_filter = filt_list.pop(0)
        exptime = exptime_dict[current_filter]

        # See if it was a good guess
        print('Taking {} test exposure to find new exposure time'.format(current_filter))
        sky_mean = take_sky(exptime, current_filter, field_name, glance=True)
        scaling_factor = sky_mean_target / sky_mean
        start_exptime = exptime * scaling_factor
        print('Rescaling exposure time from {:.1f} to {:.1f}'.format(exptime, start_exptime))
        if start_exptime > params.FLATS_MAXEXPTIME:
            print('Limiting exposure time to {:.1f}'.format(params.FLATS_MAXEXPTIME))
            start_exptime = params.FLATS_MAXEXPTIME

        print('Taking flats in {} filter'.format(current_filter))
        exp_list = exposure_sequence(today, 1, start_exptime, nflats=nflats, eve=eve)

        for i, exptime in enumerate(exp_list):
            print('Taking {} filter flat {}/{}'.format(current_filter, i + 1, len(exp_list)))

            if exptime > params.FLATS_MAXEXPTIME:
                print('Limiting exposure time to {:.1f}'.format(params.FLATS_MAXEXPTIME))
                exptime = params.FLATS_MAXEXPTIME

            sky_mean = take_sky(exptime, current_filter, field_name)
            print('{} image sky mean: {:.1f} counts'.format(current_filter, sky_mean))

    print('Done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take flat frames in the morning or evening.')
    parser.add_argument('time', type=str, choices=['EVE', 'MORN'],
                        help='run the evening or morning routine')
    parser.add_argument('-l', '--late', action='store_true',
                        help=('ignore the expected twilight time')
                        )
    args = parser.parse_args()

    if args.time == 'EVE':
        eve = True
        alt = -5 * u.deg
    else:
        eve = False
        alt = -10 * u.deg
    late = args.late

    run(eve, alt, late)
