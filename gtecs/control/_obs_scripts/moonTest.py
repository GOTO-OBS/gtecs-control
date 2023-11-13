#!/usr/bin/env python3
"""Script to take images over the sky, avoiding the Moon."""

import sys

from astropy.time import Time

from gtecs.control.astronomy import get_moon_distance, radec_from_altaz
from gtecs.control.observing import prepare_for_images, slew_to_radec, take_image_set


def run():
    """Run the moon test routine."""
    # make sure hardware is ready
    prepare_for_images()

    # generate alt list
    # if n_alt > 4:
    #     n_alt = 4
    alt_list = [40, 50, 60, 75]

    # generate az list
    # if n_az > 4:
    #     n_az = 4
    n_az = 8
    az_list = [i for i in range(0, 360, int(360 / n_az))]

    # generate pointings
    altaz_list = [(90, 0)]  # don't repeat zenith at different azimuths
    for az in az_list:
        for alt in alt_list:
            altaz_list.append((alt, az))

    print('Generated {} AltAz pointings:'.format(len(altaz_list)))
    print(altaz_list)

    exposure_list = [60]  # [15, 30, 60, 120, 240, 480]

    print('Exposure times:')
    print(exposure_list)

    total_exptime = (sum(exposure_list) * len(altaz_list)) / 60.
    print('Total exposure time: {} mins'.format(total_exptime))

    total_readout = 0.5 * len(exposure_list) * len(altaz_list)
    total_slew = 0.5 * len(altaz_list)
    print('Estimated total time: {} mins'.format(total_exptime + total_readout + total_slew))

    cont = 'na'
    while cont.lower() not in ['', 'y', 'n']:
        cont = input('Continue? [Y/n]: ')
    if cont.lower() == 'n':
        sys.exit()

    print('OK, starting routine...')

    for altaz in altaz_list:
        t = Time.now()
        print(t)

        alt, az = altaz
        print('Slewing to Alt {}, Az {}'.format(alt, az))

        # need to check distance to the moon
        ra, dec = radec_from_altaz(alt, az, t)
        moon_dist = get_moon_distance(ra, dec, t)
        print('Distance to Moon = {}'.format(moon_dist))
        if moon_dist < 30:
            print('Too close (<30), skipping to next target...')
            continue

        # Slew to position
        slew_to_radec(ra, dec, timeout=120)

        take_image_set(exposure_list, 'L', 'Moon Test Pointing')

    print("Done")


if __name__ == '__main__':
    # if len(sys.argv) == 1:
    #     n_alt = 4
    #     n_az = 4
    # elif len(sys.argv) == 2:
    #     n_alt = int(sys.argv[1])
    #     n_az = 2
    # else:
    #     n_alt = int(sys.argv[1])
    #     n_az = int(sys.argv[2])

    run()
