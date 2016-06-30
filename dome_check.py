#!/usr/bin/env python

########################################################################
#                            dome_check.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#               G-TeCS script to check the dome status                 #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import os, sys
import time
import Pyro4
# TeCS modules
from tecs_modules import misc
from tecs_modules import params

INFO_TIMEOUT = 10.
PING_LIMIT = 60.

DOME_DAEMON_ADDRESS = params.DAEMONS['dome']['ADDRESS']

start_time = time.time()
while True:
    dome = Pyro4.Proxy(DOME_DAEMON_ADDRESS)
    dome._pyroTimeout = params.PROXY_TIMEOUT
    info = dome.get_info()
    if type(info) == dict:
        break
    if time.time() - start_time > INFO_TIMEOUT:
        print('Failed to get info dict')
        misc.send_email(message='Failed to get dome info dictionary')
        sys.exit()
    time.sleep(1)

print(info)
if info['ping'] > PING_LIMIT:
    print('Failed to ping dome daemon')
    misc.send_email(message='Dome ping failed - daemon crashed?\nKilling and restarting daemon...')
    print('dome kill')
    cmd = ' '.join((sys.executable, params.SCRIPT_PATH, 'kill'))
    os.system(cmd)
    print('Sleeping...')
    time.sleep(10)
    print('dome start')
    cmd = ' '.join((sys.executable, params.SCRIPT_PATH, 'start'))
    os.system(cmd)
    exit()

print('Dome OK')
