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
from tecs_modules import misc
from tecs_modules import params

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
        print 'Timestamp: %s' %info['timestamp']
        print '###########################'
    except:
        print misc.ERROR('No response from dome daemon')
    
def open_dome(side='both',steps=None):
    dome = Pyro4.Proxy(DOME_DAEMON_ADDRESS)
    dome._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = dome.open_dome(side,steps)
        if c: print c
    except:
        print misc.ERROR('No response from dome daemon')
    
def close_dome(side='both',steps=None):
    dome = Pyro4.Proxy(DOME_DAEMON_ADDRESS)
    dome._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = dome.close_dome(side,steps)
        if c: print c
    except:
        print misc.ERROR('No response from dome daemon')
    
def halt_dome():
    dome = Pyro4.Proxy(DOME_DAEMON_ADDRESS)
    dome._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = dome.halt_dome()
        if c: print c
    except:
        print misc.ERROR('No response from dome daemon')

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('dome> '))
        if len(command) > 0:
            if command[0] == 'q' or command[0] == 'exit':
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
    elif command[0] == 'help' or command[0] == '?':
        print_instructions()
    elif command[0] == 'i':
        print misc.ERROR('Already in interactive mode')
    
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
        print misc.ERROR('Unrecognized command "%s"' %command[0])

def print_instructions():
    help_str = misc.bold('Usage:') + ' dome [command]' + '\n' +\
    ' ' + misc.undl('Daemon commands') + ':' + '\n' +\
    '  dome ' + misc.bold('start') + '                     - start the daemon' + '\n' +\
    '  dome ' + misc.bold('shutdown') + '                  - shutdown the daemon' + '\n' +\
    '  dome ' + misc.bold('kill') + '                      - kill the daemon (' + misc.rtxt('emergency use') + ')' + '\n' +\
    '  dome ' + misc.bold('ping') + '                      - ping the daemon' + '\n' +\
    ' ' + misc.undl('Dome commands') + ':' + '\n' +\
    '  dome ' + misc.bold('open') + ' [east|west] [steps]' + '  - open the dome' + '\n' +\
    '  dome ' + misc.bold('close') + ' [east|west] [steps]' + ' - close the dome' + '\n' +\
    '  dome ' + misc.bold('halt') + '                      - stop the dome moving' + '\n' +\
    '  dome ' + misc.bold('info') + ' [v]' + '                  - report current status' + '\n' +\
    ' ' + misc.undl('Control commands') + ':' + '\n' +\
    '  dome ' + misc.bold('i') + '                         - enter interactive mode' + '\n' +\
    '  dome ' + misc.bold('q') + '/' + misc.bold('exit') + '                    - quit interactive mode' + '\n' +\
    '  dome ' + misc.bold('?') + '/' + misc.bold('help') + '                    - print these instructions'
    print help_str

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
