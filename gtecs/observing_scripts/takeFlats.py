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
    exq_command = 'exq image {:.1f} {} 1 "{}" FLAT'.format(expT, current_filter, name)
    execute_command(exq_command)
    time.sleep(0.1)
    wait_for_exposure_queue(180)
    offset('n', 60)  # make an offset to move stars
    time.sleep(0.1)
    offset('w', 60)  # make an offset to move stars
    time.sleep(0.1)
    fnames = last_written_image()
    skyMean = mean_sky_brightness(fnames)
    return skyMean


def run(eve, alt, late=False):
    """run just after sunset or just after start of twilight"""
    # make sure hardware is ready
    prepare_for_images()

    print("Starting flats")

    # spin our heels until we reach correct sunAlt
    time_to_go = -1000*u.second
    today = nightStarting()
    start_time = startTime(today, alt, eve)
    time_to_go = Time.now() - start_time
    if time_to_go > 10*u.min and not late:
        print("Too late for flats")
        sys.exit(1)
    print('starting in {:.1f}'.format(-time_to_go.to(u.min)))
    # wait
    if -time_to_go > 30*u.second:
        time.sleep(-time_to_go.to(u.second).value - 30)

    # OK! Let's go
    #flat = flats.best_flat(Time.now())
    flat = flats.antisun_flat(Time.now())
    print('Slewing to flat', flat)
    coordinate = flat.coord
    goto(coordinate.ra.deg, coordinate.dec.deg)
    time.sleep(10)
    wait_for_telescope(120)  # 120s timeout

    # set exposure order and check for sky brightness
    if eve:
        expT = 3.0
        nflats = 3
        filt_order = ['B', 'G', 'R', 'L']
        skyMean = 40000.0
        skyMeanCheck = lambda x: x > 25000.0
    else:
        expT = 40.0
        nflats = 3
        filt_order = ['L', 'R', 'G', 'B']
        skyMean = 2.0
        skyMeanCheck = lambda x: x < 25000.0

    # start taking exposures (glances) and wait for sky
    current_filter = filt_order.pop(0)
    while skyMeanCheck(skyMean):
        skyMean = take_sky(expT, current_filter, flat.name)
        print('Waiting... Sky Mean: {:.1f}'.format(skyMean))

    # we have just crossed threshold
    # start the first filter
    exposure_sequence = flats.exposure_sequence(
        today, 1, expT, nflats=nflats, eve=eve
    )
    for expT in exposure_sequence:
        skyMean = take_sky(expT, current_filter, flat.name)
        print('Sky Flat {} taken. Sky Mean: {:.1f}'.format(current_filter,
                                                           skyMean))

    # now do the rest of the filters
    while filt_order:
        previous_filter = current_filter
        current_filter = filt_order.pop(0)

        # first guess exp time
        expT = flats.extrapolate_from_filters(
            previous_filter, expT, current_filter, Time.now()
        )

        # take a look see
        skyMean = take_sky(expT, current_filter, flat.name)
        scaling_factor = 25000.0 / skyMean
        print('Rescaling exposure time from {:.1f} to {:.1f}'.format(
            expT, expT*scaling_factor
        ))
        expT = expT*scaling_factor

        # now we know the correct start exposure
        # exposure_sequence should work from here
        exposure_sequence = flats.exposure_sequence(
            today, 1, expT, nflats=nflats, eve=eve
        )
        for expT in exposure_sequence:
            skyMean = take_sky(expT, current_filter, flat.name)
            print('Sky Flat {} taken. Sky Mean: {:.1f}'.format(current_filter,
                                                               skyMean))

    print("Done")


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
