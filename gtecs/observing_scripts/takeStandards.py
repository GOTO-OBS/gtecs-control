"""
takeStandards
Script to take Landolt standard star observations
with a range of colours and airmasses.
"""

import time

from astropy.time import Time

from gtecs import params
from gtecs.misc import execute_command
from gtecs.astronomy import check_alt_limit
from gtecs.observing import (prepare_for_images, take_image_set,
                             goto, wait_for_telescope)
from gtecs.catalogs import landolt


def run():
    # make sure hardware is ready
    prepare_for_images()

    airmasses = [1.0, 1.0, 1.3, 1.3, 1.8, 1.8]
    colours = [-0.5, 1, -0.5, 1, -0.5, 1.0]
    # use set so we don't duplicate observations
    stars = set([landolt.standard_star(Time.now(), airmass, colour)
                 for airmass, colour in zip(airmasses, colours)])
    print('Starting standard star routine')
    for star in stars:
        coordinate = star.coord_now()

        if check_alt_limit(coordinate.ra.deg, coordinate.dec.deg, Time.now()):
            print('Star ', star, ' is below limit')
            continue

        print('Slewing to star', star)
        name = star.name
        goto(coordinate.ra.deg, coordinate.dec.deg)
        time.sleep(10)
        wait_for_telescope(120)  # 120s timeout

        # take 20 second exposures in all filters
        take_image_set(20, params.FILTER_LIST, name, imgtype='STD')

    print("Done")


if __name__ == "__main__":
    run()
