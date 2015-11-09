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
import X_params as params
import X_misc as misc


########################################################################
# Daemon control functions
def set_focuser(position):
    foc=Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    try:
        f=foc.set_focuser(position)
        if f: print f
    except:
        print 'No response from focuser daemon'

########################################################################
# Define interactive mode
def interactive():
    while 1:
        command=split(raw_input('lilith> '))
        if(len(command)>0):
            if command[0]=='q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0]=='start':
        print 'starting', command[1:]
        if len(command) == 1:
            print 'Need to specify daemons (cam, foc, filt, queue) or all'
            return
        if 'cam' in command[1:] or 'all' in command[1:]:
            print '~~Starting camera daemon:'
            misc.startDaemon(CAM_DAEMON_PROCESS,CAM_DAEMON_HOST,stdout=CAM_DAEMON_OUTPUT)
        if 'foc' in command[1:] or 'all' in command[1:]:
            print '~~Starting focuser daemon:'
            misc.startDaemon(FOC_DAEMON_PROCESS,FOC_DAEMON_HOST,stdout=FOC_DAEMON_OUTPUT)
        if 'filt' in command[1:] or 'all' in command[1:]:
            print '~~Starting filter wheel daemon:'
            misc.startDaemon(FILT_DAEMON_PROCESS,FILT_DAEMON_HOST,stdout=FILT_DAEMON_OUTPUT)
        if 'queue' in command[1:] or 'all' in command[1:]:
            print '~~Starting queue daemon:'
            misc.startDaemon(QUEUE_DAEMON_PROCESS,QUEUE_DAEMON_HOST,stdout=QUEUE_DAEMON_OUTPUT)
    
    elif command[0]=='ping':
        if len(command) == 1:
            print 'Need to specify daemons (cam, foc, filt, queue) or all'
            return
        if 'cam' in command[1:] or 'all' in command[1:]:
            print '~~Pinging camera daemon:'
            misc.pingDaemon(CAM_DAEMON_ADDRESS)
        if 'foc' in command[1:] or 'all' in command[1:]:
            print '~~Pinging focuser daemon:'
            misc.pingDaemon(FOC_DAEMON_ADDRESS)
        if 'filt' in command[1:] or 'all' in command[1:]:
            print '~~Pinging filter wheel daemon:'
            misc.pingDaemon(FILT_DAEMON_ADDRESS)
        if 'queue' in command[1:] or 'all' in command[1:]:
            print '~~Pinging queue daemon:'
            misc.pingDaemon(QUEUE_DAEMON_ADDRESS)
    
    elif command[0]=='shutdown':
        if len(command) == 1:
            print 'Need to specify daemons (cam, foc, filt, queue) or all'
            return
        if 'cam' in command[1:] or 'all' in command[1:]:
            print '~~Shuting down camera daemon:'
            misc.shutdownDaemon(CAM_DAEMON_ADDRESS)
        if 'foc' in command[1:] or 'all' in command[1:]:
            print '~~Shuting down focuser daemon:'
            misc.shutdownDaemon(FOC_DAEMON_ADDRESS)
        if 'filt' in command[1:] or 'all' in command[1:]:
            print '~~Shuting down filter wheel daemon:'
            misc.shutdownDaemon(FILT_DAEMON_ADDRESS)
        if 'queue' in command[1:] or 'all' in command[1:]:
            print '~~Shuting down queue daemon:'
            misc.shutdownDaemon(QUEUE_DAEMON_ADDRESS)
            
    elif command[0]=='kill':
        if len(command) == 1:
            print 'Need to specify daemons (cam, foc, filt, queue) or all'
            return
        if 'cam' in command[1:] or 'all' in command[1:]:
            print '~~Killing camera daemon:'
            misc.killDaemon(CAM_DAEMON_PROCESS,CAM_DAEMON_HOST)
        if 'foc' in command[1:] or 'all' in command[1:]:
            print '~~Killing focuser daemon:'
            misc.killDaemon(FOC_DAEMON_PROCESS,FOC_DAEMON_HOST)
        if 'filt' in command[1:] or 'all' in command[1:]:
            print '~~Killing filter wheel daemon:'
            misc.killDaemon(FILT_DAEMON_PROCESS,FILT_DAEMON_HOST)
        if 'queue' in command[1:] or 'all' in command[1:]:
            print '~~Killing queue daemon:'
            misc.killDaemon(QUEUE_DAEMON_PROCESS,QUEUE_DAEMON_HOST)

    elif command[0]=='help':
        printInstructions()
    elif command[0]=='i':
        print 'Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'lilith> Command not recognized:',command[0]

def printInstructions():
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

if len(sys.argv)==1:
    printInstructions()
else:
    # Camera daemon
    CAM_DAEMON_PROCESS=params.DAEMONS['cam']['PROCESS']
    CAM_DAEMON_HOST=params.DAEMONS['cam']['HOST']
    CAM_DAEMON_ADDRESS=params.DAEMONS['cam']['ADDRESS']
    CAM_DAEMON_OUTPUT=params.LOG_PATH+'cam_daemon-stdout.log'
    # Focuser
    FOC_DAEMON_PROCESS=params.DAEMONS['foc']['PROCESS']
    FOC_DAEMON_HOST=params.DAEMONS['foc']['HOST']
    FOC_DAEMON_ADDRESS=params.DAEMONS['foc']['ADDRESS']
    FOC_DAEMON_OUTPUT=params.LOG_PATH+'foc_daemon-stdout.log'
    # Filter wheel daemon
    FILT_DAEMON_PROCESS=params.DAEMONS['filt']['PROCESS']
    FILT_DAEMON_HOST=params.DAEMONS['filt']['HOST']
    FILT_DAEMON_ADDRESS=params.DAEMONS['filt']['ADDRESS']
    FILT_DAEMON_OUTPUT=params.LOG_PATH+'filt_daemon-stdout.log'
    # Queue daemon
    QUEUE_DAEMON_PROCESS=params.DAEMONS['queue']['PROCESS']
    QUEUE_DAEMON_HOST=params.DAEMONS['queue']['HOST']
    QUEUE_DAEMON_ADDRESS=params.DAEMONS['queue']['ADDRESS']
    QUEUE_DAEMON_OUTPUT=params.LOG_PATH+'queue_daemon-stdout.log'
    
    command=sys.argv[1:]    

    if command[0]=='i':
        interactive()
    else:
        query(command)
