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
import traceback

from gtecs.control.daemons import daemon_proxy
from gtecs.control.observing import wait_for_dome, wait_for_mirror_covers, wait_for_mount_parking
from gtecs.control.slack import send_slack_msg


def run():
    """Run shutdown tasks."""
    print('Running shutdown tasks')

    # Pause and clear the exposure queue & abort any ongoing images
    try:
        with daemon_proxy('exq') as daemon:
            daemon.pause()
            time.sleep(1)
            daemon.clear()
        with daemon_proxy('cam') as daemon:
            daemon.abort_exposure()
    except Exception:
        print('Failed to clear image queue, continuing with shutdown')
        traceback.print_exc()
        send_slack_msg('Shutdown script could not clear the exposure queue!')

    # Close the mirror covers
    print('Closing mirror covers')
    try:
        with daemon_proxy('ota') as daemon:
            daemon.close_covers()
        time.sleep(2)
        wait_for_mirror_covers(opening=False, timeout=60)
        print('  Mirror covers closed')
    except Exception:
        print('Failed to close mirror covers, continuing with shutdown')
        traceback.print_exc()
        send_slack_msg('Shutdown script could not close the mirror covers!')

    # Set camera temps to warm during the day
    print('Setting cameras to warm')
    try:
        with daemon_proxy('cam') as daemon:
            daemon.set_temperature('warm')
        # We don't need to wait for them to warm up
        print('  Camera temperature set')
    except Exception:
        print('Failed to warm cameras, continuing with shutdown')
        traceback.print_exc()
        send_slack_msg('Shutdown script could not warm the cameras!')

    # Park the mount
    print('Parking the mount')
    try:
        with daemon_proxy('mnt') as daemon:
            daemon.park()
        time.sleep(2)
        wait_for_mount_parking(timeout=60)
        print('  Mount parked')
    except Exception:
        print('Failed to park the mount, continuing with shutdown')
        traceback.print_exc()
        send_slack_msg('Shutdown script could not park the mount!')

    # Close the dome and wait (pilot will try again before shutdown)
    print('Closing the dome')
    try:
        with daemon_proxy('dome') as daemon:
            daemon.close_dome()
        time.sleep(2)
        wait_for_dome(target_position='closed', timeout=120)
        print('  Dome closed')
    except TimeoutError:
        print('Failed to close the dome!')
        traceback.print_exc()
        send_slack_msg('Shutdown script could not close the dome!')


if __name__ == '__main__':
    run()
