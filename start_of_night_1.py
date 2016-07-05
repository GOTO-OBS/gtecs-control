"""
Script to run the tasks for Start Night Phase 1.

This script should perform the following simple tasks:
* power on the equipment
* start the FLI interfaces and SITECH interface
* start the daemons
* empty the persistent camera queues
* unpause the camera daemon
* start the pipeline data reduction
"""
from __future__ import absolute_import
from __future__ import print_function
import time
from tecs_modules.misc import python_command

print('Start of Night Phase 1')
python_command('power.py', 'on filt')
python_command('power.py', 'on foc')
python_command('power.py', 'on cam')
python_command('power.py', 'on mnt')
time.sleep(5)

# start the interfaces
python_command('fli_interface.py', '')
python_command('fli_interfaceB.py', '')
# python_command('mnt.py', 'startS')
time.sleep(5)

# start the daemons
python_command('lilith.py', 'start')
time.sleep(15)

# clean up persistent queue from previous night
python_command('exq.py', 'clear')
time.sleep(1)
python_command('exq.py', 'resume')

# start the pipeline DR (TODO)
# python_command('qsireduce start')
