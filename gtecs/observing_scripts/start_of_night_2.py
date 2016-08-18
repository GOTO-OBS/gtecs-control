"""
Script to run the tasks for Start Night Phase 1.

This script should perform the following simple tasks:
* start scope tracking and point to LST+4
"""
from __future__ import absolute_import
from __future__ import print_function
import time
from gtecs.tecs_modules.misc import execute_command as cmd


def run():
    print('Start of Night Phase 2')
    cmd('mnt info')
    cmd('mnt unpark')
    time.sleep(5)
    cmd('mnt info')

if __name__ == "__main__":
    run()