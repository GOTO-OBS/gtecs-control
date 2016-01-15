#!/usr/bin/env python

########################################################################
#                                filt.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over filt_daemon          #
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
# Filter wheel control functions
def get_info():
    flist = params.FILTER_LIST
    filt = Pyro4.Proxy(FILT_DAEMON_ADDRESS)
    filt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = filt.get_info()
        print '#### FILTER WHEEL INFO ####'
        for tel in params.TEL_DICT.keys():
            print 'FILTER WHEEL ' + str(tel) + ' (%s-%i)'%tuple(params.TEL_DICT[tel])
            if info['status'+str(tel)] != 'Moving':
                print 'Status: %s' %info['status'+str(tel)]
                print 'Current filter:     %s' %flist[info['current_filter_num'+str(tel)]]
            else:
                print 'Status: %s (%i)' %(info['status'+str(tel)],info['remaining'+str(tel)])
                print 'Current filter:     N/A'
            print 'Current filter num: %s' %info['current_filter_num'+str(tel)]
            print 'Current motor pos:  %s' %info['current_pos'+str(tel)]
            print '~~~~~~~'
        print 'Uptime: %.1fs' %info['uptime']
        print 'Ping: %.5fs' %info['ping']
        print '###########################'
    except:
        print 'ERROR: No response from filter wheel daemon'

def get_info_summary():
    flist = params.FILTER_LIST
    filt = Pyro4.Proxy(FILT_DAEMON_ADDRESS)
    filt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = filt.get_info()
        for tel in params.TEL_DICT.keys():
            print 'FILTER WHEEL ' + str(tel) + ' (%s-%i)'%tuple(params.TEL_DICT[tel]),
            if info['status'+str(tel)] != 'Moving':
                print '  Current filter: %s' %flist[info['current_filter_num'+str(tel)]],
                print '  [%s]' %info['status'+str(tel)]
            else:
                #print '  Current filter: -',
                print '  %s (%i)' %(info['status'+str(tel)],info['remaining'+str(tel)])
    except:
        print 'ERROR: No response from filter wheel daemon'

def set_filter(new_filt,HW_list):
    filt = Pyro4.Proxy(FILT_DAEMON_ADDRESS)
    filt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = filt.set_filter(new_filt,HW_list)
        if c: print c
    except:
        print 'ERROR: No response from filter wheel daemon'

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('filt> '))
        if len(command) > 0:
            if command[0] == 'q' or command[0] == 'exit':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        misc.start_daemon(FILT_DAEMON_PROCESS, FILT_DAEMON_HOST, stdout=FILT_DAEMON_OUTPUT)
    elif command[0] == 'shutdown':
        misc.shutdown_daemon(FILT_DAEMON_ADDRESS)
    elif command[0] == 'kill':
        misc.kill_daemon(FILT_DAEMON_PROCESS,FILT_DAEMON_HOST)
    elif command[0] == 'ping':
        misc.ping_daemon(FILT_DAEMON_ADDRESS)
    elif command[0] == 'help' or command[0] == '?':
        print_instructions()
    elif command[0] == 'i':
        print 'ERROR: Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Filter wheel control functions
    elif command[0] == 'info':
        if len(command) > 1 and command[1] in ['v','V','-v','-V']:
            get_info()
        else:
            get_info_summary()
    
    elif command[0] == 'set':
        if command[1] in str(params.TEL_DICT.keys()):
            set_filter(command[2].upper(),[int(command[1])])
        else:
            set_filter(command[1].upper(),params.TEL_DICT.keys())
    elif command[0] == 'list':
        print params.FILTER_LIST

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'filt> Command not recognized:',command[0]

def print_instructions():
    print 'Usage: filt start                - starts the filter wheel daemon'
    print '       filt shutdown             - shuts down the filter wheel daemon cleanly'
    print '       filt kill                 - kills the filter wheel daemon (emergency use only!)'
    print '       filt ping                 - pings the filter wheel daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       filt info                 - reports current filter wheel data'
    print '       filt set [filter]         - sets all filter wheels to given filter'
    print '       filt set X [filter]       - sets filter wheel X to given filter'
    print '       filt list                 - lists the possible filters'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       filt i                    - enter interactive (command line) usage'
    print '       filt exit/q               - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       filt help/?               - prints these instructions'

########################################################################
# Control system

if len(sys.argv) == 1:
    print_instructions()
else:
    FILT_DAEMON_PROCESS = params.DAEMONS['filt']['PROCESS']
    FILT_DAEMON_HOST = params.DAEMONS['filt']['HOST']
    FILT_DAEMON_ADDRESS = params.DAEMONS['filt']['ADDRESS']
    FILT_DAEMON_OUTPUT = params.LOG_PATH + 'filt_daemon-stdout.log'
    
    command = sys.argv[1:]    
    if command[0] == 'i':
        interactive()
    else:
        query(command)
