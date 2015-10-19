#!/usr/bin/env python

########################################################################
#                                filt.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over filt_daemon          #
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
# Filter wheel control functions
def get_info():
    filt=Pyro4.Proxy(FILT_DAEMON_ADDRESS)
    try:
        filt.get_info()
        time.sleep(0.1) # Wait for it to update
        info = filt.report_to_UI('info')
        
        print '#### FILTER WHEEL INFO ####'
        print 'Status: %s' %info['status']
        print 'Current filter:     %s' %info['current_filter']
        print 'Current filter pos: %s' %info['current_filter_pos']
        print 'Current motor pos:  %s' %info['current_pos']
        print '###########################'
    except:
        print 'No response from filter wheel daemon'
    
def set_filter(new_filt):
    filt=Pyro4.Proxy(FILT_DAEMON_ADDRESS)
    try:
        filt.set_filter(new_filt)
    except:
        print 'No response from filter wheel daemon'

########################################################################
# Define interactive mode
def interactive():
    while 1:
        command=split(raw_input('filt> '))
        if(len(command)>0):
            if command[0]=='q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0]=='start':
        misc.startDaemon(FILT_DAEMON_PROCESS,FILT_DAEMON_HOST,stdout=FILT_DAEMON_OUTPUT)
    elif command[0]=='ping':
        misc.pingDaemon(FILT_DAEMON_ADDRESS)
    elif command[0]=='shutdown':
        misc.shutdownDaemon(FILT_DAEMON_ADDRESS)
    elif command[0]=='kill':
        misc.killDaemon(FILT_DAEMON_PROCESS,FILT_DAEMON_HOST)
    elif command[0]=='help':
        printInstructions()
    elif command[0]=='i':
        print 'Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount control functions
    elif command[0]=='info':
        get_info()
    elif command[0]=='set':
        set_filter(command[1])
    elif command[0]=='list':
        print params.FILTER_LIST

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    elif command[0]=='exit':
        print 'filt> Use "shutdown" to close the daemon or "q" to quit interactive mode'
    else:
        print 'filt> Command not recognized:',command[0]

def printInstructions():
    print 'Usage: filt start              - starts the mount daemon'
    print '       filt shutdown           - shuts down the mount daemon cleanly'
    print '       filt kill               - kills the mount daemon (emergency use only!)'
    print '       filt ping               - pings the mount daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       filt info               - reports current filter wheel data'
    print '       filt set [filter]       - sets the currently active filter'
    print '       filt list               - lists the possible filters'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       filt i                  - enter interactive (command line) usage'
    print '       filt q                  - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       filt help               - prints these instructions'

########################################################################
# Control System

if len(sys.argv)==1:
    printInstructions()
else:
    FILT_DAEMON_PROCESS=params.DAEMONS['filt']['PROCESS']
    FILT_DAEMON_HOST=params.DAEMONS['filt']['HOST']
    FILT_DAEMON_ADDRESS=params.DAEMONS['filt']['ADDRESS']
    FILT_DAEMON_OUTPUT=params.LOG_PATH+'filt_daemon-stdout.log'
    
    command=sys.argv[1:]    

    if command[0]=='i':
        interactive()
    else:
        query(command)
