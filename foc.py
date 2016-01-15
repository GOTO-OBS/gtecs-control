#!/usr/bin/env python

########################################################################
#                                foc.py                                #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over foc_daemon           #
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
# Focuser control functions
def get_info():
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = foc.get_info()
        print '###### FOCUSER INFO #######'
        for tel in params.TEL_DICT.keys():
            print 'FOCUSER ' + str(tel) + ' (%s-%i)'%tuple(params.TEL_DICT[tel])
            if info['status'+str(tel)] != 'Moving':
                print 'Status: %s' %info['status'+str(tel)]
            else:
                print 'Status: %s (%i)' %(info['status'+str(tel)],info['remaining'+str(tel)])
            print 'Current motor pos:    %s' %info['current_pos'+str(tel)]
            print 'Maximum motor limit:  %s' %info['limit'+str(tel)]
            print 'Internal temperature: %s' %info['int_temp'+str(tel)]
            print 'External temperature: %s' %info['ext_temp'+str(tel)]
            print '~~~~~~~'
        print 'Uptime: %.1fs' %info['uptime']
        print 'Ping: %.5fs' %info['ping']
        print '###########################'
    except:
        print 'ERROR: No response from focuser daemon'

def get_info_summary():
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = foc.get_info()
        for tel in params.TEL_DICT.keys():
            print 'FOCUSER ' + str(tel) + ' (%s-%i)'%tuple(params.TEL_DICT[tel]),
            if info['status'+str(tel)] != 'Moving':
                print '  Current position: %s/%s' %(info['current_pos'+str(tel)],info['limit'+str(tel)]),
                print '  [%s]' %info['status'+str(tel)]
            else:
                print '  %s (%i)' %(info['status'+str(tel)],info['remaining'+str(tel)])
    except:
        print 'ERROR: No response from focuser daemon'

def set_focuser(pos,HW_list):
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = foc.set_focuser(pos,HW_list)
        print pos, HW_list
        if c: print c
    except:
        print 'ERROR: No response from focuser daemon'

def move_focuser(steps,HW_list):
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = foc.move_focuser(steps,HW_list)
        if c: print c
    except:
        print 'ERROR: No response from focuser daemon'

def home_focuser(HW_list):
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = foc.home_focuser(HW_list)
        if c: print c
    except:
        print 'ERROR: No response from focuser daemon'

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('foc> '))
        if len(command) > 0:
            if command[0] == 'q' or command[0] == 'exit':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        misc.start_daemon(FOC_DAEMON_PROCESS, FOC_DAEMON_HOST, stdout=FOC_DAEMON_OUTPUT)
    elif command[0] == 'shutdown':
        misc.shutdown_daemon(FOC_DAEMON_ADDRESS)
    elif command[0] == 'kill':
        misc.kill_daemon(FOC_DAEMON_PROCESS, FOC_DAEMON_HOST)
    elif command[0] == 'ping':
        misc.ping_daemon(FOC_DAEMON_ADDRESS)
    elif command[0] == 'help' or command[0] == '?':
        print_instructions()
    elif command[0] == 'i':
        print 'ERROR: Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Focuser control functions
    elif command[0] == 'info':
        if len(command) > 1 and command[1] in ['v','V','-v','-V']:
            get_info()
        else:
            get_info_summary()
    
    elif command[0] == 'set':
        if len(command) > 2 and command[1] in str(params.TEL_DICT.keys()):
            set_focuser(int(command[2]),[int(command[1])])
        else:
            set_focuser(int(command[1]),params.TEL_DICT.keys())
    elif command[0] == 'move':
        if len(command) > 2 and command[1] in str(params.TEL_DICT.keys()):
            move_focuser(int(command[2]),[int(command[1])])
        else:
            move_focuser(int(command[1]),params.TEL_DICT.keys())
    elif command[0] == 'home':
        if len(command) > 1 and command[1] in str(params.TEL_DICT.keys()):
            home_focuser([int(command[1])])
        else:
            home_focuser(params.TEL_DICT.keys())
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'foc> Command not recognized:',command[0]

def print_instructions():
    print 'Usage: foc start              - starts the focuser daemon'
    print '       foc shutdown           - shuts down the focuser daemon cleanly'
    print '       foc kill               - kills the focuser daemon (emergency use only!)'
    print '       foc ping               - pings the focuser daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       foc info               - reports current focuser data'
    print '       foc set [pos]          - moves all focusers to a given position'
    print '       foc set X [pos]        - moves focuser X to a given position'
    print '       foc move [steps]       - moves all focusers a number of steps'
    print '       foc move X [steps]     - moves focuser X a number of steps'
    print '       foc home               - moves all focusers to home position'
    print '       foc home X             - moves focuser X to home position'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       foc i                  - enter interactive (command line) usage'
    print '       foc q                  - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       foc help               - prints these instructions'

########################################################################
# Control system

if len(sys.argv) == 1:
    print_instructions()
else:
    FOC_DAEMON_PROCESS = params.DAEMONS['foc']['PROCESS']
    FOC_DAEMON_HOST = params.DAEMONS['foc']['HOST']
    FOC_DAEMON_ADDRESS = params.DAEMONS['foc']['ADDRESS']
    FOC_DAEMON_OUTPUT = params.LOG_PATH + 'foc_daemon-stdout.log'
    
    command = sys.argv[1:]
    if command[0] == 'i':
        interactive()
    else:
        query(command)
