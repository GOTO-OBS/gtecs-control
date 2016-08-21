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
                                          wait_for_telescope)
from gtecs.tecs_modules import params


def take_image_set(expT, name):
    for filt in params.FILTER_LIST:
        cmd('exq image {} {} 1 "{}" STD'.format(
            expT, filt, name
        ))
    time.sleep(0.1)
    wait_for_exposure_queue()
    time.sleep(0.1)


if __name__ == "__main__":
    airmasses = [1.0, 1.0, 1.3, 1.3, 1.8, 1.8]
    colours = [-0.5, 1, -0.5, 1, -0.5, 1.0]

    print('Starting standard star routine')
    for colour, airmass in zip(airmasses, colours):
        star = landolt.standard_star(Time.now(), airmass, colour)

        print('Slewing to star', star)
        name = star.name

        coordinate = star.coord_now()
        goto(coordinate.ra.deg, coordinate.dec.deg)
        time.sleep(10)
        wait_for_telescope(480)  # 480s timeout

        take_image_set(20, name)

    print("Done")
