#!/usr/bin/env python
"""Script to run start of night tasks.

startup

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

    time.sleep(10)

    # Power on the FLI hardware and mount box
    for tel in sorted(params.TEL_DICT):
        execute_command('power on cam{0},foc{0},filt{0}'.format(tel))
        time.sleep(0.5)
    execute_command('power on sitech')

    time.sleep(5)

    # Restart the FLI interface, as it would have crashed if the power was off
    # Note don't use the restart command, I don't trust it any more
    execute_command('fli shutdown')
    time.sleep(4)
    execute_command('fli start')
    time.sleep(1)

    # Make sure all the other daemons are running
    for daemon in list(params.DAEMONS):
        # don't run the individual interfaces
        if daemon not in params.FLI_INTERFACES:
            execute_command('{} start'.format(daemon))

    time.sleep(10)

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

    print('Startup tasks done')


if __name__ == "__main__":
    run()
