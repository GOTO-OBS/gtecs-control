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

def run():
    print('Start of Night Phase 1')

    cmd('power off filt')
    cmd('power off foc')
    cmd('power off cam')

    cmd('mnt park')
    time.sleep(10)  # TODO: drastically increase this on deployment

    cmd('power off mnt')

if __name__ == "__main__":
    run()
