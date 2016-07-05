"""
Script to run the tasks for Start Night Phase 1.

This script should perform the following simple tasks:
* start scope tracking and point to LST+4
"""
from __future__ import absolute_import
from __future__ import print_function
import time
from ..misc import python_command

print('Start of Night Phase 2')
execute_command('tel info')
execute_command('tel unpark')
time.sleep(5)
execute_command('tel track')
