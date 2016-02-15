#!/usr/bin/env python

########################################################################
#                                cam.py                                #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over cam_daemon           #
#                    Martin Dyer, Sheffield, 2015-16                   #
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
from tecs_modules import misc
from tecs_modules import params

########################################################################
# Camera control functions
def get_info():
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = cam.get_info()
        print '####### CAMERA INFO #######'
        for tel in params.TEL_DICT.keys():
            print 'CAMERA ' + str(tel) + ' (%s-%i)'%tuple(params.TEL_DICT[tel])
            if info['status'+str(tel)] != 'Exposing':
                print 'Status: %s' %info['status'+str(tel)]
            else:
                print 'Status: %s %s (%.2f)' %(info['status'+str(tel)],info['run_ID'],info['remaining'+str(tel)])
            print 'Frame type:       %s' %info['frametype'+str(tel)]
            print 'Exposure time:    %.2fs' %info['exptime'+str(tel)]
            print 'Active area:      %s' %str(info['area'+str(tel)])
            print 'Bin factors:      %s' %str(info['bins'+str(tel)])
            print 'CCD Temperature:   %i' %info['ccd_temp'+str(tel)]
            print 'Base Temperature:  %i' %info['base_temp'+str(tel)]
            print 'Cooler power:      %i' %info['cooler_power'+str(tel)]
            print 'Serial number:     %s' %info['serial_number'+str(tel)]
            print '~~~~~~~'
        print 'Uptime: %.1fs' %info['uptime']
        print 'Ping: %.5fs' %info['ping']
        print '###########################'
    except:
        print misc.ERROR('No response from camera daemon')

def get_info_summary():
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = cam.get_info()
        for tel in params.TEL_DICT.keys():
            print 'CAMERA ' + str(tel) + ' (%s-%i)'%tuple(params.TEL_DICT[tel]),
            if info['status'+str(tel)] != 'Exposing':
                print '  Temp: %iC' %info['ccd_temp'+str(tel)],
                print '  [%s]' %info['status'+str(tel)]
            else:
                print '  %s %s (%.2f)' %(info['status'+str(tel)],info['run_ID'],info['remaining'+str(tel)])
    except:
        print misc.ERROR('No response from camera daemon')

def take_image(exptime,HW_list):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = cam.take_image(exptime,HW_list)
        if c: print c
    except:
        print misc.ERROR('No response from camera daemon')

def take_dark(exptime,HW_list):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = cam.take_dark(exptime,HW_list)
        if c: print c
    except:
	print misc.ERROR('No response from camera daemon')

def take_bias(HW_list):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = cam.take_bias(HW_list)
        if c: print c
    except:
	print misc.ERROR('No response from camera daemon')

def abort_exposure(HW_list):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = cam.abort_exposure(HW_list)
        if c: print c
    except:
        print misc.ERROR('No response from camera daemon')

def set_temperature(target_temp, HW_list):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = cam.set_temperature(target_temp, HW_list)
        if c: print c
    except:
        print misc.ERROR('No response from camera daemon')

def set_flushes(target_flushes, HW_list):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = cam.set_flushes(target_flushes, HW_list)
        if c: print c
    except:
        print misc.ERROR('No response from camera daemon')

def set_bins(bins, HW_list):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = cam.set_bins(bins, HW_list)
        if c: print c
    except:
        print misc.ERROR('No response from camera daemon')

def set_area(area, HW_list):
    cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
    cam._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = cam.set_area(area, HW_list)
        if c: print c
    except:
        print misc.ERROR('No response from camera daemon')

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('cam> '))
        if len(command) > 0:
            if command[0] == 'q' or command[0] == 'exit':
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
    elif command[0] == 'help' or command[0] == '?':
        print_instructions()
    elif command[0] == 'i':
        print misc.ERROR('Already in interactive mode')
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    elif command[0] == 'info':
        if len(command) > 1 and command[1] in ['v','V','-v','-V']:
            get_info()
        else:
            get_info_summary()
        
    elif command[0] == 'image':
        if len(command) == 2:
            # e.g. "image 2" - 2s on all cameras
            take_image(float(command[1]),params.TEL_DICT.keys())
        elif len(command) == 3 and command[1] in str(params.TEL_DICT.keys()):
            # e.g. "image 2 2" - 2s on camera 2
            take_image(float(command[2]),[int(command[1])])
        else:
            print misc.ERROR('Invalid arguments')

    elif command[0] == 'dark':
        if len(command) == 2:
            # e.g. "dark 2" - 2s dark on all cameras
            take_dark(float(command[1]),params.TEL_DICT.keys())
        elif len(command) == 3 and command[1] in str(params.TEL_DICT.keys()):
            # e.g. "dark 2 2" - 2s dark on camera 2
            take_dark(float(command[2]),[int(command[1])])
        else:
            print misc.ERROR('Invalid arguments')

    elif command[0] == 'bias':
        if len(command) == 1:
            # e.g. "bias" - bias on all cameras
            take_bias(params.TEL_DICT.keys())
        elif len(command) == 2 and command[1] in str(params.TEL_DICT.keys()):
            # e.g. "bias 2" - bias on camera 2
            take_bias([int(command[1])])
        else:
            print misc.ERROR('Invalid arguments')
    
    elif command[0] == 'abort':
        if len(command) > 1 and command[1] in str(params.TEL_DICT.keys()):
            abort_exposure([int(command[1])])
        else:
            abort_exposure(params.TEL_DICT.keys())
    
    elif command[0] == 'temp':
        if len(command) > 2 and command[1] in str(params.TEL_DICT.keys()):
            set_temperature(float(command[2]),[int(command[1])])
        else:
            set_temperature(float(command[1]),params.TEL_DICT.keys())
    
    elif command[0] == 'flush':
        if len(command) > 2 and command[1] in str(params.TEL_DICT.keys()):
            set_flushes(int(command[2]),[int(command[1])])
        else:
            set_flushes(int(command[1]),params.TEL_DICT.keys())
    
    elif command[0] == 'bin':
        if len(command) > 3 and command[1] in str(params.TEL_DICT.keys()):
            set_bins([int(command[2]),int(command[3])],[int(command[1])])
        else:
            set_bins([int(command[1]),int(command[2])],params.TEL_DICT.keys())
    
    elif command[0] == 'area':
        if len(command) > 5 and command[1] in str(params.TEL_DICT.keys()):
            set_area([int(command[2]), int(command[3]), int(command[4]), int(command[5])],[int(command[1])])
        else:
            set_area([int(command[1]), int(command[2]), int(command[3]), int(command[4])],params.TEL_DICT.keys())
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print misc.ERROR('Unrecognized command "%s"' %command[0])

def print_instructions():
    help_str = misc.bold('Usage:') + ' cam [command]' + '\n' +\
    ' ' + misc.undl('Daemon commands') + ':' + '\n' +\
    '  cam ' + misc.bold('start') + '                - start the daemon' + '\n' +\
    '  cam ' + misc.bold('shutdown') + '             - shutdown the daemon' + '\n' +\
    '  cam ' + misc.bold('kill') + '                 - kill the daemon (' + misc.rtxt('emergency use') + ')' + '\n' +\
    '  cam ' + misc.bold('ping') + '                 - ping the daemon' + '\n' +\
    ' ' + misc.undl('Camera commands') + ':' + '\n' +\
    '  cam ' + misc.bold('image') + ' [tels] exptime' + ' - take a normal exposure' + '\n' +\
    '  cam ' + misc.bold('dark') + ' [tels] exptime' + '  - take a dark frame' + '\n' +\
    '  cam ' + misc.bold('bias') + ' [tels]' + '          - take a bias frame' + '\n' +\
    '  cam ' + misc.bold('abort') + ' [tels]' + '         - abort current exposure' + '\n' +\
    '  cam ' + misc.bold('bin') + ' [tels] h v' + '       - set horiz/vert binning factors' + '\n' +\
    '  cam ' + misc.bold('temp') + ' [tels] temp' + '     - set camera temperature' + '\n' +\
    '  cam ' + misc.bold('flush') + ' [tels] number' + '  - set no. of flushes before exposing' + '\n' +\
    '  cam ' + misc.bold('area') + ' [tels] x y X Y' + '  - sets the active area of the CCD' + '\n' +\
    '  cam ' + misc.bold('info') + ' [v]' + '        - report current status' + '\n' +\
    ' ' + misc.undl('Control commands') + ':' + '\n' +\
    '  cam ' + misc.bold('i') + '               - enter interactive mode' + '\n' +\
    '  cam ' + misc.bold('q') + '/' + misc.bold('exit') + '          - quit interactive mode' + '\n' +\
    '  cam ' + misc.bold('?') + '/' + misc.bold('help') + '          - print these instructions'
    print help_str

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
