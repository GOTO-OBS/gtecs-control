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

from gtecs.control import params
from gtecs.control.daemons import daemon_proxy, start_daemon


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
        # TODO: blocking command with confirmation or timeout in daemon

        print('Powering on mount')
        if params.MOUNT_CLASS == 'SITECH':
            daemon.on(['sitech'])
        elif params.MOUNT_CLASS == 'ASA':
            daemon.on(['mount', 'tcu', 'asa_gateways'])
    time.sleep(10)

    # Make sure the interfaces are started before the other daemons
    for interface_id in params.INTERFACES:
        print(f'Starting daemon: {interface_id}')
        start_daemon(interface_id, timeout=10)
        time.sleep(1)
    time.sleep(10)

    # Make all the other daemons are running
    # Note we can't shutdown and restart, because the daemons will die when this script ends
    for daemon_id in list(params.DAEMONS):
        print(f'Starting daemon: {daemon_id}')
        start_daemon(daemon_id, timeout=10)
        time.sleep(1)

    time.sleep(4)

    # Start bringing the CCDs down to temperature first, since they can take a while
    print('Setting cameras to cool')
    with daemon_proxy('cam') as daemon:
        daemon.set_temperature('cool')
    cam_start_time = time.time()
    time.sleep(1)

    # Restart the mount daemon, to reconnect to the mount, and make sure the motors are on
    if params.MOUNT_CLASS == 'ASA':
        with daemon_proxy('mnt') as daemon:
            daemon.power_motors('on')
            time.sleep(5)
            # Don't unpark the mount or set a target, we want to stay parked while opening
            # Instead make sure the mount is parked
            info = daemon.get_info(force_update=True)
            if info['status'] not in ['Parked', 'IN BLINKY MODE', 'MOTORS OFF']:
                print('Parking mount')
                daemon.park()
                # TODO: blocking command with confirmation or timeout in daemon
                start_time = time.time()
                while True:
                    time.sleep(0.5)
                    info = daemon.get_info(force_update=True)
                    if info['status'] in ['Parked', 'IN BLINKY MODE', 'MOTORS OFF']:
                        break
                    if (time.time() - start_time) > 60:
                        raise TimeoutError('Mount parking timed out')
            info_str = daemon.get_info_string(force_update=True)
            print(info_str)

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
        # TODO: blocking command with confirmation or timeout in daemon
        start_time = time.time()
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            if all(info[ut]['homed'] for ut in info['uts']):
                break
            if (time.time() - start_time) > 30:
                raise TimeoutError('Filter wheels timed out')

        info_str = daemon.get_info_string(force_update=True)
        print(info_str)

    # Set the focusers
    print('Setting focusers')
    with daemon_proxy('foc') as daemon:
        daemon.move_focusers(10)
        time.sleep(4)
        daemon.move_focusers(-10)
        # TODO: blocking command with confirmation or timeout in daemon
        start_time = time.time()
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            if not any(info[ut]['status'] == 'UNSET' for ut in info['uts']):
                break
            if (time.time() - start_time) > 60:
                raise TimeoutError('Focusers timed out')

        info_str = daemon.get_info_string(force_update=True)
        print(info_str)

    # Set the cameras to full-frame
    print('Setting cameras to full-frame')
    with daemon_proxy('cam') as daemon:
        daemon.remove_window()
    time.sleep(4)

    # Don't open the mirror covers, because we want to do darks first
    # Instead make sure they are closed
    with daemon_proxy('ota') as daemon:
        daemon.close_covers()
        # TODO: blocking command with confirmation or timeout in daemon
        start_time = time.time()
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            closed_covers = sum([info[ut]['position'] == 'closed'
                                    for ut in info['uts_with_covers']])
            if closed_covers > params.MIN_COVER_STATUS:
                break
            if (time.time() - start_time) > 60:
                raise TimeoutError('Mirror covers timed out')

        info_str = daemon.get_info_string(force_update=True)
        print(info_str)

    # Finally check that the CCDs are cool
    with daemon_proxy('cam') as daemon:
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            if all(info[ut]['ccd_temp'] < info[ut]['target_temp'] + 1 for ut in info['uts']):
                break
            if (time.time() - cam_start_time) > 600:
                raise TimeoutError('Camera cooling timed out')

        info_str = daemon.get_info_string(force_update=True)
        print(info_str)

    print('Startup tasks done')


if __name__ == '__main__':
    run()
