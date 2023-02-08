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

    # Make sure hardware is powered on
    start_daemon('power')
    time.sleep(5)
    with daemon_proxy('power') as daemon:
        print('Powering on UT hardware')
        daemon.on(['cams', 'focs', 'filts', 'fans'])
        time.sleep(5)

        print('Powering on mount')
        if params.MOUNT_CLASS == 'SITECH':
            daemon.on(['sitech'])
        elif params.MOUNT_CLASS == 'ASA':
            daemon.on(['mount', 'tcu', 'asa_gateways'])
    time.sleep(10)

    # Make sure the interfaces are started before the other daemons
    for interface_id in params.INTERFACES:
        start_daemon(interface_id)
    time.sleep(10)
    execute_command('intf info')

    # Make all the other daemons are running
    # Note we can't shutdown and restart, because the daemons will die when this script ends
    for daemon_id in list(params.DAEMONS):
        start_daemon(daemon_id)
        time.sleep(1)

    time.sleep(4)

    # Start bringing the CCDs down to temperature first, since they can take a while
    print('Setting cameras to cool')
    with daemon_proxy('cam') as daemon:
        daemon.set_temperature('cool')
    cam_start_time = time.time()
    time.sleep(1)

    # Restart the mount daemon, to reconnect to the mount, and make sure the motors are on
    restart_daemon('mnt')
    time.sleep(10)
    if params.MOUNT_CLASS == 'ASA':
        with daemon_proxy('mnt') as daemon:
            daemon.power_motors('on')
    # Don't unpark the mount or set a target, we want to stay parked while opening
    # Instead make sure the mount is parked
    if not mount_is_parked():
        print('Parking mount')
        with daemon_proxy('mnt') as daemon:
            daemon.park()
        start_time = time.time()
        while not mount_is_parked():
            time.sleep(1)
            if (time.time() - start_time) > 60:
                raise TimeoutError('Mount parking timed out')
    execute_command('mnt info')

    # TODO: Isn't this just repeating "prepare_for_images"? Couldn't we call that here?

    # Clean up any persistent queue from previous night,
    # and cancel any exposures just in case we're restarting
    with daemon_proxy('exq') as daemon:
        daemon.clear()
        time.sleep(1)
        daemon.resume()
    with daemon_proxy('cam') as daemon:
        daemon.abort_exposure()
    time.sleep(4)

    # Home the filter wheels
    print('Homing filter wheels')
    with daemon_proxy('filt') as daemon:
        daemon.home_filters()
    start_time = time.time()
    while not filters_are_homed():
        time.sleep(1)
        if (time.time() - start_time) > 30:
            raise TimeoutError('Filter wheels timed out')
    execute_command('filt info -f')

    # Set the focusers
    print('Setting focusers')
    with daemon_proxy('foc') as daemon:
        daemon.move_focusers(10)
        time.sleep(4)
        daemon.move_focusers(-10)
    start_time = time.time()
    while not focusers_are_set():
        time.sleep(1)
        if (time.time() - start_time) > 60:
            raise TimeoutError('Focusers timed out')
    execute_command('foc info -f')

    # Set the cameras to full-frame
    print('Setting cameras to full-frame')
    with daemon_proxy('cam') as daemon:
        daemon.remove_window()
    time.sleep(4)

    # Don't open the mirror covers, because we want to do darks first
    # Instead make sure they are closed
    if mirror_covers_are_open():
        print('Closing mirror covers')
        with daemon_proxy('ota') as daemon:
            daemon.close_covers()
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
