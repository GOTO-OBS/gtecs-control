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
from gtecs.tecs_modules.astronomy import find_lst, observatory_location
from gtecs.tecs_modules.observing import wait_for_telescope, goto
from gtecs.tecs_modules import params


def run():
    print('Start of Night Phase 2')

    # clean up persistent queue from previous night
    cmd('exq clear')
    time.sleep(1)
    cmd('exq resume')

    # home the wheels
    cmd('filt home')

    print('Moving telescope to safe position')
    if params.FREEZE_DEC:
        cmd('mnt stop')
    else:
        cmd('mnt park')

    print('Setting target to Zenith')
    lst = find_lst(Time.now()) * u.hourangle
    obs = observatory_location()
    ra = lst.to(u.deg)
    dec = obs.lat.value
    cmd('mnt ra {}'.format(ra))
    cmd('mnt dec {}'.format(dec))

    time.sleep(5)
    cmd('mnt info')


if __name__ == "__main__":
    run()