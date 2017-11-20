"""
Script to run startup tasks at the start of the night.
These are hardware tasks to do BEFORE the dome opens:
    * power on the hardware
    * make sure the daemons are running
    * empty the camera queues
    * home the filter wheels
    * unpark the mount (but don't move anywhere yet)
    * bring the cameras down to the target temperature
"""
from __future__ import absolute_import
from __future__ import print_function

import time

from gtecs.tecs_modules import params
from gtecs.tecs_modules.misc import execute_command as cmd


def run():
    """
    Run startup tasks.
    """
    print('Running startup tasks')

    # Make sure the power daemon is running
    cmd('power start')

    time.sleep(10)

    # Power on the FLI hardware and mount box
    for tel in params.TEL_DICT:
        cmd('power on filt{}'.format(tel))
        cmd('power on foc{}'.format(tel))
        cmd('power on cam{}'.format(tel))
    cmd('power on sitech')

    time.sleep(5)

    # Restart the FLI interface, as it would have crashed if the power was off
    cmd('fli shutdown')
    time.sleep(5)
    cmd('fli start')

    # Make sure all the other daemons are running
    cmd('lilith start')

    time.sleep(10)

    # Unpark the mount
    cmd('mnt unpark')

    # Don't set a target, we want to stay parked while opening
    #print('Setting target to Zenith')
    #lst = find_lst(Time.now()) * u.hourangle
    #obs = observatory_location()
    #ra = lst.to(u.deg)
    #dec = obs.lat.value
    #cmd('mnt ra {}'.format(ra))
    #cmd('mnt dec {}'.format(dec))
    #cmd('mnt slew')
    #time.sleep(20)
    #cmd('mnt info')

    # Clean up any persistent queue from previous night
    cmd('exq clear')
    time.sleep(1)
    cmd('exq resume')

    # Home the filter wheels
    cmd('filt home')

    # Bring the CCDs down to temperature
    cmd('cam temp {}'.format(params.CCD_TEMP))
    cool = False
    while not cool:
        cool = np.all(pd.Series(get_cam_temps()) < ccd_temp + 0.1)
        time.sleep(1)
    cmd('cam info')

    print('Startup tasks done')

if __name__ == "__main__":
    run()
