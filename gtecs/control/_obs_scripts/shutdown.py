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

from gtecs.control.daemons import daemon_proxy
from gtecs.control.observing import wait_for_dome, wait_for_mirror_covers, wait_for_mount_parking
from gtecs.control.slack import send_slack_msg


def run():
    """Run shutdown tasks."""
    print('Running shutdown tasks')

    # Pause and clear the exposure queue
    with daemon_proxy('exq') as daemon:
        reply = daemon.pause()
        print(reply)
        time.sleep(1)
        reply = daemon.clear()
        print(reply)

    # Abort any current exposures
    with daemon_proxy('cam') as daemon:
        reply = daemon.abort_exposure()
        print(reply)

    # Close the mirror covers
    # (we need to do this before powering off the cameras, when we lose the interfaces)
    with daemon_proxy('ota') as daemon:
        reply = daemon.close_covers()
        print(reply)
    try:
        wait_for_mirror_covers(opening=False, timeout=60)
    except TimeoutError:
        print('Mirror covers timed out, continuing with shutdown')
        send_slack_msg('Shutdown script could not close the mirror covers!')

    # Set camera temps to warm during the day
    with daemon_proxy('cam') as daemon:
        reply = daemon.set_temperature('warm')
        print(reply)

    # Park the mount
    with daemon_proxy('mnt') as daemon:
        reply = daemon.park()
        print(reply)
    try:
        wait_for_mount_parking(timeout=60)
    except TimeoutError:
        print('Mount timed out, continuing with shutdown')
        send_slack_msg('Shutdown script could not park the mount!')
    # Leave the mount motors on, so it can't move accidentally (e.g. in the wind)
    # Note we don't power off the mount control computers here,
    # we just make sure they are powered on during startup

    # Close the dome and wait (pilot will try again before shutdown)
    with daemon_proxy('dome') as daemon:
        reply = daemon.close_dome()
        print(reply)
    try:
        wait_for_dome(target_position='closed', timeout=120)
    except TimeoutError:
        print('Dome timed out')
        send_slack_msg('Shutdown script could not close the dome!')


if __name__ == '__main__':
    run()
