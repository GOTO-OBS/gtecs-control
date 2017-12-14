"""
testPointings
Script to take images at a wide range of pointings for analysis
"""

import time

from astropy.time import Time

from gtecs import params
from gtecs.misc import execute_command as cmd
from gtecs.astronomy import radec_from_altaz
from gtecs.observing import (prepare_for_images, wait_for_exposure_queue,
                             goto, wait_for_telescope)


def take_image_set(expT, name):
    cmd('exq image {} L 1 "{}"'.format(expT, name))
    time.sleep(0.1)
    wait_for_exposure_queue()
    time.sleep(0.1)


def run():
    # make sure hardware is ready
    prepare_for_images()

    # generate altaz pointings
    alt_list = [60, 45]
    az_list = [0, 180]
    altaz_list = [(90, 0)] # don't repeat zenith at different azimuths
    for az in az_list:
        for alt in alt_list:
            altaz_list.append((alt, az))

    exposure_list = [15, 30, 60, 120, 240, 480]

    for altaz in altaz_list:
        alt, az = altaz
        print('Slewing to Alt {}, Az {}'.format(alt, az))
        ra, dec = radec_from_altaz(alt, az, Time.now())
        goto(ra, dec)
        time.sleep(10)
        wait_for_telescope(120)  # 120s timeout

        for exp_time in exposure_list:
            take_image_set(exp_time, 'Test Pointing')

    print("Done")


if __name__ == "__main__":
    run()
