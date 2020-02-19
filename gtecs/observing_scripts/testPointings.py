#!/usr/bin/env python3
"""Script to take images at a range of pointings."""

import sys
from argparse import ArgumentParser

from astropy.time import Time

from gtecs.astronomy import radec_from_altaz
from gtecs.observing import prepare_for_images, slew_to_radec, take_image_set


def run(n_alt, n_az):
    """Run test pointings routine."""
    # make sure hardware is ready
    prepare_for_images()

    # generate alt list
    if n_alt > 4:
        n_alt = 4
    alt_list = [45, 60, 75][:n_alt - 1][::-1]

    # generate az list
    if n_az > 4:
        n_az = 4
    az_list = [i for i in range(0, 360, int(360 / n_az))]

    # generate pointings
    altaz_list = [(90, 0)]  # don't repeat zenith at different azimuths
    for az in az_list:
        for alt in alt_list:
            altaz_list.append((alt, az))

    print('Generated {} AltAz pointings:'.format(len(altaz_list)))
    print(altaz_list)

    exposure_list = [15, 30, 60, 120, 240, 480]

    print('Exposure times:')
    print(exposure_list)

    total_exptime = (sum(exposure_list) * len(altaz_list)) / 60.
    print('Total exposure time: {} mins'.format(total_exptime))

    total_readout = 0.5 * len(exposure_list) * len(altaz_list)
    total_slew = 0.5 * len(altaz_list)
    print('Estimated total time: {} mins'.format(total_exptime + total_readout + total_slew))

    cont = 'na'
    while cont not in ['y', 'n']:
        cont = input('Continue? [y/n]: ')
    if cont == 'n':
        sys.exit()

    print('OK, starting routine...')

    for altaz in altaz_list:
        alt, az = altaz
        print('Slewing to Alt {}, Az {}'.format(alt, az))

        # Slew to position
        ra, dec = radec_from_altaz(alt, az, Time.now())
        slew_to_radec(ra, dec, timeout=120)

        take_image_set(exposure_list, 'L', 'Test Pointing')

    print('Done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take images at a range of pointings.')
    parser.add_argument('n_alt', type=int, nargs='?', default=2,
                        help='number of altitude rows (default=2)')
    parser.add_argument('n_az', type=int, nargs='?', default=2,
                        help='number of aximuth rows (default=2)')
    args = parser.parse_args()

    run(args.n_alt, args.n_az)
