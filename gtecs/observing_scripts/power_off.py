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
        cmd('power on filt{}'.format(tel))
        cmd('power on foc{}'.format(tel))
        cmd('power on cam{}'.format(tel))

    cmd('mnt park')


if __name__ == "__main__":
    run()
