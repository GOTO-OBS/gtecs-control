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
from gtecs.tecs_modules import params


def run():
    print('Start of Night Phase 1')

    cmd('power start')
    time.sleep(10)

    for tel in params.TEL_DICT:
        cmd('power on filt{}'.format(tel))
        cmd('power on foc{}'.format(tel))
        cmd('power on cam{}'.format(tel))
    cmd('power on sitech')
    time.sleep(5)

    # clean up persistent queue from previous night
    cmd('exq clear')
    time.sleep(1)
    cmd('exq resume')

    # home the wheels
    cmd('filt home')


if __name__ == "__main__":
    run()
