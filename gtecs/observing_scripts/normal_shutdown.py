"""
Script to run the tasks for end of night.

This script should perform the following simple tasks:
* park the scope
* power off the equipment
"""
from __future__ import absolute_import
from __future__ import print_function
import time
from gtecs.tecs_modules.misc import execute_command as cmd
from gtecs.tecs_modules import params


def run():
    print('End of night power down')

    for tel in params.TEL_DICT:
        cmd('power off filt{}'.format(tel))
        cmd('power off foc{}'.format(tel))
        cmd('power off cam{}'.format(tel))

    if params.FREEZE_DEC:
        cmd('mnt stop')
    else:
        cmd('mnt park')

    # give time before closing dome
    time.sleep(60)

    cmd('mnt blinky on')

    # close dome and wait (pilot will try again before shutdown)
    cmd('dome close')
    time.sleep(65)

if __name__ == "__main__":
    run()
