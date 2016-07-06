"""
Script to run the tasks for Start Night Phase 1.

This script should perform the following simple tasks:
* power on the equipment
* start the daemons
* empty the persistent camera queues
* unpause the camera daemon
<<<<<<< 81b68339c14fe7cf121b757e77b318bea23fb2cf
* home the filter wheel
=======
>>>>>>> adjusting to change in module structure
* start the pipeline data reduction
"""
from __future__ import absolute_import
from __future__ import print_function
import time
from gtecs.tecs_modules.misc import execute_command as cmd
<<<<<<< 81b68339c14fe7cf121b757e77b318bea23fb2cf

=======
import sys
>>>>>>> adjusting to change in module structure

def run():
    print('Start of Night Phase 1')

    cmd('lilith start power')
    cmd('power on filt')
    cmd('power on foc')
    cmd('power on cam')
    cmd('power on mnt')
    time.sleep(5)

    # start the daemons
    cmd('lilith start')
    time.sleep(15)

    # clean up persistent queue from previous night
    cmd('exq clear')
    time.sleep(1)
    cmd('exq resume')

<<<<<<< 81b68339c14fe7cf121b757e77b318bea23fb2cf
    # home the wheels
    cmd('filt home')

=======
>>>>>>> adjusting to change in module structure
    # start the pipeline DR (TODO)
    # cmd('qsireduce start')

if __name__ == "__main__":
<<<<<<< 81b68339c14fe7cf121b757e77b318bea23fb2cf
    run()
=======
    run()
>>>>>>> adjusting to change in module structure
