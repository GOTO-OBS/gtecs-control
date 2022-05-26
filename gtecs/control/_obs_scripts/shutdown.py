#!/usr/bin/env python3
"""Script to run end of night tasks.

This script should perform the following simple tasks:
    * empty the camera queues
    * abort any current exposures
    * shutdown the interfaces
    * power off the hardware
    * park the mount
    * close the dome
"""

import time

from gtecs.common.system import execute_command
from gtecs.control import params
from gtecs.control.observing import wait_for_dome, wait_for_mirror_covers, wait_for_mount_parking
from gtecs.control.slack import send_slack_msg


def run():
    """Run shutdown tasks."""
    print('Running shutdown tasks')

    # Pause and clear the exposure queue
    execute_command('exq pause')
    time.sleep(1)
    execute_command('exq clear')

    # Abort any current exposures
    execute_command('cam abort')

    # Close the mirror covers
    # (we need to do this before powering off the cameras, when we lose the interfaces)
    execute_command('ota close')
    try:
        wait_for_mirror_covers(opening=False, timeout=60)
    except TimeoutError:
        print('Mirror covers timed out, continuing with shutdown')
        send_slack_msg('Shutdown script could not close the mirror covers!')

    # # Shutdown the interfaces (kill to be sure, they can be sticky sometimes)
    # execute_command('intf shutdown')
    # time.sleep(2)
    # execute_command('intf kill')
    # time.sleep(2)

    # # Power off the cameras, focusers etc
    # execute_command('power off cams,focs,filts,fans')
    # if params.MOUNT_CLASS == 'ASA':
    #     execute_command('power off asa_gateways')

    # Set camera temps to 0 (don't shutdown any more)
    execute_command('cam temp 0')

    # Park the mount
    execute_command('mnt park')
    try:
        wait_for_mount_parking(timeout=60)
    except TimeoutError:
        print('Mount timed out, continuing with shutdown')
        send_slack_msg('Shutdown script could not park the mount!')
    # Power off the mount motors
    if params.MOUNT_CLASS == 'ASA':
        execute_command('mnt motors off')
    # Note we don't power off the mount control computers here,
    # we just make sure they are powered on during startup

    # Close the dome and wait (pilot will try again before shutdown)
    execute_command('dome close')
    try:
        wait_for_dome(target_position='closed', timeout=120)
    except TimeoutError:
        print('Dome timed out')
        send_slack_msg('Shutdown script could not close the dome!')


if __name__ == '__main__':
    run()
