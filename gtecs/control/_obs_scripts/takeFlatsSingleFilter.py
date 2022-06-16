#!/usr/bin/env python3
"""Script to take flat frames in the morning or evening in a single filter."""

import time
from argparse import ArgumentParser

from astropy import units as u
from astropy.time import Time

from gtecs.control import params
from gtecs.control.astronomy import night_startdate, sunalt_time
from gtecs.control.catalogs import antisun_flat
from gtecs.control.observing import (get_analysis_image, get_mount_position,
                                     prepare_for_images, slew_to_radec)

import numpy as np


def take_flat(exptime, filt, offset_step, target_name='Sky flats', glance=False):
    """Offset the telescope then take an image and return the mean sky brightness."""
    # Make an offset to move the stars
    # TODO: THIS SHOULD USE MNT OFFSET
    current_ra, current_dec = get_mount_position()
    step = offset_step * u.arcsec
    new_ra = current_ra + step.to(u.deg).value
    if new_ra >= 360:
        new_ra -= 360
    new_dec = current_dec + step.to(u.deg).value
    if new_dec > 90:
        new_dec = current_dec - step.to(u.deg).value

    # Move to the new position and wait until we're there
    slew_to_radec(new_ra, new_dec, timeout=120)

    # Take the image and load the image data
    if filt != 'C':
        uts = params.UTS_WITH_FILTERWHEELS
    else:
        uts = None
    image_headers = get_analysis_image(exptime, filt, 1, target_name, 'FLAT', glance, uts=uts,
                                       get_headers=True)

    # Get the mean value for the images
    sky_medians = {ut: image_headers[ut]['MEDCNTS'] for ut in sorted(image_headers)}
    print('Median counts:', sky_medians)
    mean_counts = np.mean([sky_medians[ut] for ut in sky_medians])

    return mean_counts


def run(eve, filt, exptime, offset_step,
        late=False, start_now=False, no_slew=False):
    """Take flats just after sunset or just after start of twilight."""
    # make sure hardware is ready
    prepare_for_images()

    print('Starting flats')
    if eve:
        start_alt = -5 * u.deg
        start_counts = 50000
        end_counts = 10000
    else:
        start_alt = -10 * u.deg
        start_counts = 10000
        end_counts = 50000

    # Wait until we reach correct sun altitude
    if start_now is False:
        today = night_startdate()
        start_time = sunalt_time(today, start_alt, eve)

        time_to_go = start_time - Time.now()
        if time_to_go < -10 * u.min and not late:
            raise Exception('Too late for flats!')

        print('Flats starting at {}'.format(str(start_time.datetime.time())[:8]))
        while True:
            time_to_go = start_time - Time.now()
            print('Flats starting in {:.1f}'.format(time_to_go.to(u.min)))
            if time_to_go.value < 0:
                break
            time.sleep(30)

    print('Ready to start flats')
    start_time = Time.now()

    # Slew to a flat field
    if not no_slew:
        print('~~~~~~')
        target = antisun_flat(start_time)
        print('Slewing to target {}...'.format(target))
        target_name = target.name
        target_coords = target.coord
        slew_to_radec(target_coords.ra.deg, target_coords.dec.deg, timeout=120)
    else:
        target_name = 'Sky flats'

    # Start taking glances and wait for sky to reach target brightness
    print('~~~~~~')
    print('Taking initial exposures')
    while True:
        counts = take_flat(exptime, filt, offset_step, target_name, glance=True)
        print('{} image sky mean: {:.1f} counts'.format(filt, counts))

        if eve and counts > start_counts:
            print('Waiting until below {:.1f} counts'.format(start_counts))
            time.sleep(1)
        elif not eve and counts < start_counts:
            print('Waiting until above {:.1f} counts'.format(start_counts))
            time.sleep(1)
        else:
            break
    print('Reached target sky brightness ({:.1f} counts)'.format(start_counts))

    # Take exposures until the target counts have been reached
    print('~~~~~~')
    print('Taking flats in {} filter'.format(filt))
    flats_count = 0
    while True:
        flats_count += 1
        print('Taking {} filter flat {}'.format(filt, flats_count))
        counts = take_flat(exptime, filt, offset_step, target_name)
        print('{} image sky mean: {:.1f} counts'.format(filt, counts))

        if eve and counts > end_counts:
            time.sleep(1)
        elif not eve and counts < end_counts:
            time.sleep(1)
        else:
            break
    print('Reached target sky brightness ({:.1f} counts)'.format(end_counts))

    print('Done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take flat frames in the morning or evening.')
    # Mandatory arguments
    parser.add_argument('time',
                        type=str, choices=['EVE', 'MORN'],
                        help='run the evening or morning routine')
    # Optional arguments
    parser.add_argument('-f', '--filter',
                        type=str, choices=params.FILTER_LIST, default='L',
                        help=('filter to use'
                              ' (default=%(default)d)')
                        )
    parser.add_argument('-t', '--exptime',
                        type=float, default=5,
                        help=('exposure time, in seconds'
                              ' (default=%(default)d)')
                        )
    parser.add_argument('-o', '--offset-step',
                        type=float, default=600,
                        help=('distance to move the mount between exposures, in arcsec'
                              ' (default=%(default)d)')
                        )
    # Flags
    parser.add_argument('-l', '--late',
                        action='store_true',
                        help=('ignore the expected twilight time')
                        )
    parser.add_argument('-N', '--now',
                        action='store_true',
                        help=('start taking flats NOW, regardless of sun altitude')
                        )
    parser.add_argument('--no-slew',
                        action='store_true',
                        help=('do not slew to a focus star (stay at current position)')
                        )

    args = parser.parse_args()
    eve = args.time == 'EVE'
    filt = args.filter
    exptime = args.exptime
    offset_step = args.offset_step
    late = args.late
    start_now = args.now
    no_slew = args.no_slew
    if start_now and late:
        print('Conflicting options detected: --now flag will override --late flag')

    run(eve, filt, exptime, offset_step, late, start_now, no_slew)
