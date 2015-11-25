#!/usr/bin/env python

########################################################################
#                               lilith.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#       G-TeCS script to provide overall control of the daemons        #
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
from tecs_modules import misc
from tecs_modules import params

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('lilith> '))
        if len(command) > 0:
            if command[0] == 'q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        if len(command) == 1:
            print 'Need to specify daemons (cam, foc, filt, queue) or all'
            return
        if 'cam' in command[1:] or 'all' in command[1:]:
            print 'Camera daemon:'
            print '   ',
            misc.start_daemon(CAM_DAEMON_PROCESS, CAM_DAEMON_HOST, stdout=CAM_DAEMON_OUTPUT)
        if 'foc' in command[1:] or 'all' in command[1:]:
            print 'Focuser daemon:'
            print '   ',
            misc.start_daemon(FOC_DAEMON_PROCESS, FOC_DAEMON_HOST, stdout=FOC_DAEMON_OUTPUT)
        if 'filt' in command[1:] or 'all' in command[1:]:
            print 'Filter wheel daemon:'
            print '   ',
            misc.start_daemon(FILT_DAEMON_PROCESS, FILT_DAEMON_HOST, stdout=FILT_DAEMON_OUTPUT)
        if 'queue' in command[1:] or 'all' in command[1:]:
            print 'Queue daemon:'
            print '   ',
            misc.start_daemon(QUEUE_DAEMON_PROCESS, QUEUE_DAEMON_HOST, stdout=QUEUE_DAEMON_OUTPUT)
    
    elif command[0] == 'shutdown':
        if len(command) == 1:
            print 'Need to specify daemons (cam, foc, filt, queue) or all'
            return
        if 'cam' in command[1:] or 'all' in command[1:]:
            print 'Camera daemon:'
            print '   ',
            misc.shutdown_daemon(CAM_DAEMON_ADDRESS)
        if 'foc' in command[1:] or 'all' in command[1:]:
            print 'Focuser daemon:'
            print '   ',
            misc.shutdown_daemon(FOC_DAEMON_ADDRESS)
        if 'filt' in command[1:] or 'all' in command[1:]:
            print 'Filter wheel daemon:'
            print '   ',
            misc.shutdown_daemon(FILT_DAEMON_ADDRESS)
        if 'queue' in command[1:] or 'all' in command[1:]:
            print 'Queue daemon:'
            print '   ',
            misc.shutdown_daemon(QUEUE_DAEMON_ADDRESS)
            
    elif command[0] == 'kill':
        if len(command) == 1:
            print 'Need to specify daemons (cam, foc, filt, queue) or all'
            return
        if 'cam' in command[1:] or 'all' in command[1:]:
            print 'Camera daemon:'
            print '   ',
            misc.kill_daemon(CAM_DAEMON_PROCESS,CAM_DAEMON_HOST)
        if 'foc' in command[1:] or 'all' in command[1:]:
            print 'Focuser daemon:'
            print '   ',
            misc.kill_daemon(FOC_DAEMON_PROCESS,FOC_DAEMON_HOST)
        if 'filt' in command[1:] or 'all' in command[1:]:
            print 'Filter wheel daemon:'
            print '   ',
            misc.kill_daemon(FILT_DAEMON_PROCESS,FILT_DAEMON_HOST)
        if 'queue' in command[1:] or 'all' in command[1:]:
            print 'Queue daemon:'
            print '   ',
            misc.kill_daemon(QUEUE_DAEMON_PROCESS,QUEUE_DAEMON_HOST)
    
    elif command[0] == 'ping':
        if len(command) == 1:
            print 'Need to specify daemons (cam, foc, filt, queue) or all'
            return
        if 'cam' in command[1:] or 'all' in command[1:]:
            print 'Camera daemon:'
            print '   ',
            misc.ping_daemon(CAM_DAEMON_ADDRESS)
        if 'foc' in command[1:] or 'all' in command[1:]:
            print 'Focuser daemon:'
            print '   ',
            misc.ping_daemon(FOC_DAEMON_ADDRESS)
        if 'filt' in command[1:] or 'all' in command[1:]:
            print 'Filter wheel daemon:'
            print '   ',
            misc.ping_daemon(FILT_DAEMON_ADDRESS)
        if 'queue' in command[1:] or 'all' in command[1:]:
            print 'Queue daemon:'
            print '   ',
            misc.ping_daemon(QUEUE_DAEMON_ADDRESS)
    
    elif command[0] == 'help':
        print_instructions()
    elif command[0] == 'i':
        print 'Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'lilith> Command not recognized:',command[0]

def print_instructions():
    print 'Usage: lilith start [daemons]    - starts the daemon(s)'
    print '       lilith shutdown [daemons] - shuts down the daemon(s) cleanly'
    print '       lilith kill [daemons]     - kills the daemon(s) (emergency use only!)'
    print '       lilith ping [daemons]     - pings the daemon(s)'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       lilith i                  - enter interactive (command line) usage'
    print '       lilith q                  - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       lilith help               - prints these instructions'

########################################################################
# Control System

if len(sys.argv) == 1:
    print_instructions()
else:
    # Camera daemon
    CAM_DAEMON_PROCESS = params.DAEMONS['cam']['PROCESS']
    CAM_DAEMON_HOST = params.DAEMONS['cam']['HOST']
    CAM_DAEMON_ADDRESS = params.DAEMONS['cam']['ADDRESS']
    CAM_DAEMON_OUTPUT = params.LOG_PATH + 'cam_daemon-stdout.log'
    # Focuser daemon
    FOC_DAEMON_PROCESS = params.DAEMONS['foc']['PROCESS']
    FOC_DAEMON_HOST = params.DAEMONS['foc']['HOST']
    FOC_DAEMON_ADDRESS = params.DAEMONS['foc']['ADDRESS']
    FOC_DAEMON_OUTPUT = params.LOG_PATH + 'foc_daemon-stdout.log'
    # Filter wheel daemon
    FILT_DAEMON_PROCESS = params.DAEMONS['filt']['PROCESS']
    FILT_DAEMON_HOST = params.DAEMONS['filt']['HOST']
    FILT_DAEMON_ADDRESS = params.DAEMONS['filt']['ADDRESS']
    FILT_DAEMON_OUTPUT = params.LOG_PATH + 'filt_daemon-stdout.log'
    # Queue daemon
    QUEUE_DAEMON_PROCESS = params.DAEMONS['queue']['PROCESS']
    QUEUE_DAEMON_HOST = params.DAEMONS['queue']['HOST']
    QUEUE_DAEMON_ADDRESS = params.DAEMONS['queue']['ADDRESS']
    QUEUE_DAEMON_OUTPUT = params.LOG_PATH + 'queue_daemon-stdout.log'
    
    command = sys.argv[1:]    
    if command[0] == 'i':
        interactive()
    else:
        query(command)
