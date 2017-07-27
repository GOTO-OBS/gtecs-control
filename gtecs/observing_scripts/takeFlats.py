from __future__ import absolute_import
from __future__ import print_function

import numpy as np

from astropy import units as u
from astropy.time import Time
from astropy.io import fits

from gtecs.tecs_modules.misc import execute_command as cmd
from gtecs.catalogs import flats
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          last_written_image,
                                          filters_are_homed,
                                          goto, random_offset,
                                          wait_for_telescope)
import gtecs.tecs_modules.astronomy as ast
from gtecs.tecs_modules.time_date import nightStarting
from gtecs.tecs_modules import params

import time
import sys


def mean_sky_brightness(fnames):
    means = []
    for tel in params.TEL_DICT:
        data = fits.getdata(fnames[tel])
        mean = np.median(data)
        means.append(mean)
    return np.mean(means)


def take_sky(expT, current_filter, name):
    cmd('exq image {} {} 1 "{}" FLAT'.format(
        expT, current_filter, name
    ))
    time.sleep(0.1)
    wait_for_exposure_queue(180)
    random_offset(10)  # make random offset to move stars
    time.sleep(0.1)
    fnames = last_written_image()
    skyMean = mean_sky_brightness(fnames)
    return skyMean

if __name__ == "__main__":

    '''run just after sunset or just after start of twilight'''
    try:
        assert len(sys.argv) == 2
        assert sys.argv[1].upper() in ['EVE','MORN']
    except:
        print("usage: takeFlats EVE|MORN")
        sys.exit(1)
    if sys.argv[1].upper() == 'EVE':
        eve = True
        alt = -3*u.deg
    else:
        eve = False
        alt = -5.5*u.deg

    if not filters_are_homed():
        print('homing filters')
        time.sleep(1)
        while not filters_are_homed():
            time.sleep(1)

    print("Starting flats")

    # spin our heels until we reach correct sunAlt
    time_to_go = -1000*u.second
    today = nightStarting()
    startTime = ast.startTime(today, alt, eve)
    time_to_go = Time.now() - startTime
    if time_to_go > 10*u.min:
        print("Too late for flats")
        sys.exit(1)
    print('starting in ', -time_to_go.to(u.min))
    # wait
    if -time_to_go > 30*u.second:
        time.sleep(-time_to_go.to(u.second).value - 30)

    # OK! Let's go
    flat = flats.best_flat(Time.now())
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
        print('Rescaling exposure time from {} to {}'.format(
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
