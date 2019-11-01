#!/usr/bin/env python
"""Script to run end of night tasks.

shutdown

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
from gtecs.observing import wait_for_dome, wait_for_mount_parking


def run():
    """Run shutdown tasks."""
    print('Running shutdown tasks')

    # Pause and clear the exposure queue
    execute_command('exq pause')
    time.sleep(1)
    execute_command('exq clear')

    # Abort any current exposures
    execute_command('cam abort')

    # Power off the cameras
    execute_command('power off cams')

    # Park the mount
    execute_command('mnt park')
    wait_for_mount_parking(timeout=60)

    # Close the dome and wait (pilot will try again before shutdown)
    execute_command('dome close')
    wait_for_dome(target_position='closed', timeout=120)


if __name__ == "__main__":
    run()
