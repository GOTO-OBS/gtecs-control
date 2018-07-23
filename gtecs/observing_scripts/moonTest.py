"""
moonTest
Script to take images over the sky, avoiding the Moon
"""

import sys
import time

from astropy.time import Time

from gtecs import params
from gtecs.misc import execute_command
from gtecs.astronomy import get_moon_distance, radec_from_altaz
from gtecs.observing import (prepare_for_images, take_image_set,
                             goto_altaz, wait_for_telescope)


def run():
    # make sure hardware is ready
    prepare_for_images()

    # generate alt list
    #if nAlt > 4:
    #    nAlt = 4
    alt_list = [40, 50, 60, 75]

    # generate az list
#    if nAz > 4:
#        nAz = 4
    nAz = 8
    az_list = [i for i in range(0,360,int(360/nAz))]

    # generate pointings
    altaz_list = [(90, 0)] # don't repeat zenith at different azimuths
    for az in az_list:
        for alt in alt_list:
            altaz_list.append((alt, az))

    print('Generated {} AltAz pointings:'.format(len(altaz_list)))
    print(altaz_list)

    exposure_list = [60]#[15, 30, 60, 120, 240, 480]

    print('Exposure times:')
    print(exposure_list)

    total_exptime = (sum(exposure_list) * len(altaz_list))/60.
    print('Total exposure time: {} mins'.format(total_exptime))

    total_readout = 0.5 * len(exposure_list) * len(altaz_list)
    total_slew = 0.5 * len(altaz_list)
    print('Estimated total time: {} mins'.format(total_exptime + total_readout + total_slew))

    cont = 'na'
    while cont not in ['y','n']:
        cont = input('Continue? [y/n]: ')
    if cont == 'n':
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
        print('Distane to Moon = {}'.format(moon_dist))
        if moon_dist < 30:
            print('Too close (<30), skipping to next target...')
            continue

        goto_altaz(alt, az)
        time.sleep(1)#10)
        wait_for_telescope(120, targ_dist=0.1)  # 120s timeout
                                                # lower distance for altaz

        take_image_set(exposure_list, 'L', 'Moon Test Pointing')

    print("Done")


if __name__ == "__main__":
#    if len(sys.argv) == 1:
#        nAlt = 4
#        nAz = 4
#    elif len(sys.argv) == 2:
#        nAlt = int(sys.argv[1])
#        nAz = 2
#    else:
#        nAlt = int(sys.argv[1])
#        nAz = int(sys.argv[2])

    run()
