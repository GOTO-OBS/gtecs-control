#!/usr/bin/env python3
"""Script to take flat frames in the morning or evening."""

import time
from argparse import ArgumentParser

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

from gtecs.control import params
from gtecs.control.astronomy import sunalt_time
from gtecs.control.catalogs import antisun_flat, exposure_sequence
from gtecs.control.daemons import daemon_proxy
from gtecs.control.observing import get_analysis_image, prepare_for_images, slew_to_radec

import numpy as np


# TODO: filter info could be in params?
FILTER_BANDWIDTH = {'L': 2942,
                    'R': 979,
                    'G': 813,
                    'B': 1188,
                    'C': 5596,
                    }
# Order filters by twilight sky brightness, dimmest first (remember to reverse in the morning)
FILTER_ORDER = ['B', 'G', 'R', 'L', 'C']


def take_flat(exptime, filt, offset_step, target_name='Sky flats', glance=False):
    """Offset the telescope then take an image and return the mean sky brightness."""
    # Make an offset to move the stars
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
    current_position = SkyCoord(info['mount_ra'], info['mount_dec'], unit=(u.hourangle, u.deg))
    new_position = current_position.directional_offset_by(90 * u.deg, offset_step * u.arcsec)

    # Move to the new position and wait until we're there
    slew_to_radec(new_position.ra.deg, new_position.dec.deg, timeout=120)

    # Only use UTs which have the given filter
    uts = [ut for ut in params.UT_DICT if filt in params.UT_DICT[ut]['FILTERS']]

    # Take the image, then get the mean value from the headers
    image_headers = get_analysis_image(exptime, filt, 1, target_name, 'FLAT', glance, uts=uts,
                                       get_data=False, get_headers=True)

    # Get the mean value for the images
    sky_medians = {ut: image_headers[ut]['MEDCNTS'] for ut in sorted(image_headers)}
    print('Median counts:', sky_medians)
    mean_counts = np.mean([sky_medians[ut] for ut in sky_medians])

    return mean_counts


def run(eve, target_counts, num_exp, filt_list=None, max_exptime=30, offset_step=600,
        late=False, start_now=False, no_slew=False):
    """Take flats just after sunset or just after start of twilight."""
    # make sure hardware is ready
    prepare_for_images()

    print('Starting flats')

    # Sort filters based on sky brightness
    if filt_list is None:
        filt_list = FILTER_ORDER.copy()
    else:
        filt_list = sorted(filt_list, key=lambda x: FILTER_ORDER.index(x))

    if eve:
        start_alt = -5 * u.deg
        exptime = 3.0
    else:
        start_alt = -10 * u.deg
        exptime = 20.0
        filt_list.reverse()

    # Wait until we reach correct sun altitude
    if start_now is False:
        start_time = sunalt_time(start_alt, eve)

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
    filt = filt_list[0]
    while True:
        counts = take_flat(exptime, filt, offset_step, target_name, glance=True)
        print('{} image sky mean: {:.1f} counts'.format(filt, counts))

        if eve and counts > target_counts:
            print('Waiting until below {:.1f} counts'.format(target_counts))
            time.sleep(1)
        elif not eve and counts < target_counts:
            print('Waiting until above {:.1f} counts'.format(target_counts))
            time.sleep(1)
        else:
            break
    print('Reached target sky brightness ({:.1f} counts)'.format(target_counts))

    # Run through the filter list
    for i, filt in enumerate(filt_list):
        print('~~~~~~')
        filt = filt_list[i]
        print('Using {} filter'.format(filt))

        if i > 0:
            new_exptime = exptime * (target_counts / counts)
            # Guess initial exposure time based on the previous filter
            bandwidth_ratio = FILTER_BANDWIDTH[filt_list[i - 1]] / FILTER_BANDWIDTH[filt]
            new_exptime = new_exptime * bandwidth_ratio
            print('Rescaling exposure time from {:.1f} to {:.1f}'.format(exptime, new_exptime))
            exptime = new_exptime
            if exptime > max_exptime:
                print('Limiting exposure time to {:.1f}s'.format(max_exptime))
                exptime = max_exptime

            # Take initial measurement
            print('Taking {} test exposure to find new exposure time'.format(filt))
            counts = take_flat(new_exptime, filt, offset_step, target_name, glance=True)
            print('{} image sky mean: {:.1f} counts'.format(filt, counts))

        # Rescale based on new measurement
        new_exptime = exptime * (target_counts / counts)
        print('Rescaling exposure time from {:.1f} to {:.1f}'.format(exptime, new_exptime))
        exptime = new_exptime
        if exptime > max_exptime:
            print('Limiting exposure time to {:.1f}s'.format(max_exptime))
            exptime = max_exptime

        print('~~~~~~')
        exptime_list = exposure_sequence(exptime, num_exp, eve=eve)
        print('Taking {} flats in {} filter'.format(len(exptime_list), filt))
        for i, exptime in enumerate(exptime_list):
            print('Taking {} filter flat {}/{}'.format(filt, i + 1, len(exptime_list)))
            if exptime > max_exptime:
                print('Limiting exposure time to {:.1f}s'.format(max_exptime))
                exptime = max_exptime

            counts = take_flat(exptime, filt, offset_step, target_name)
            print('{} image sky mean: {:.1f} counts'.format(filt, counts))

            # Stop if saturated in the morning
            if not eve and counts > 65000:
                print('Images are saturated, stopping flats')
                break

    print('Done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take flat frames in the morning or evening.')
    # Mandatory arguments
    parser.add_argument('time',
                        type=str, choices=['EVE', 'MORN'],
                        help='run the evening or morning routine')
    # Optional arguments
    parser.add_argument('-c', '--target-counts',
                        type=int, default=30000,
                        help=('target mean sky counts for each flat frame'
                              ' (default=%(default)d)')
                        )
    parser.add_argument('-n', '--numexp',
                        type=int, default=3,
                        help=('number of exposures to take in each filter'
                              ' (default=%(default)d)')
                        )
    parser.add_argument('-f', '--filters',
                        type=str, default='L,R,G,B,C',
                        help=('filters to use'
                              ' (comma separated, default=%(default)s)')
                        )
    parser.add_argument('-m', '--max-exptime',
                        type=float, default=300,
                        help=('maximum exposure time, in seconds'
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
    target_counts = args.target_counts
    num_exp = args.numexp
    filt_list = args.filters.split(',')
    max_exptime = args.max_exptime
    offset_step = args.offset_step
    late = args.late
    start_now = args.now
    no_slew = args.no_slew
    if start_now and late:
        print('Conflicting options detected: --now flag will override --late flag')

    run(eve, target_counts, num_exp, filt_list, max_exptime, offset_step, late, start_now, no_slew)
