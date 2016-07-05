"""
Script to run the tasks for Start Night Phase 1.

This script should perform the following simple tasks:
* power on the equipment
* start the daemons
* empty the persistent camera queues
* unpause the camera daemon
* start the pipeline data reduction
"""
from __future__ import absolute_import
from __future__ import print_function
import time
from tecs_modules.misc import execute_command
import sys


def make_cmd_string(cmd, args):
    return ' '.join((sys.executable, cmd + '.py', args))


def cmd(cmd, args):
    execute_command(make_cmd_string(cmd, args))

print('Start of Night Phase 1')

cmd('lilith', 'start power')
cmd('power', 'on filt')
cmd('power', 'on foc')
cmd('power', 'on cam')
cmd('power', 'on mnt')
time.sleep(5)

# start the daemons
cmd('lilith', 'start')
time.sleep(15)

# clean up persistent queue from previous night
cmd('exq', 'clear')
time.sleep(1)
cmd('exq', 'resume')

# start the pipeline DR (TODO)
# cmd('qsireduce start')
