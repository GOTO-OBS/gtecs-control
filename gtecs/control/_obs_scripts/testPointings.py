#!/usr/bin/env python3
"""Script to take images at a range of pointings."""

import sys
from argparse import ArgumentParser

from astropy.time import Time

from gtecs.control import params
from gtecs.control.astronomy import get_moon_distance, radec_from_altaz
from gtecs.control.observing import prepare_for_images, slew_to_radec, take_image_set


def run(n_alt, n_az, num_exp, exp_list, filt, min_moonsep):
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

    print('Exposure times:')
    print(exp_list)

    total_exptime = (sum(exp_list) * num_exp * len(altaz_list)) / 60.
    print('Total exposure time: {:.1f} mins'.format(total_exptime))

    total_readout = 0.5 * len(exp_list) * num_exp * len(altaz_list)
    total_slew = 0.5 * len(altaz_list)
    print('Estimated total time: {:.1f} mins'.format(total_exptime + total_readout + total_slew))

    cont = 'na'
    while cont.lower() not in ['', 'y', 'n']:
        cont = input('Continue? [Y/n]: ')
    if cont.lower() == 'n':
        sys.exit()

    print('OK, starting routine...')

    for i, altaz in altaz_list:
        print('~~~~~~')
        print('POINTING {} of {}'.format(i + 1, len(altaz_list)))

        alt, az = altaz
        print('Slewing to Alt {:.4f}, Az {:.4f}'.format(alt, az))

        # Get coordiantes
        t = Time.now()
        ra, dec = radec_from_altaz(alt, az, t)

        # Check the moon distance
        moonsep = get_moon_distance(ra, dec, t)
        if moonsep < min_moonsep:
            print('Too close to the Moon ({.1f} deg < {:.1f} deg)!'.format(moonsep, min_moonsep))
            continue

        # Slew to position
        slew_to_radec(ra, dec, timeout=120)

        # Take images
        for i in range(num_exp):
            if num_exp > 1:
                print('Taking exposure {} of {}'.format(i + 1, num_exp))
            take_image_set(exp_list, 'L', 'Test Pointing')

    print('Done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take images at a range of pointings.')
    # Optional arguments
    parser.add_argument('n_alt',
                        type=int, nargs='?', default=2,
                        help=('number of altitude rows'
                              ' (default=%(default)d)'),
                        )
    parser.add_argument('n_az',
                        type=int, nargs='?', default=2,
                        help=('number of azimuth rows'
                              ' (default=%(default)d)'),
                        )
    parser.add_argument('-n', '--numexp',
                        type=int, default=1,
                        help=('number of exposures to take for each exposure time'
                              ' (default=%(default)d)'),
                        )
    parser.add_argument('-t', '--exptime',
                        type=str, default='30,60,90',
                        help=('exposure time(s), in seconds'
                              ' (comma-separated, default=%(default)s)')
                        )
    parser.add_argument('-f', '--filter',
                        type=str, default='L',
                        help=('filter to use'
                              ' (default=%(default)s)'),
                        )
    parser.add_argument('-m', '--minmoonsep',
                        type=float, default=30,
                        help=('minimum distance to stay from the Moon, in degrees'
                              ' (default=%(default)d)'),
                        )

    args = parser.parse_args()
    n_alt = args.n_alt
    n_az = args.n_az
    num_exp = args.numexp
    exp_list = [float(i) for i in args.exptime.split(',')]
    filt = args.filter
    min_moonsep = args.minmoonsep

    run(n_alt, n_az, num_exp, exp_list, filt, min_moonsep)
