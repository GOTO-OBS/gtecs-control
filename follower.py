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
import X_params as params

def queue_info():
    proc = subprocess.Popen('python2 queueX.py info', shell=True, stdout=subprocess.PIPE)
    queue_info = proc.communicate()[0]
    return queue_info
    
def cam_info():
    proc = subprocess.Popen('python2 cam.py info', shell=True, stdout=subprocess.PIPE)
    cam_info = proc.communicate()[0]
    return cam_info

def filt_info():
    proc = subprocess.Popen('python2 filt.py info', shell=True, stdout=subprocess.PIPE)
    filt_info = proc.communicate()[0]
    return filt_info

def foc_info():
    proc = subprocess.Popen('python2 foc.py info', shell=True, stdout=subprocess.PIPE)
    foc_info = proc.communicate()[0]
    return foc_info

def mnt_info():
    proc = subprocess.Popen('python2 mnt.py info', shell=True, stdout=subprocess.PIPE)
    mnt_info = proc.communicate()[0]
    return mnt_info
    
if __name__ == '__main__':
    
    if len(sys.argv) > 1:
        daemons = sys.argv[1:]
    else:
        daemons = ['queue','cam','filt','foc','mnt']
    while True:
        queue = queue_info()
        cam = cam_info()
        filt = filt_info()
        foc = foc_info()
        mnt = mnt_info()
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
        time.sleep(0.5)
