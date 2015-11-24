#!/usr/bin/env python

########################################################################
#                                power.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over power_daemon         #
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
# Power control functions
def get_info():
    power = Pyro4.Proxy(POWER_DAEMON_ADDRESS)
    power._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = power.get_info()
        print '####### POWER INFO ########'
        for i in range(len(info['status_dict'])):
            outlet_name = params.POWER_LIST[i]
            outlet_status = info['status_dict'][outlet_name]
            print 'Power outlet %i (%s):\t%s' %(i+1,outlet_name,outlet_status)
        print '~~~~~~~'
        print 'Uptime: %.1fs' %info['uptime']
        print 'Ping: %.5fs' %info['ping']
        print '###########################'
    except:
        print 'ERROR: No response from power daemon'
    
def on(outlet):
    power = Pyro4.Proxy(POWER_DAEMON_ADDRESS)
    power._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = power.on(outlet)
        if c: print c
    except:
        print 'ERROR: No response from power daemon'
    
def off(outlet):
    power = Pyro4.Proxy(POWER_DAEMON_ADDRESS)
    power._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = power.off(outlet)
        if c: print c
    except:
        print 'ERROR: No response from power daemon'

def reboot(outlet):
    power = Pyro4.Proxy(POWER_DAEMON_ADDRESS)
    try:
        c = power.reboot(outlet)
        if c: print c
    except:
        print 'ERROR: No response from power daemon'

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('power> '))
        if len(command) > 0:
            if command[0] == 'q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        misc.start_daemon(POWER_DAEMON_PROCESS, POWER_DAEMON_HOST, stdout=POWER_DAEMON_OUTPUT)
    elif command[0] == 'shutdown':
        misc.shutdown_daemon(POWER_DAEMON_ADDRESS)
    elif command[0] == 'kill':
        misc.kill_daemon(POWER_DAEMON_PROCESS,POWER_DAEMON_HOST)
    elif command[0] == 'ping':
        misc.ping_daemon(POWER_DAEMON_ADDRESS)
    elif command[0] == 'help':
        print_instructions()
    elif command[0] == 'i':
        print 'ERROR: Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Filter wheel control functions
    elif command[0] == 'info':
        get_info()
    elif command[0] == 'on':
        on(command[1])
    elif command[0] == 'off':
        off(command[1])
    elif command[0] == 'reboot':
        reboot(command[1])

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'power> Command not recognized:',command[0]

def print_instructions():
    print 'Usage: power start              - starts the power daemon'
    print '       power shutdown           - shuts down the power daemon cleanly'
    print '       power kill               - kills the power daemon (emergency use only!)'
    print '       power ping               - pings the power daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       power info               - reports current power data'
    print '       power on [outlet]        - turns on specified outlet'
    print '       power off [outlet]       - turns off specified outlet'
    print '       power reboot [outlet]    - reboots specifiec outlet'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       power i                  - enter interactive (command line) usage'
    print '       power q                  - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       power help               - prints these instructions'

########################################################################
# Control system

if len(sys.argv) == 1:
    print_instructions()
else:
    POWER_DAEMON_PROCESS = params.DAEMONS['power']['PROCESS']
    POWER_DAEMON_HOST = params.DAEMONS['power']['HOST']
    POWER_DAEMON_ADDRESS = params.DAEMONS['power']['ADDRESS']
    POWER_DAEMON_OUTPUT = params.LOG_PATH + 'power_daemon-stdout.log'
    
    command = sys.argv[1:]    
    if command[0] == 'i':
        interactive()
    else:
        query(command)
