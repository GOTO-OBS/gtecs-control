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

from gtecs.common.system import execute_command, restart_daemon, start_daemon
from gtecs.control import params
from gtecs.control.daemons import daemon_proxy
from gtecs.control.observing import (cameras_are_cool, filters_are_homed, focusers_are_set,
                                     mirror_covers_are_open, mount_is_parked)


def run():
    """Run startup tasks."""
    print('Running startup tasks')

    # Make sure the power daemon is running first
    reply = start_daemon('power')
    print(reply)
    time.sleep(5)

    with daemon_proxy('power') as daemon:
        # Power on the cams, focusers etc
        reply = daemon.on(['cams', 'focs', 'filts', 'fans'])
        print(reply)
        time.sleep(5)

        # Ensure the mount control computers are on
        # Note we don't power them off during shutdown, we just double-check they are on here
        if params.MOUNT_CLASS == 'SITECH':
            reply = daemon.on(['sitech'])
            print(reply)
        elif params.MOUNT_CLASS == 'ASA':
            reply = daemon.on(['mount', 'tcu', 'asa_gateways'])
            print(reply)
    time.sleep(10)

    # Make sure the interfaces are started before the other daemons
    for interface_id in params.INTERFACES:
        reply = start_daemon(interface_id)
        print(reply)
    time.sleep(10)
    execute_command('intf info')

    # Make all the other daemons are running
    # Note we can't shutdown and restart, because the daemons will die when this script ends
    for daemon_id in list(params.DAEMONS):
        reply = start_daemon(daemon_id)
        print(reply)
        time.sleep(1)

    time.sleep(4)

    # Start bringing the CCDs down to temperature first, since they can take a while
    with daemon_proxy('cam') as daemon:
        reply = daemon.set_temperature('cool')
        print(reply)
    cam_start_time = time.time()
    time.sleep(1)

    # Restart the mount daemon, to reconnect to the mount, and make sure the motors are on
    reply = restart_daemon('mnt')
    print(reply)
    time.sleep(10)
    if params.MOUNT_CLASS == 'ASA':
        with daemon_proxy('mnt') as daemon:
            reply = daemon.power_motors('on')
            print(reply)
    # Don't unpark the mount or set a target, we want to stay parked while opening
    # Instead make sure the mount is parked
    if not mount_is_parked():
        with daemon_proxy('mnt') as daemon:
            reply = daemon.park()
            print(reply)
        start_time = time.time()
        while not mount_is_parked():
            time.sleep(1)
            if (time.time() - start_time) > 60:
                raise TimeoutError('Mount parking timed out')
    execute_command('mnt info')

    # Clean up any persistent queue from previous night,
    # and cancel any exposures just in case we're restarting
    with daemon_proxy('exq') as daemon:
        reply = daemon.clear()
        print(reply)
        time.sleep(1)
        reply = daemon.resume()
        print(reply)
    time.sleep(1)
    with daemon_proxy('cam') as daemon:
        reply = daemon.abort_exposure()
        print(reply)
    time.sleep(4)

    # Home the filter wheels
    with daemon_proxy('filt') as daemon:
        reply = daemon.home_filters()
        print(reply)
    start_time = time.time()
    while not filters_are_homed():
        time.sleep(1)
        if (time.time() - start_time) > 30:
            raise TimeoutError('Filter wheels timed out')
    execute_command('filt info -f')

    # Set the focusers
    with daemon_proxy('foc') as daemon:
        reply = daemon.move_focusers(10)
        print(reply)
        time.sleep(4)
        reply = daemon.move_focusers(-10)
        print(reply)
    start_time = time.time()
    while not focusers_are_set():
        time.sleep(1)
        if (time.time() - start_time) > 60:
            raise TimeoutError('Focusers timed out')
    execute_command('foc info -f')

    # Set the cameras to full-frame
    with daemon_proxy('cam') as daemon:
        reply = daemon.remove_window()
        print(reply)
    time.sleep(4)

    # Don't open the mirror covers, because we want to do darks first
    # Instead make sure they are closed
    if mirror_covers_are_open():
        with daemon_proxy('ota') as daemon:
            reply = daemon.close_covers()
            print(reply)
        start_time = time.time()
        while mirror_covers_are_open():
            time.sleep(1)
            if (time.time() - start_time) > 60:
                raise TimeoutError('Mirror covers timed out')
    execute_command('ota info -f')

    # Finally check that the CCDs are cool
    while not cameras_are_cool():
        time.sleep(1)
        if (time.time() - cam_start_time) > 600:
            raise TimeoutError('Camera cooling timed out')
    execute_command('cam info -f')

    print('Startup tasks done')


if __name__ == '__main__':
    run()
