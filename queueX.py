#!/usr/bin/env python

########################################################################
#                               queue.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#          G-TeCS script to provide control over queue_daemon          #
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
# Queue control functions
def get_info():
    queue = Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    queue._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = queue.get_info()
        print '####### QUEUE INFO #######'
        print 'Status: %s' %info['status']
        print '~~~~~~~'
        print 'Current exposure:'
        try:
            print '   %i: %i, %i, %s, %i, %s, %s, %s' \
                %(info['current_run_ID'], info['current_tel'], info['current_exptime'], info['current_filter'], info['current_bins'], info['current_frametype'], info['current_target'], info['current_imgtype'])
        except:
            print '   None'
        print 'Items in queue:     %s' %info['queue_length']
        print '~~~~~~~'
        print 'Uptime: %.1fs' %info['uptime']
        print 'Ping: %.3fs' %info['ping']
        print 'Timestamp: %s' %info['timestamp']
        print '###########################'
    except:
        print 'ERROR: No response from queue daemon'

def get_info_summary():
    queue = Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    queue._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = queue.get_info()
        print 'QUEUE: [%s]' %info['status']
        print '  Current exposure:',
        try:
            print '   %i: %i, %i, %s, %i, %s, %s, %s' \
                %(info['current_run_ID'], info['current_tel'], info['current_exptime'], info['current_filter'], info['current_bins'], info['current_frametype'], info['current_target'], info['current_imgtype'])
        except:
            print 'None'
        print '  Items in queue: %s' %info['queue_length']
    except:
        print 'ERROR: No response from queue daemon'

def take_image(tel,exptime,filt,bins=1,frametype='normal',target='N/A',imgtype='SCIENCE'):
    queue = Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    queue._pyroTimeout = params.PROXY_TIMEOUT
    if filt.upper() not in params.FILTER_LIST:
        print 'Filter needs to be one of', params.FILTER_LIST
        return
    if tel not in params.TEL_DICT.keys()+[0]:
        print 'Invalid tel number'
        return
    if 1:#try:
        c = queue.add(exptime,filt,tel,bins,frametype,target,imgtype)
        if c: print c
    #except:
     #   print 'ERROR: No response from queue daemon'

def pause():
    queue = Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    queue._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = queue.pause()
        if c: print c
    except:
        print 'ERROR: No response from queue daemon'

def resume():
    queue = Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    queue._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = queue.resume()
        if c: print c
    except:
        print 'ERROR: No response from queue daemon'


########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('queue> '))
        if len(command) > 0:
            if command[0] == 'q' or command[0] == 'exit':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        misc.start_daemon(QUEUE_DAEMON_PROCESS,QUEUE_DAEMON_HOST,stdout=QUEUE_DAEMON_OUTPUT)
    elif command[0] == 'shutdown':
        misc.shutdown_daemon(QUEUE_DAEMON_ADDRESS)
    elif command[0] == 'kill':
        misc.kill_daemon(QUEUE_DAEMON_PROCESS,QUEUE_DAEMON_HOST)
    elif command[0] == 'ping':
        misc.ping_daemon(QUEUE_DAEMON_ADDRESS)
    elif command[0] == 'help' or command[0] == '?':
        print_instructions()
    elif command[0] == 'i':
        print 'ERROR: Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    elif command[0] == 'info':
        if len(command) > 1 and command[1] in ['v','V','-v','-V']:
            get_info()
        else:
            get_info_summary()
    
    elif command[0] == 'image':
        if len(command) < 4:
            print 'ERROR: need at least telescopes, exptime and filter and bins'
        else:
            if len(command) == 5:
                take_image(int(command[1]),int(command[2]),command[3],int(command[4]))
            elif len(command) == 6:
                take_image(int(command[1]),int(command[2]),command[3],int(command[4]),command[5])
            elif len(command) == 7:
                take_image(int(command[1]),int(command[2]),command[3],int(command[4]),command[5],command[6])
            elif len(command) == 8:
                take_image(int(command[1]),int(command[2]),command[3],int(command[4]),command[5],command[6],command[7])
    elif command[0] == 'pause':
        pause()
    elif command[0] == 'resume' or command[0] == 'unpause':
        resume()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'queue> Command not recognized:',command[0]

def print_instructions():
    help_str = misc.bold('Usage:') + ' queue [command]' + '\n' +\
    ' ' + misc.undl('Daemon commands') + ':' + '\n' +\
    '  queue ' + misc.bold('start') + '          - start the daemon' + '\n' +\
    '  queue ' + misc.bold('shutdown') + '       - shutdown the daemon' + '\n' +\
    '  queue ' + misc.bold('kill') + '           - kill the daemon (' + misc.rtxt('emergency use') + ')' + '\n' +\
    '  queue ' + misc.bold('ping') + '           - ping the daemon' + '\n' +\
    ' ' + misc.undl('Queue commands') + ':' + '\n' +\
    '  queue ' + misc.bold('image') + ' [exptime] [filter] [bins] [object] [imgtype]' + '\n' +\
    '  queue ' + misc.bold('pause') + '          - pause taking exposures' + '\n' +\
    '  queue ' + misc.bold('unpause') + '/' + misc.bold('resume') + ' - resumes taking exposures' + '\n' +\
    '  queue ' + misc.bold('info') + ' [v]' + '       - report current status' + '\n' +\
    ' ' + misc.undl('Control commands') + ':' + '\n' +\
    '  queue ' + misc.bold('i') + '              - enter interactive mode' + '\n' +\
    '  queue ' + misc.bold('q') + '/' + misc.bold('exit') + '         - quit interactive mode' + '\n' +\
    '  queue ' + misc.bold('?') + '/' + misc.bold('help') + '         - print these instructions'
    print help_str
    
########################################################################
# Control System

if len(sys.argv) == 1:
    print_instructions()
else:
    QUEUE_DAEMON_PROCESS = params.DAEMONS['queue']['PROCESS']
    QUEUE_DAEMON_HOST = params.DAEMONS['queue']['HOST']
    QUEUE_DAEMON_ADDRESS = params.DAEMONS['queue']['ADDRESS']
    QUEUE_DAEMON_OUTPUT = params.LOG_PATH + 'queue_daemon-stdout.log'
    
    command = sys.argv[1:]
    if command[0] == 'i':
        interactive()
    else:
        query(command)
