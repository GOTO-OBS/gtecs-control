#!/usr/bin/env python

########################################################################
#                               queue.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#          G-TeCS script to provide control over queue_daemon          #
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
# Queue control functions
def get_info():
    queue=Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    try:
        info = queue.get_info()
        print '####### QUEUE INFO #######'
        print 'Status: %s' %info['status']
        print '~~~~~~~'
        print 'Current exposure:'
        try:
            print '   %i, %s, %i, %s' %(info['current_exptime'], info['current_filter'], info['current_bins'], info['current_frametype'])
        except:
            print '   None'
        print 'Items in queue:     %s' %info['queue_length']
        print '~~~~~~~'
        print 'Uptime: %.1fs' %info['uptime']
        print 'Ping: %.3fs' %info['ping']
        print '###########################'
    except:
        print 'No response from queue daemon'
        
def take_image(exptime,filt,bins,frametype='normal',obj='None',imgtype='SCIENCE',dbID='manual'):
    queue=Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    if filt.upper() not in params.FILTER_LIST:
        print 'Filter needs to be one of', params.FILTER_LIST
        return
    try:
        queue.add(exptime,filt,bins,frametype,obj,imgtype,dbID,False)
        print 'Added exposure to queue'
    except:
        print 'No response from queue daemon'

def pause():
    queue=Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    try:
        queue.pause()
    except:
        print 'No response from queue daemon'
    
def resume():
    queue=Pyro4.Proxy(QUEUE_DAEMON_ADDRESS)
    try:
        queue.resume()
    except:
        print 'No response from queue daemon'


########################################################################
# Define interactive mode
def interactive():
    while 1:
        command=split(raw_input('queue> '))
        if(len(command)>0):
            if command[0]=='q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0]=='start':
        misc.startDaemon(QUEUE_DAEMON_PROCESS,QUEUE_DAEMON_HOST,stdout=QUEUE_DAEMON_OUTPUT)
    elif command[0]=='ping':
        misc.pingDaemon(QUEUE_DAEMON_ADDRESS)
    elif command[0]=='shutdown':
        misc.shutdownDaemon(QUEUE_DAEMON_ADDRESS)
    elif command[0]=='kill':
        misc.killDaemon(QUEUE_DAEMON_PROCESS,QUEUE_DAEMON_HOST)
    elif command[0]=='help':
        printInstructions()
    elif command[0]=='i':
        print 'Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    elif command[0]=='info':
        get_info()
    elif command[0]=='image':
        if len(command) < 4:
            print 'ERROR: need at least exptime, filter and bins'
        else:
            if len(command) == 4:
                print command
                take_image(int(command[1]),command[2],int(command[3]))
            elif len(command) == 5:
                take_image(int(command[1]),command[2],int(command[3]),command[4])
            elif len(command) == 6:
                take_image(int(command[1]),command[2],int(command[3]),command[4],command[5])
            elif len(command) == 7:
                take_image(int(command[1]),command[2],int(command[3]),command[4],command[5],command[6])
    elif command[0]=='pause':
        pause()
    elif command[0]=='resume':
        resume()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    elif command[0]=='exit':
        print 'queue> Use "shutdown" to close the daemon or "q" to quit interactive mode'
    else:
        print 'queue> Command not recognized:',command[0]

def printInstructions():
    print 'Usage: queue start                   - starts the queue daemon'
    print '       queue shutdown                - shuts down the queue daemon cleanly'
    print '       queue kill                    - kills the queue daemon (emergency use only!)'
    print '       queue ping                    - pings the queue daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       queue info                    - reports current queue data'
    print '       queue image [exptime] [filter] [bins] <[object] [imgtype] [databaseID]>'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       queue pause                   - pauses taking exposures'
    print '       queue resume                  - resumes taking exposures'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       queue i                       - enter interactive (command line) usage'
    print '       queue q                       - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       queue help                    - prints these instructions'

########################################################################
# Control System

if len(sys.argv)==1:
    printInstructions()
else:
    QUEUE_DAEMON_PROCESS=params.DAEMONS['queue']['PROCESS']
    QUEUE_DAEMON_HOST=params.DAEMONS['queue']['HOST']
    QUEUE_DAEMON_ADDRESS=params.DAEMONS['queue']['ADDRESS']
    QUEUE_DAEMON_OUTPUT=params.LOG_PATH+'queue_daemon-stdout.log'
    
    command=sys.argv[1:]    

    if command[0]=='i':
        interactive()
    else:
        query(command)
