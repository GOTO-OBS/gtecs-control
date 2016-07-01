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
from __future__ import absolute_import
from __future__ import print_function
import os, sys
import readline
import time
import Pyro4
# TeCS modules
from tecs_modules import misc
from tecs_modules import params
from six.moves import input

########################################################################
# Focuser control functions
def get_info():
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = foc.get_info()
        print('###### FOCUSER INFO #######')
        for tel in params.TEL_DICT:
            print('FOCUSER ' + str(tel) + ' (%s-%i)'%tuple(params.TEL_DICT[tel]))
            if info['status'+str(tel)] != 'Moving':
                print('Status: %s' %info['status'+str(tel)])
            else:
                print('Status: %s (%i)' %(info['status'+str(tel)],info['remaining'+str(tel)]))
            print('Current motor pos:    %s' %info['current_pos'+str(tel)])
            print('Maximum motor limit:  %s' %info['limit'+str(tel)])
            print('Internal temperature: %s' %info['int_temp'+str(tel)])
            print('External temperature: %s' %info['ext_temp'+str(tel)])
            print('Serial number:        %s' %info['serial_number'+str(tel)])
            print('~~~~~~~')
        print('Uptime: %.1fs' %info['uptime'])
        print('Ping: %.5fs' %info['ping'])
        print('Timestamp: %s' %info['timestamp'])
        print('###########################')
    except:
        print(misc.ERROR('No response from focuser daemon'))

def get_info_summary():
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = foc.get_info()
        for tel in params.TEL_DICT:
            print('FOCUSER ' + str(tel) + ' (%s-%i)'%tuple(params.TEL_DICT[tel]), end=' ')
            if info['status'+str(tel)] != 'Moving':
                print('  Current position: %s/%s' %(info['current_pos'+str(tel)],info['limit'+str(tel)]), end=' ')
                print('  [%s]' %info['status'+str(tel)])
            else:
                print('  %s (%i)' %(info['status'+str(tel)],info['remaining'+str(tel)]))
    except:
        print(misc.ERROR('No response from focuser daemon'))

def set_focuser(pos,HW_list):
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = foc.set_focuser(pos,HW_list)
        if c: print(c)
    except:
        print(misc.ERROR('No response from focuser daemon'))

def move_focuser(steps,HW_list):
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = foc.move_focuser(steps,HW_list)
        if c: print(c)
    except:
        print(misc.ERROR('No response from focuser daemon'))

def home_focuser(HW_list):
    foc = Pyro4.Proxy(FOC_DAEMON_ADDRESS)
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = foc.home_focuser(HW_list)
        if c: print(c)
    except:
        print(misc.ERROR('No response from focuser daemon'))

########################################################################
# Interactive mode
def interactive():
    while True:
        command = input('foc> ').split()
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
        print(misc.ERROR('Already in interactive mode'))

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Focuser control functions
    elif command[0] == 'info':
        if len(command) == 1:
            get_info_summary()
        elif len(command) == 2 and command[1] in ['v','V','-v','-V']:
            get_info()
        else:
            print(misc.ERROR('Invalid arguments'))

    elif command[0] == 'set':
        if len(command) == 2 and misc.is_num(command[1]):
            set_focuser(int(command[1]),list(params.TEL_DICT))
        elif len(command) == 3 and misc.is_num(command[2]):
            valid = misc.valid_ints(command[1].split(','),list(params.TEL_DICT))
            if len(valid) > 0:
                set_focuser(int(command[2]),valid)
        else:
            print(misc.ERROR('Invalid arguments'))

    elif command[0] == 'move':
        if len(command) == 2 and misc.is_num(command[1]):
            move_focuser(int(command[1]),list(params.TEL_DICT))
        elif len(command) == 3 and misc.is_num(command[2]):
            valid = misc.valid_ints(command[1].split(','),list(params.TEL_DICT))
            if len(valid) > 0:
                move_focuser(int(command[2]),valid)
        else:
            print(misc.ERROR('Invalid arguments'))

    elif command[0] == 'home':
        if len(command) == 1:
            home_focuser(list(params.TEL_DICT))
        elif len(command) == 2:
            valid = misc.valid_ints(command[1].split(','),list(params.TEL_DICT))
            if len(valid) > 0:
                home_focuser(valid)
        else:
            print(misc.ERROR('Invalid arguments'))

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print(misc.ERROR('Unrecognized command "%s"' %command[0]))

def print_instructions():
    help_str = misc.bold('Usage:') + ' foc [command]' + '\n' +\
    ' ' + misc.undl('Daemon commands') + ':' + '\n' +\
    '  foc ' + misc.bold('start') + '             - start the daemon' + '\n' +\
    '  foc ' + misc.bold('shutdown') + '          - shutdown the daemon' + '\n' +\
    '  foc ' + misc.bold('kill') + '              - kill the daemon (' + misc.rtxt('emergency use') + ')' + '\n' +\
    '  foc ' + misc.bold('ping') + '              - ping the daemon' + '\n' +\
    ' ' + misc.undl('Focuser commands') + ':' + '\n' +\
    '  foc ' + misc.bold('move') + ' [tels] steps' + ' - move by the given steps' + '\n' +\
    '  foc ' + misc.bold('set') + ' [tels] pos' + '    - move to the given position' + '\n' +\
    '  foc ' + misc.bold('home') + ' [tels]' + '       - move to the home position' + '\n' +\
    '  foc ' + misc.bold('info') + ' [v]' + '          - report current status' + '\n' +\
    ' ' + misc.undl('Control commands') + ':' + '\n' +\
    '  foc ' + misc.bold('i') + '                 - enter interactive mode' + '\n' +\
    '  foc ' + misc.bold('q') + '/' + misc.bold('exit') + '            - quit interactive mode' + '\n' +\
    '  foc ' + misc.bold('?') + '/' + misc.bold('help') + '            - print these instructions'
    print(help_str)

########################################################################
# Control system

if len(sys.argv) == 1:
    print_instructions()
else:
    FOC_DAEMON_PROCESS = params.DAEMONS['foc']['PROCESS']
    FOC_DAEMON_HOST = params.DAEMONS['foc']['HOST']
    FOC_DAEMON_ADDRESS = params.DAEMONS['foc']['ADDRESS']
    if params.REDIRECT_STDOUT:
        FOC_DAEMON_OUTPUT = params.LOG_PATH + 'foc_daemon-stdout.log'
    else:
        FOC_DAEMON_OUTPUT = '/dev/stdout'

    command = sys.argv[1:]
    if command[0] == 'i':
        interactive()
    else:
        query(command)
