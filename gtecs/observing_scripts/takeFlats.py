#!/usr/bin/env python
"""Script to take flat frames in the morning or evening.

takeFlats [EVE|MORN] [-l]
"""

import sys
import time

from astropy import units as u
from astropy.time import Time

from gtecs import params
from gtecs.astronomy import night_startdate, sunalt_time
from gtecs.catalogs import antisun_flat, exposure_sequence, extrapolate_from_filters
from gtecs.observing import get_analysis_image, goto, offset, prepare_for_images, wait_for_telescope

import numpy as np


def take_sky(exptime, current_filter, name, glance=False):
    """Offset the telescope then take an image and return the mean sky brightness."""
    # make offsets to move stars
    offset('n', params.FLATS_STEPSIZE)
    time.sleep(3)
    wait_for_telescope(30)  # 30s timeout
    offset('w', params.FLATS_STEPSIZE)
    time.sleep(3)
    wait_for_telescope(30)  # 30s timeout

    # take the image and load the image data
    data = get_analysis_image(exptime, current_filter, name, 'FLAT', glance)

    # get the mean value for the images
    sky_mean = np.mean([np.median(data[tel]) for tel in data])
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
    goto(coordinate.ra.deg, coordinate.dec.deg)
    time.sleep(10)
    wait_for_telescope(120)  # 120s timeout

    # Set exposure order and check for sky brightness
    sky_mean_target = params.FLATS_SKYMEANTARGET
    nflats = params.FLATS_NUM
    if eve:
        start_exptime = 3.0
        filt_list = ['B', 'G', 'R', 'L']
        sky_mean = 40000.0
    else:
        start_exptime = 40.0
        filt_list = ['L', 'R', 'G', 'B']
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
        previous_filter = current_filter
        current_filter = filt_list.pop(0)
        print('~~~~~~')
        # Guess starting exposure time for new filter
        exptime = extrapolate_from_filters(previous_filter, exptime, current_filter, Time.now())

        # See if it was a good guess
        print('Taking {} test exposure to find new exposure time'.format(current_filter))
        sky_mean = take_sky(exptime, current_filter, field_name, glance=True)
        scaling_factor = 25000.0 / sky_mean
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


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1].upper() not in ['EVE', 'MORN']:
        print("usage: takeFlats EVE|MORN")
        sys.exit(1)
    else:
        period = sys.argv[1].upper()
    if len(sys.argv) > 2 and sys.argv[2] in ['l', 'late', '-l', '--late']:
        late = True
    else:
        late = False

    if period == 'EVE':
        eve = True
        alt = -3 * u.deg
    else:
        eve = False
        alt = -10 * u.deg

    run(eve, alt, late)
