#!/usr/bin/env python

########################################################################
#                              follower.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#     G-TeCS script to provide regularly updated status infomation     #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
import os, sys, commands
import time, datetime
import subprocess
# TeCS modules
from tecs_modules import misc
from tecs_modules import params

if __name__ == '__main__':
    if len(sys.argv) > 1:
        daemons = sys.argv[1:]
    else:
        daemons = ['queue','cam','filt','foc','mnt','power']
    while True:
        queue = misc.python_command('queueX.py', 'info')
        cam = misc.python_command('cam.py', 'info')
        filt = misc.python_command('filt.py', 'info')
        foc = misc.python_command('foc.py', 'info')
        mnt = misc.python_command('mnt.py', 'info')
        power = misc.python_command('power.py', 'info')
        now = datetime.datetime.utcnow()
        print now.strftime('%Y-%m-%d %H:%M:%S') + '\n'
        if 'queue' in daemons:
            print queue
        if 'cam' in daemons:
            print cam
        if 'filt' in daemons:
            print filt
        if 'foc' in daemons:
            print foc
        if 'mnt' in daemons:
            print mnt
        if 'power' in daemons:
            print power
        time.sleep(0.5)
