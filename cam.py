#!/usr/bin/env python

########################################################################
#                                cam.py                                #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over cam_daemon           #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
import os, sys, commands
from string import split
import readline
import time
import Pyro4
# TeCS modules
import X_params as params
import X_misc as misc

########################################################################
# Camera control functions
def get_info():
    cam=Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        info = cam.get_info()
        
        print '####### CAMERA INFO #######'
        if info['status'] != 'Exposing':
            print 'Status: %s' %info['status']
        else:
            print 'Status: %s (%i ms)' %(info['status'],info['timeleft'])
        print '~~~~~~~'
        print 'Array area:         %s' %str(info['array_area'])
        print 'Active area:        %s' %str(info['active_area'])
        print 'Pixel size:         %s' %str(info['pixel_size'])
        print 'Frame type:         %s' %info['frametype']
        print 'Exposure time:      %i' %info['exptime']
        print 'Bin factors:        %s' %str(info['bins'])
        print '~~~~~~~'
        print 'Temperature:        %.1f' %info['ccd_temperature']
        print 'Cooler power:       %.2f' %info['cooler_power']
        print '~~~~~~~'
        print 'Serial number:      %s' %info['serial_number']
        print 'Firmware:           %s' %info['firmware_revision']
        print 'Hardware:           %s' %info['hardware_revision']
        print '###########################'
    except:
        print 'No response from camera daemon'
    
def take_image(exptime,frametype='normal'):
    cam=Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        cam.take_image(exptime,frametype.strip())
    except:
        print 'No response from camera daemon'
    
def abort_exposure():
    cam=Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        cam.abort_exposure()
    except:
        print 'No response from camera daemon'
    
def set_temp(target_temp):
    cam=Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        cam.set_temp(target_temp)
    except:
        print 'No response from camera daemon'
    
def set_flushes(target_flushes):
    cam=Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        cam.set_flushes(target_flushes)
    except:
        print 'No response from camera daemon'
    
def set_binning(hbin,vbin):
    cam=Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        cam.set_binning(hbin,vbin)
    except:
        print 'No response from camera daemon'


########################################################################
# Define interactive mode
def interactive():
    while 1:
        command=split(raw_input('cam> '))
        if(len(command)>0):
            if command[0]=='q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0]=='start':
        misc.startDaemon(CAM_DAEMON_PROCESS,CAM_DAEMON_HOST,stdout=CAM_DAEMON_OUTPUT)
    elif command[0]=='ping':
        misc.pingDaemon(CAM_DAEMON_ADDRESS)
    elif command[0]=='shutdown':
        misc.shutdownDaemon(CAM_DAEMON_ADDRESS)
    elif command[0]=='kill':
        misc.killDaemon(CAM_DAEMON_PROCESS,CAM_DAEMON_HOST)
    elif command[0]=='help':
        printInstructions()
    elif command[0]=='i':
        print 'Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    elif command[0]=='info':
        get_info()
    elif command[0]=='image':
        if len(command) == 2:
            take_image(int(command[1]))
        else:
            if command[2] in ['normal', 'dark', 'rbi_flush']:
                take_image(int(command[1]),command[2])
            else:
                print 'ERROR: [type] must be normal, dark or rbi_flush'
    elif command[0]=='abort':
        abort_exposure()
    elif command[0]=='temp':
        if (-55 <= float(command[1]) <= 45):
            set_temp(float(command[1]))
        else:
            print 'ERROR: [temp] must be between -55 and 45 Celcius'
    elif command[0]=='flush':
        if (0 <= int(command[1]) <= 16):
            set_flushes(int(command[1]))
        else:
            print 'ERROR: [number] must be between 0 and 16'
    elif command[0]=='bin':
        if len(command) == 3:
            set_binning(int(command[1]),int(command[2]))
        else:
            set_binning(int(command[1]),int(command[1]))

    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    elif command[0]=='exit':
        print 'cam> Use "shutdown" to close the daemon or "q" to quit interactive mode'
    else:
        print 'cam> Command not recognized:',command[0]

def printInstructions():
    print 'Usage: cam start                   - starts the camera daemon'
    print '       cam shutdown                - shuts down the camera daemon cleanly'
    print '       cam kill                    - kills the camera daemon (emergency use only!)'
    print '       cam ping                    - pings the camera daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       cam info                    - reports current camera data'
    print '       cam image [exptime] [type]  - takes an image'
    print '       cam abort                   - aborts current exposure'
    print '       cam bin [b] OR [hb] [vb]    - sets binning factor(s)'
    print '       cam temp [temp]             - sets camera temperature'
    print '       cam flush [number]          - sets number of CCD flushes before exposing'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       cam i                       - enter interactive (command line) usage'
    print '       cam q                       - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       cam help                    - prints these instructions'

########################################################################
# Control System

if len(sys.argv)==1:
    printInstructions()
else:
    CAM_DAEMON_PROCESS=params.DAEMONS['cam']['PROCESS']
    CAM_DAEMON_HOST=params.DAEMONS['cam']['HOST']
    CAM_DAEMON_ADDRESS=params.DAEMONS['cam']['ADDRESS']
    CAM_DAEMON_OUTPUT=params.LOG_PATH+'cam_daemon-stdout.log'
    
    command=sys.argv[1:]    

    if command[0]=='i':
        interactive()
    else:
        query(command)
