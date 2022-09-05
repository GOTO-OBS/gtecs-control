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

from gtecs.common.system import execute_command
from gtecs.control import params
from gtecs.control.observing import cameras_are_cool, filters_are_homed, focusers_are_set


def run():
    """Run startup tasks."""
    print('Running startup tasks')

    # Make sure the power daemon is running first
    execute_command('power start')
    time.sleep(5)

    # Power on the cams, focusers etc
    execute_command('power on cams,focs,filts,fans')
    time.sleep(5)

    # Ensure the mount control computers are on
    # Note we don't power them off during shutdown, we just double-check they are on here
    if params.MOUNT_CLASS == 'SITECH':
        execute_command('power on sitech')
    elif params.MOUNT_CLASS == 'ASA':
        execute_command('power on mount,tcu,asa_gateways')
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
    time.sleep(10)
    execute_command('intf info')

    # Make all the other daemons are running
    # Note we can't shutdown and restart, because the daemons will die when this script ends
    for daemon_id in list(params.DAEMONS):
        if daemon_id not in params.INTERFACES:
            execute_command('{} start'.format(daemon_id))
            time.sleep(1)

    time.sleep(4)

    # Restart the mount daemon, to reconnect to the mount, and power on the motors
    execute_command('mnt restart')
    time.sleep(10)
    execute_command('mnt info')
    time.sleep(1)
    if params.MOUNT_CLASS == 'ASA':
        execute_command('mnt motors on')

    # Don't unpark the mount or set a target, we want to stay parked while opening
    # execute_command('mnt unpark')
    # print('Setting target to Zenith')
    # execute_command('mnt slew_altaz 89 0')
    # time.sleep(20)
    # execute_command('mnt info -f')

    # Clean up any persistent queue from previous night
    execute_command('exq clear')
    time.sleep(1)
    execute_command('exq resume')

    # Home the filter wheels
    execute_command('filt home')
    while not filters_are_homed():
        time.sleep(1)
    execute_command('filt info -f')

    # Set the focusers
    execute_command('foc move 10')
    time.sleep(4)
    execute_command('foc move -10')
    while not focusers_are_set():
        time.sleep(1)
    execute_command('foc info -f')

    # Set the cameras to full-frame
    execute_command('cam window full')
    time.sleep(4)  # need a long sleep or the commands will interfere?

    # Bring the CCDs down to temperature
    execute_command('cam temp {}'.format(params.CCD_TEMP))
    while not cameras_are_cool():
        time.sleep(1)
    execute_command('cam info -f')

    # Don't open the mirror covers, because we want to do darks first
    # execute_command('ota open')
    # while not mirror_covers_are_open():
    #     time.sleep(1)
    # execute_command('ota info -f')

    print('Startup tasks done')


if __name__ == '__main__':
    run()
