#!/usr/bin/env python
"""Script to take Landolt standard star observations.

takeStandards

with a range of colours and airmasses.
"""

from astropy.time import Time

from gtecs import params
from gtecs.astronomy import check_alt_limit
from gtecs.catalogs import standard_star
from gtecs.observing import prepare_for_images, slew_to_radec, take_image_set, wait_for_mount


def run():
    """Run the standards routine."""
    # make sure hardware is ready
    prepare_for_images()

    airmasses = [1.0, 1.0, 1.3, 1.3, 1.8, 1.8]
    colours = [-0.5, 1, -0.5, 1, -0.5, 1.0]
    # use set so we don't duplicate observations
    stars = set([standard_star(Time.now(), airmass, colour)
                 for airmass, colour in zip(airmasses, colours)])
    print('Starting standard star routine')
    for star in stars:
        coordinate = star.coord_now()

        if check_alt_limit(coordinate.ra.deg, coordinate.dec.deg, Time.now()):
            print('Star ', star, ' is below limit')
            continue

        print('Slewing to star', star)
        name = star.name
        slew_to_radec(coordinate.ra.deg, coordinate.dec.deg)
        wait_for_mount(coordinate.ra.deg, coordinate.dec.deg, timeout=120)

        # take 20 second exposures in all filters
        take_image_set(20, params.FILTER_LIST, name, imgtype='STD')

    print("Done")


if __name__ == "__main__":
    run()
