#!/usr/bin/env python

########################################################################
#                                foc.py                                #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over foc_daemon           #
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
# Focuser control functions
def get_info():
    foc=Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    try:
        foc.get_info()
        time.sleep(0.1) # Wait for it to update
        info = foc.report_to_UI('info')
        
        print '#### FOCUSER INFO ####'
        print 'Status: %s' %info['status']
        print 'Current motor pos:  %s' %info['current_pos']
        print 'Internal temperature: %s' %info['int_temp']
        print 'External temperature:  %s' %info['ext_temp']
        print '#######################'
    except:
        print 'No response from focuser daemon'
    
def set_focuser(position):
    foc=Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    try:
        f=foc.set_focuser(position)
        if f: print f
    except:
        print 'No response from focuser daemon'
    
def move_focuser(steps):
    foc=Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    try:
        f=foc.move_focuser(steps)
        if f: print f
    except:
        print 'No response from focuser daemon'
    
def home_focuser():
    foc=Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    try:
        f=foc.home_focuser()
        if f: print f
    except:
        print 'No response from focuser daemon'

########################################################################
# Define interactive mode
def interactive():
    while 1:
        command=split(raw_input('foc> '))
        if(len(command)>0):
            if command[0]=='q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0]=='start':
        misc.startDaemon(FOC_DAEMON_PROCESS,FOC_DAEMON_HOST,stdout=FOC_DAEMON_OUTPUT)
    elif command[0]=='ping':
        misc.pingDaemon(FOC_DAEMON_ADDRESS)
    elif command[0]=='shutdown':
        misc.shutdownDaemon(FOC_DAEMON_ADDRESS)
    elif command[0]=='kill':
        misc.killDaemon(FOC_DAEMON_PROCESS,FOC_DAEMON_HOST)
    elif command[0]=='help':
        printInstructions()
    elif command[0]=='i':
        print 'Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Focuser control functions
    elif command[0]=='info':
        get_info()
    elif command[0]=='set':
        set_focuser(int(command[1]))
    elif command[0]=='move':
        move_focuser(int(command[1]))
    elif command[0]=='home':
        home_focuser()


    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    elif command[0]=='exit':
        print 'foc> Use "shutdown" to close the daemon or "q" to quit interactive mode'
    else:
        print 'foc> Command not recognized:',command[0]

def printInstructions():
    print 'Usage: foc start              - starts the focuser daemon'
    print '       foc shutdown           - shuts down the focuser daemon cleanly'
    print '       foc kill               - kills the focuser daemon (emergency use only!)'
    print '       foc ping               - pings the focuser daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       foc info               - reports current focuser data'
    print '       foc set [pos]          - moves the focuser to a given position'
    print '       foc move [steps]       - moves the focuser a number of steps'
    print '       foc home               - moves the focuser to home position'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       foc i                  - enter interactive (command line) usage'
    print '       foc q                  - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       foc help               - prints these instructions'

########################################################################
# Control System

if len(sys.argv)==1:
    printInstructions()
else:
    FOC_DAEMON_PROCESS=params.DAEMONS['foc']['PROCESS']
    FOC_DAEMON_HOST=params.DAEMONS['foc']['HOST']
    FOC_DAEMON_ADDRESS=params.DAEMONS['foc']['ADDRESS']
    FOC_DAEMON_OUTPUT=params.LOG_PATH+'foc_daemon-stdout.log'
    
    command=sys.argv[1:]    

    if command[0]=='i':
        interactive()
    else:
        query(command)
