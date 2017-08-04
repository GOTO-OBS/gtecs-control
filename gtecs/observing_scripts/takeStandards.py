"""
Script to take Landolt standard star observations with a range of colours and airmasses.
"""
from __future__ import absolute_import
from __future__ import print_function
import time

from astropy.time import Time

from gtecs.tecs_modules.misc import execute_command as cmd
from gtecs.catalogs import landolt
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          last_written_image, goto,
                                          filters_are_homed,
                                          wait_for_telescope)
from gtecs.tecs_modules import params
from gtecs.tecs_modules import astronomy as ast


def take_image_set(expT, name):
    for filt in params.FILTER_LIST:
        cmd('exq image {} {} 1 "{}" STD'.format(
            expT, filt, name
        ))
    time.sleep(0.1)
    wait_for_exposure_queue()
    time.sleep(0.1)


if __name__ == "__main__":

    if not filters_are_homed():
        print('homing filters')
        time.sleep(1)
        while not filters_are_homed():
            time.sleep(1)

    airmasses = [1.0, 1.0, 1.3, 1.3, 1.8, 1.8]
    colours = [-0.5, 1, -0.5, 1, -0.5, 1.0]
    # use set so we don't duplicate observations
    stars = set([landolt.standard_star(Time.now(), airmass, colour)
                 for airmass, colour in zip(airmasses, colours)])
    print('Starting standard star routine')
    for star in stars:
        coordinate = star.coord_now()

        if ast.check_alt_limit(coordinate.ra.deg,
                               coordinate.dec.deg,
                               Time.now()):
            print('Star ', star, ' is below limit')
            continue

        print('Slewing to star', star)
        name = star.name
        goto(coordinate.ra.deg, coordinate.dec.deg)
        time.sleep(10)
        wait_for_telescope(120)  # 120s timeout

        take_image_set(20, name)

    print("Done")
