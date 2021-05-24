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

from gtecs.misc import execute_command
from gtecs.observing import wait_for_dome, wait_for_mirror_covers, wait_for_mount_parking
from gtecs.slack import send_slack_msg


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

    # Power off the cameras and fans
    execute_command('power off cams,focs,filts,fans')

    # Park the mount
    execute_command('mnt park')
    try:
        wait_for_mount_parking(timeout=60)
    except TimeoutError:
        print('Mount timed out, continuing with shutdown')
        send_slack_msg('Shutdown script could not park the mount!')

    # Close the dome and wait (pilot will try again before shutdown)
    execute_command('dome close')
    try:
        wait_for_dome(target_position='closed', timeout=120)
    except TimeoutError:
        print('Dome timed out')
        send_slack_msg('Shutdown script could not close the dome!')


if __name__ == '__main__':
    run()
