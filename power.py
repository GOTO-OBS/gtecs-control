from __future__ import absolute_import
from __future__ import print_function
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
from tecs_modules import misc
from tecs_modules import params
from six.moves import range
from six.moves import input

########################################################################
# Power control functions
def get_info():
    power = Pyro4.Proxy(POWER_DAEMON_ADDRESS)
    power._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = power.get_info()
        print('####### POWER INFO ########')
        for i in range(len(info['status_dict'])):
            outlet_name = params.POWER_LIST[i]
            outlet_status = info['status_dict'][outlet_name]
            print('Power outlet %i (%s):\t%s' %(i+1,outlet_name,outlet_status))
        print('~~~~~~~')
        print('Uptime: %.1fs' %info['uptime'])
        print('Ping: %.5fs' %info['ping'])
        print('Timestamp: %s' %info['timestamp'])
        print('###########################')
    except:
        print(misc.ERROR('No response from power daemon'))
    
def on(outlet):
    power = Pyro4.Proxy(POWER_DAEMON_ADDRESS)
    power._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = power.on(outlet)
        if c: print(c)
    except:
        print(misc.ERROR('No response from power daemon'))
    
def off(outlet):
    power = Pyro4.Proxy(POWER_DAEMON_ADDRESS)
    power._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = power.off(outlet)
        if c: print(c)
    except:
        print(misc.ERROR('No response from power daemon'))

def reboot(outlet):
    power = Pyro4.Proxy(POWER_DAEMON_ADDRESS)
    try:
        c = power.reboot(outlet)
        if c: print(c)
    except:
        print(misc.ERROR('No response from power daemon'))

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(input('power> '))
        if len(command) > 0:
            if command[0] == 'q' or command[0] == 'exit':
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
    elif command[0] == 'help' or command[0] == '?':
        print_instructions()
    elif command[0] == 'i':
        print(misc.ERROR('Already in interactive mode'))
    
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
        print(misc.ERROR('Unrecognized command "%s"' %command[0]))

def print_instructions():
    help_str = misc.bold('Usage:') + ' power [command]' + '\n' +\
    ' ' + misc.undl('Daemon commands') + ':' + '\n' +\
    '  power ' + misc.bold('start') + '           - start the daemon' + '\n' +\
    '  power ' + misc.bold('shutdown') + '        - shutdown the daemon' + '\n' +\
    '  power ' + misc.bold('kill') + '            - kill the daemon (' + misc.rtxt('emergency use') + ')' + '\n' +\
    '  power ' + misc.bold('ping') + '            - ping the daemon' + '\n' +\
    ' ' + misc.undl('Power commands') + ':' + '\n' +\
    '  power ' + misc.bold('on') + ' [outlet]' + '     - turn on specified outlet' + '\n' +\
    '  power ' + misc.bold('off') + ' [outlet]' + '    - turn off specified outlet' + '\n' +\
    '  power ' + misc.bold('reboot') + ' [outlet]' + ' - reboots specified outlet' + '\n' +\
    '  power ' + misc.bold('info') + ' [v]' + '        - report current status' + '\n' +\
    ' ' + misc.undl('Control commands') + ':' + '\n' +\
    '  power ' + misc.bold('i') + '               - enter interactive mode' + '\n' +\
    '  power ' + misc.bold('q') + '/' + misc.bold('exit') + '          - quit interactive mode' + '\n' +\
    '  power ' + misc.bold('?') + '/' + misc.bold('help') + '          - print these instructions'
    print(help_str)

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
