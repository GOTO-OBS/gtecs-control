"""
Script to run the tasks for Start Night Phase 1.

This script should perform the following simple tasks:
* power on the equipment
* empty the persistent camera queues
* unpause the camera daemon
* home the filter wheel
* start the pipeline data reduction
"""
from __future__ import absolute_import
from __future__ import print_function
import time
from gtecs.tecs_modules.misc import execute_command as cmd


def run():
    print('Start of Night Phase 1')

    cmd('power on filt')
    cmd('power on foc')
    cmd('power on cam')
    cmd('power on mnt')
    time.sleep(5)

    # clean up persistent queue from previous night
    cmd('exq clear')
    time.sleep(1)
    cmd('exq resume')

    # home the wheels
    cmd('filt home')


if __name__ == "__main__":
    run()
