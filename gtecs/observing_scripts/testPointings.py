"""
testPointings [nAlt] [nAz]
Script to take images at a range of pointings
"""

import sys
import time

from astropy.time import Time

from gtecs import params
from gtecs.misc import execute_command as cmd
from gtecs.observing import (prepare_for_images, wait_for_exposure_queue,
                             goto_altaz, wait_for_telescope)


def take_image_set(expT, name):
    cmd('exq image {} L 1 "{}"'.format(expT, name))
    time.sleep(0.1)
    wait_for_exposure_queue()
    time.sleep(0.1)


def run(nAlt, nAz):
    # make sure hardware is ready
    prepare_for_images()

    # generate alt list
    if nAlt > 4:
        nAlt = 4
    alt_list = [45, 60, 75][:nAlt-1][::-1]

    # generate az list
    if nAz > 4:
        nAz = 4
    az_list = [i for i in range(0,360,int(360/nAz))]

    # generate pointings
    altaz_list = [(90, 0)] # don't repeat zenith at different azimuths
    for az in az_list:
        for alt in alt_list:
            altaz_list.append((alt, az))

    print('Generated {} AltAz pointings:'.format(len(altaz_list)))
    print(altaz_list)

    time.sleep(5)

    exposure_list = [15, 30, 60, 120, 240, 480]

    for altaz in altaz_list:
        alt, az = altaz
        print('Slewing to Alt {}, Az {}'.format(alt, az))
        goto_altaz(alt, az)
        time.sleep(10)
        wait_for_telescope(120, targ_dist=0.1)  # 120s timeout
                                                # lower distance for altaz

        for exp_time in exposure_list:
            take_image_set(exp_time, 'Test Pointing')

    print("Done")


if __name__ == "__main__":
    if len(sys.argv) == 1:
        nAlt = 2
        nAz = 2
    elif len(sys.argv) == 2:
        nAlt = int(sys.argv[1])
        nAz = 2
    else:
        nAlt = int(sys.argv[1])
        nAz = int(sys.argv[2])

    run(nAlt, nAz)
