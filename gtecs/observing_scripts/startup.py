"""
startup
Script to run start of night tasks
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
from gtecs.observing import (filters_are_homed, cameras_are_cool)


def run():
    """
    Run startup tasks.
    """
    print('Running startup tasks')

    # Make sure the power daemon is running
    execute_command('power start')

    time.sleep(10)

    # Power on the FLI hardware and mount box
    for tel in params.TEL_DICT:
        execute_command('power on filt{}'.format(tel))
        execute_command('power on foc{}'.format(tel))
        execute_command('power on cam{}'.format(tel))
    execute_command('power on sitech')

    time.sleep(5)

    # Restart the FLI interface, as it would have crashed if the power was off
    execute_command('fli shutdown')
    time.sleep(5)
    execute_command('fli start')

    # Make sure all the other daemons are running
    execute_command('lilith start')

    time.sleep(10)

    # Unpark the mount
    execute_command('mnt unpark')

    # Don't set a target, we want to stay parked while opening
    #print('Setting target to Zenith')
    #lst = find_lst(Time.now()) * u.hourangle
    #obs = observatory_location()
    #ra = lst.to(u.deg)
    #dec = obs.lat.value
    #execute_command('mnt ra {}'.format(ra))
    #execute_command('mnt dec {}'.format(dec))
    #execute_command('mnt slew')
    #time.sleep(20)
    #execute_command('mnt info')

    # Clean up any persistent queue from previous night
    execute_command('exq clear')
    time.sleep(1)
    execute_command('exq resume')

    # Home the filter wheels
    execute_command('filt home')
    while not filters_are_homed():
        time.sleep(1)
    print('filt info')

    # Bring the CCDs down to temperature
    execute_command('cam temp {}'.format(params.CCD_TEMP))
    while not cameras_are_cool():
        time.sleep(1)
    execute_command('cam info')

    print('Startup tasks done')


if __name__ == "__main__":
    run()
