"""
normal_shutdown
Script to run end of night tasks
This script should perform the following simple tasks:
    * empty the camera queues
    * abort any current exposures
    * shutdown the interfaces
    * power off the hardware
    * park the scope
    * close the dome
"""

import time

from gtecs import params
from gtecs.misc import execute_command as cmd


def run():
    """
    Run shutdown tasks.
    """
    print('Running shutdown tasks')

    # Pause and clear the exposure queue
    cmd('exq pause')
    time.sleep(1)
    cmd('exq clear')

    # Abort any current exposures
    cmd('cam abort')

    # Shut down the FLI interface, else it would crash when we power off
    cmd('fli shutdown')

    # Power off the FLI hardware
    for tel in params.TEL_DICT:
        cmd('power off filt{}'.format(tel))
        cmd('power off foc{}'.format(tel))
        cmd('power off cam{}'.format(tel))

    # Park the mount
    cmd('mnt park')

    # give time before closing dome
    time.sleep(60)

    # close dome and wait (pilot will try again before shutdown)
    cmd('dome close')
    time.sleep(65)

if __name__ == "__main__":
    run()
