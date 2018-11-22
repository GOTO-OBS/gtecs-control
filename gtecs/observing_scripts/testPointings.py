#!/usr/bin/env python
"""Script to take images at a range of pointings.

testPointings [n_alt] [n_az]
"""

import sys
import time

from gtecs.observing import prepare_for_images, slew_to_altaz, take_image_set, wait_for_mount


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
        slew_to_altaz(alt, az)
        time.sleep(10)
        wait_for_mount(timeout=120, targ_dist=0.1)  # lower distance for altaz

        take_image_set(exposure_list, 'L', 'Test Pointing')

    print('Done')


if __name__ == "__main__":
    if len(sys.argv) == 1:
        n_alt = 2
        n_az = 2
    elif len(sys.argv) == 2:
        n_alt = int(sys.argv[1])
        n_az = 2
    else:
        n_alt = int(sys.argv[1])
        n_az = int(sys.argv[2])

    run(n_alt, n_az)
