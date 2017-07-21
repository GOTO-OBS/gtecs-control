"""
Script to run the tasks for Start Night Phase 1.

This script should perform the following simple tasks:
* start scope tracking and point to LST+4
"""
from __future__ import absolute_import
from __future__ import print_function
import time

from astropy.time import Time
from astropy.coordinates import Longitude
from astropy import units as u

from gtecs.tecs_modules.misc import execute_command as cmd
from gtecs.tecs_modules.astronomy import find_lst
from gtecs.tecs_modules.observing import wait_for_telescope, goto


def run():
    print('Start of Night Phase 2')
    cmd('mnt info')
    print('Setting RA to LST+4')
    ra_deg = Longitude(u.hour * (4 + find_lst(Time.now()))).deg

    goto(ra_deg, 23)
    wait_for_telescope(240)

    time.sleep(5)
    cmd('mnt info')


if __name__ == "__main__":
    run()