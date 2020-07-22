#!/usr/bin/env python3
"""Script to run start of night tasks.

These are hardware tasks to do BEFORE the dome opens:
    * power on the hardware
    * make sure the daemons are running
    * empty the camera queues
    * home the filter wheels
    * unpark the mount (but don't move anywhere yet)
    * bring the cameras down to the target temperature
"""

import time

from gtecs import params
from gtecs.misc import execute_command
from gtecs.observing import cameras_are_cool, filters_are_homed


def run():
    """Run startup tasks."""
    print('Running startup tasks')

    # Make sure the power daemon is running
    execute_command('power start')

    time.sleep(5)

    # Power on the UT hardware and mount box
    execute_command('power on cams,focs,filts')
    time.sleep(0.5)
    execute_command('power on sitech')

    time.sleep(10)

    # Restart the UT interfaces
    # We shouldn't need to do this, they should be fine running while the hardware is powered off
    # However sometimes it seems there are errors, so we do this just to be sure
    execute_command('intf shutdown')
    time.sleep(2)
    execute_command('intf kill')  # Just in case they failed to shutdown
    time.sleep(2)

    # Make sure the interfaces are started before the other daemons
    execute_command('intf start')
    time.sleep(1)

    # Make all the other daemons are running
    for daemon_id in list(params.DAEMONS):
        if daemon_id not in params.INTERFACES:
            execute_command('{} start'.format(daemon_id))
            time.sleep(0.5)

    time.sleep(4)

    # Don't unpark the mount or set a target, we want to stay parked while opening
    # execute_command('mnt unpark')
    # print('Setting target to Zenith')
    # lst = find_lst(Time.now()) * u.hourangle
    # obs = observatory_location()
    # ra = lst.to(u.deg)
    # dec = obs.lat.value
    # execute_command('mnt ra {}'.format(ra))
    # execute_command('mnt dec {}'.format(dec))
    # execute_command('mnt slew')
    # time.sleep(20)
    # execute_command('mnt info')

    # Clean up any persistent queue from previous night
    execute_command('exq clear')
    time.sleep(1)
    execute_command('exq resume')

    # Home the filter wheels
    execute_command('filt home')
    while not filters_are_homed():
        time.sleep(1)
    execute_command('filt info')

    # Bring the CCDs down to temperature
    execute_command('cam temp {}'.format(params.CCD_TEMP))
    while not cameras_are_cool():
        time.sleep(1)
    execute_command('cam info')

    # Don't open the mirror covers, because we want to do darks first
    # execute_command('ota open')
    # while not mirror_covers_are_open():
    #     time.sleep(1)
    # execute_command('ota info')

    print('Startup tasks done')


if __name__ == '__main__':
    run()
