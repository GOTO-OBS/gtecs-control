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
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        info = cam.get_info()
        print '####### CAMERA INFO #######'
        if info['status'] != 'Exposing':
            print 'Status: %s' %info['status']
        else:
            print 'Status: %s (%is)' %(info['status'],info['timeleft'])
        print '~~~~~~~'
        print 'Array area:       %s' %str(info['array_area'])
        print 'Active area:      %s' %str(info['active_area'])
        print 'Pixel size:       %s' %str(info['pixel_size'])
        print 'Frame type:       %s' %info['frametype']
        print 'Exposure time:    %is' %info['exptime']
        print 'Bin factors:      %s' %str(info['bins'])
        print '~~~~~~~'
        print 'Temperature:      %.1fC' %info['ccd_temperature']
        print 'Cooler power:     %.2f' %info['cooler_power']
        #print '~~~~~~~'
        #print 'Serial number:    %s' %info['serial_number']
        #print 'Firmware:         %s' %info['firmware_revision']
        #print 'Hardware:         %s' %info['hardware_revision']
        print '~~~~~~~'
        print 'Uptime: %.1fs' %info['uptime']
        print 'Ping: %.5fs' %info['ping']
        print '###########################'
    except:
        print 'ERROR: No response from camera daemon'
    
def take_image(exptime,frametype='normal'):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        c = cam.take_image(exptime,frametype)
        if c: print c
    except:
        print 'ERROR: No response from camera daemon'
    
def abort_exposure():
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        c = cam.abort_exposure()
        if c: print c
    except:
        print 'ERROR: No response from camera daemon'
    
def set_temp(target_temp):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        c = cam.set_temp(target_temp)
        if c: print c
    except:
        print 'ERROR: No response from camera daemon'
    
def set_flushes(target_flushes):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        c = cam.set_flushes(target_flushes)
        if c: print c
    except:
        print 'ERROR: No response from camera daemon'
    
def set_binning(hbin, vbin=None):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        c = cam.set_binning(hbin,vbin)
        if c: print c
    except:
        print 'ERROR: No response from camera daemon'
    
def set_area(ul_x, ul_y, lr_x, lr_y):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    try:
        c = cam.set_area(ul_x, ul_y, lr_x, lr_y)
        if c: print c
    except:
        print 'ERROR: No response from camera daemon'

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('cam> '))
        if len(command) > 0:
            if command[0] == 'q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        misc.start_daemon(CAM_DAEMON_PROCESS, CAM_DAEMON_HOST, stdout=CAM_DAEMON_OUTPUT)
    elif command[0] == 'shutdown':
        misc.shutdown_daemon(CAM_DAEMON_ADDRESS)
    elif command[0] == 'kill':
        misc.kill_daemon(CAM_DAEMON_PROCESS,CAM_DAEMON_HOST)
    elif command[0] == 'ping':
        misc.ping_daemon(CAM_DAEMON_ADDRESS)
    elif command[0] == 'help':
        print_instructions()
    elif command[0] == 'i':
        print 'ERROR: Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    elif command[0] == 'info':
        get_info()
    elif command[0]=='image':
        if len(command) == 2:
            take_image(int(command[1]))
        else:
            take_image(int(command[1]), command[2])
    elif command[0]=='abort':
        abort_exposure()
    elif command[0]=='temp':
        set_temp(float(command[1]))
    elif command[0]=='flush':
        set_flushes(int(command[1]))
    elif command[0]=='bin':
        if len(command) == 2:
            set_binning(int(command[1]))
        else:
            set_binning(int(command[1]), int(command[2]))
    elif command[0]=='area':
        set_area(int(command[1]), int(command[2]), int(command[3]), int(command[4]))
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'cam> Command not recognized:',command[0]

def print_instructions():
    print 'Usage: cam start                       - starts the camera daemon'
    print '       cam shutdown                    - shuts down the camera daemon cleanly'
    print '       cam kill                        - kills the camera daemon (emergency use only!)'
    print '       cam ping                        - pings the camera daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       cam info                        - reports current camera data'
    print '       cam image [exptime] [type]      - takes an image'
    print '       cam abort                       - aborts current exposure'
    print '       cam bin [b] OR [hb] [vb]        - sets binning factor(s)'
    print '       cam temp [temp]                 - sets camera temperature'
    print '       cam flush [number]              - sets number of CCD flushes before exposing'
    print '       cam area [ul_x ul_y lr_x lr_y]  - sets the active area of the CCD'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       cam i                           - enter interactive (command line) usage'
    print '       cam q                           - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       cam help                        - prints these instructions'

########################################################################
# Control System

if len(sys.argv) == 1:
    print_instructions()
else:
    CAM_DAEMON_PROCESS = params.DAEMONS['cam']['PROCESS']
    CAM_DAEMON_HOST = params.DAEMONS['cam']['HOST']
    CAM_DAEMON_ADDRESS = params.DAEMONS['cam']['ADDRESS']
    CAM_DAEMON_OUTPUT = params.LOG_PATH + 'cam_daemon-stdout.log'
    
    command = sys.argv[1:]    
    if command[0] == 'i':
        interactive()
    else:
        query(command)
