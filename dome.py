#!/usr/bin/env python

########################################################################
#                                dome.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over dome_daemon          #
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
# Dome control functions
def get_info():
    dome = Pyro4.Proxy(DOME_DAEMON_ADDRESS)
    dome._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = dome.get_info()
        print '######## DOME INFO ########'
        print 'Dome:        %s' %info['dome']
        print 'Hatch:       %s' %info['hatch']
        print '~~~~~~~'
        print 'Uptime: %.1fs' %info['uptime']
        print 'Ping: %.5fs' %info['ping']
        print '###########################'
    except:
        print 'ERROR: No response from dome daemon'
    
def open_dome(side='both',steps=None):
    dome = Pyro4.Proxy(DOME_DAEMON_ADDRESS)
    dome._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = dome.open_dome(side,steps)
        if c: print c
    except:
        print 'ERROR: No response from dome daemon'
    
def close_dome(side='both',steps=None):
    dome = Pyro4.Proxy(DOME_DAEMON_ADDRESS)
    dome._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = dome.close_dome(side,steps)
        if c: print c
    except:
        print 'ERROR: No response from dome daemon'
    
def halt_dome():
    dome = Pyro4.Proxy(DOME_DAEMON_ADDRESS)
    dome._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = dome.halt_dome()
        if c: print c
    except:
        print 'ERROR: No response from dome daemon'


########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('dome> '))
        if len(command) > 0:
            if command[0] == 'q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        misc.start_daemon(DOME_DAEMON_PROCESS, DOME_DAEMON_HOST, stdout=DOME_DAEMON_OUTPUT)
    elif command[0] == 'shutdown':
        misc.shutdown_daemon(DOME_DAEMON_ADDRESS)
    elif command[0] == 'kill':
        misc.kill_daemon(DOME_DAEMON_PROCESS,DOME_DAEMON_HOST)
    elif command[0] == 'ping':
        misc.ping_daemon(DOME_DAEMON_ADDRESS)
    elif command[0] == 'help':
        print_instructions()
    elif command[0] == 'i':
        print 'ERROR: Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Filter wheel control functions
    elif command[0] == 'info':
        get_info()
    elif command[0] == 'open':
        if len(command) == 1:
            open_dome()
        elif len(command) == 3:
            open_dome(command[1], command[2])
    elif command[0] == 'close':
        if len(command) == 1:
            close_dome()
        elif len(command) == 3:
            close_dome(command[1], command[2])
    elif command[0] == 'halt':
        halt_dome()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'dome> Command not recognized:',command[0]

def print_instructions():
    print 'Usage: dome start                     - starts the dome daemon'
    print '       dome shutdown                  - shuts down the dome daemon cleanly'
    print '       dome kill                      - kills the dome daemon (emergency use only!)'
    print '       dome ping                      - pings the dome daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       dome info                      - reports current dome data'
    print '       dome open                      - fully opens the dome'
    print '       dome close                     - fully closes the dome'
    print '       dome open [east|west] [steps]  - partially opens one side of the dome'
    print '       dome close [east|west] [steps] - partially closes one side of the dome'
    print '       dome halt                      - stops the dome moving'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       dome i                         - enter interactive (command line) usage'
    print '       dome q                         - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       dome help                      - prints these instructions'

########################################################################
# Control system

if len(sys.argv) == 1:
    print_instructions()
else:
    DOME_DAEMON_PROCESS = params.DAEMONS['dome']['PROCESS']
    DOME_DAEMON_HOST = params.DAEMONS['dome']['HOST']
    DOME_DAEMON_ADDRESS = params.DAEMONS['dome']['ADDRESS']
    DOME_DAEMON_OUTPUT = params.LOG_PATH + 'dome_daemon-stdout.log'
    
    command = sys.argv[1:]    
    if command[0] == 'i':
        interactive()
    else:
        query(command)
