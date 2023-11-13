#!/usr/bin/env python3
"""Script to take Landolt standard star observations.

with a range of colours and airmasses.
"""

from astropy.time import Time

from gtecs.control.catalogs import standard_star
from gtecs.control.observing import prepare_for_images, slew_to_radec, take_image_set


def run():
    """Run the standards routine."""
    # make sure hardware is ready
    prepare_for_images()

    airmasses = [1.0, 1.0, 1.3, 1.3, 1.8, 1.8]
    colours = [-0.5, 1, -0.5, 1, -0.5, 1.0]
    # use set so we don't duplicate observations
    now = Time.now()
    stars = {standard_star(airmass, colour, time=now)
             for airmass, colour in zip(airmasses, colours)}
    print('Starting standard star routine')
    for star in stars:
        coordinate = star.coord_now()

        print('Slewing to star', star)
        name = star.name
        slew_to_radec(coordinate.ra.deg, coordinate.dec.deg, timeout=120)

        # take 20 second exposures in all filters
        take_image_set(20, ['L', 'R', 'G', 'B', 'C'], name, imgtype='STD')

    print('Done')


if __name__ == '__main__':
    run()
