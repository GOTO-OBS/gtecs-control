"""
takeFlats [EVE|MORN] [-l]
Script to take flat frames in the morning or evening
"""

import sys
import time

import numpy as np

from astropy import units as u
from astropy.time import Time
from astropy.io import fits

from gtecs import params
from gtecs.misc import execute_command
from gtecs.astronomy import startTime, nightStarting
from gtecs.observing import (wait_for_exposure_queue, last_written_image,
                             prepare_for_images, goto, offset,
                             wait_for_telescope)
from gtecs.catalogs import flats


def mean_sky_brightness(fnames):
    means = []
    for tel in params.TEL_DICT:
        data = fits.getdata(fnames[tel])
        mean = np.median(data)
        means.append(mean)
    return np.mean(means)


def take_sky(expT, current_filter, name):
    offset('n', 60)  # make an offset to move stars
    time.sleep(1)
    offset('w', 60)  # make an offset to move stars
    time.sleep(2)
    exq_command = 'exq image {:.1f} {} 1 "{}" FLAT'.format(expT, current_filter, name)
    execute_command(exq_command)
    time.sleep(0.1)
    wait_for_exposure_queue(180)
    time.sleep(5) # need to wait for images to actually be saved
    fnames = last_written_image()
    sky_mean = mean_sky_brightness(fnames)
    return sky_mean


def run(eve, alt, late=False):
    """run just after sunset or just after start of twilight"""
    # make sure hardware is ready
    prepare_for_images()

    print('Starting flats')

    # Wait until we reach correct sunAlt
    today = nightStarting()
    start_time = startTime(today, alt, eve)

    time_to_go = start_time - Time.now()
    if time_to_go < -10*u.min and not late:
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
    #skyflat = flats.best_flat(Time.now())
    skyflat = flats.antisun_flat(Time.now())
    print('Found target', skyflat)

    # Slew to target
    print('Slewing to target')
    field_name = skyflat.name
    coordinate = skyflat.coord
    goto(coordinate.ra.deg, coordinate.dec.deg)
    time.sleep(10)
    wait_for_telescope(120)  # 120s timeout

    # Set exposure order and check for sky brightness
    sky_mean_target = 25000.0
    if eve:
        start_expT = 3.0
        nflats = 3
        filt_list = ['B', 'G', 'R', 'L']
        sky_mean = 40000.0
        sky_mean_check = lambda x: x > sky_mean_target
    else:
        start_expT = 40.0
        nflats = 3
        filt_list = ['L', 'R', 'G', 'B']
        sky_mean = 2.0
        sky_mean_check = lambda x: x < sky_mean_target

    # start taking exposures (glances) and wait for sky
    print('~~~~~~')
    print('Taking initial exposures')
    current_filter = filt_list.pop(0)
    while sky_mean_check(sky_mean):
        sky_mean = take_sky(start_expT, current_filter, field_name)
        print('{} image sky mean: {:.1f} counts'.format(current_filter, sky_mean))
    print('Reached target sky brightness ({:.1f} counts)'.format(sky_mean_target))

    # Start in the first filter
    print('~~~~~~')
    print('Taking flats in {} filter'.format(current_filter))
    exp_list = flats.exposure_sequence(today, 1, start_expT,
                                       nflats=nflats, eve=eve)

    for i, expT in enumerate(exp_list):
        print('Taking {} filter flat {}/{}'.format(current_filter, i+1, len(exp_list)))
        sky_mean = take_sky(expT, current_filter, field_name)
        print('{} image sky mean: {:.1f} counts'.format(current_filter, sky_mean))

    # Run through the rest of the filter list
    while filt_list:
        previous_filter = current_filter
        current_filter = filt_list.pop(0)
        print('~~~~~~')
        # Guess starting exposure time for new filter
        expT = flats.extrapolate_from_filters(previous_filter, expT,
                                              current_filter, Time.now())

        # See if it was a good guess
        print('Taking {} test exposure to find new exposure time'.format(current_filter))
        sky_mean = take_sky(expT, current_filter, field_name)
        scaling_factor = 25000.0 / sky_mean
        start_expT = expT*scaling_factor
        print('Rescaling exposure time from {:.1f} to {:.1f}'.format(expT, start_expT))

        print('Taking flats in {} filter'.format(current_filter))
        exp_list = flats.exposure_sequence(today, 1, start_expT,
                                           nflats=nflats, eve=eve)

        for i, expT in enumerate(exp_list):
            print('Taking {} filter flat {}/{}'.format(current_filter, i+1, len(exp_list)))
            sky_mean = take_sky(expT, current_filter, field_name)
            print('{} image sky mean: {:.1f} counts'.format(current_filter, sky_mean))

    print('Done')


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1].upper() not in ['EVE','MORN']:
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
        alt = -3*u.deg
    else:
        eve = False
        alt = -10*u.deg

    run(eve, alt, late)
