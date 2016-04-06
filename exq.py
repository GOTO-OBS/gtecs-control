#!/usr/bin/env python

########################################################################
#                                exq.py                                #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over exq_daemon           #
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
# Exposure queue control functions
def get_info():
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = exq.get_info()
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
        print misc.ERROR('No response from exposure queue daemon')

def get_info_summary():
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = exq.get_info()
        print 'QUEUE: [%s]' %info['status']
        print '  Current exposure:',
        try:
            print '   %i: %i, %i, %s, %i, %s, %s, %s' \
                %(info['current_run_ID'], info['current_tel'], info['current_exptime'], info['current_filter'], info['current_bins'], info['current_frametype'], info['current_target'], info['current_imgtype'])
        except:
            print 'None'
        print '  Items in queue: %s' %info['queue_length']
    except:
        print misc.ERROR('No response from exposure queue daemon')

def take_image(tel_list,exptime,filt,bins,target='N/A',imgtype='SCIENCE'):
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    
    frametype = 'normal'
    try:
        c = exq.add(tel_list,exptime,filt,bins,frametype,target,imgtype)
        if c: print c
    except:
        print misc.ERROR('No response from exposure queue daemon')

def take_dark(tel_list,exptime,bins):
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    
    filt = params.DARKFILT
    frametype = 'dark'
    target = 'N/A'
    imgtype = 'DARK'
    try:
        c = exq.add(tel_list,exptime,filt,bins,frametype,target,imgtype)
        if c: print c
    except:
        print misc.ERROR('No response from exposure queue daemon')

def take_bias(tel_list,bins):
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    
    exptime = params.BIASEXP
    filt = params.DARKFILT
    frametype = 'dark'
    target = 'N/A'
    imgtype = 'BIAS'
    try:
        c = exq.add(tel_list,exptime,filt,bins,frametype,target,imgtype)
        if c: print c
    except:
        print misc.ERROR('No response from exposure queue daemon')

def pause():
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = exq.pause()
        if c: print c
    except:
        print misc.ERROR('No response from exposure queue daemon')

def resume():
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = exq.resume()
        if c: print c
    except:
        print misc.ERROR('No response from exposure queue daemon')

def get_queue():
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    try:
        queue_list = exq.get()
        print queue_list
    except:
        print misc.ERROR('No response from exposure queue daemon')

def get_queue_summary():
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    try:
        queue_list = exq.get_simple()
        print queue_list
    except:
        print misc.ERROR('No response from exposure queue daemon')

def clear():
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = exq.clear()
        if c: print c
    except:
        print misc.ERROR('No response from exposure queue daemon')

########################################################################
# Interactive mode
def interactive():
    while True:
        command = split(raw_input('exq> '))
        if len(command) > 0:
            if command[0] == 'q' or command[0] == 'exit':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        misc.start_daemon(EXQ_DAEMON_PROCESS,EXQ_DAEMON_HOST,stdout=EXQ_DAEMON_OUTPUT)
    elif command[0] == 'shutdown':
        misc.shutdown_daemon(EXQ_DAEMON_ADDRESS)
    elif command[0] == 'kill':
        misc.kill_daemon(EXQ_DAEMON_PROCESS,EXQ_DAEMON_HOST)
    elif command[0] == 'ping':
        misc.ping_daemon(EXQ_DAEMON_ADDRESS)
    elif command[0] == 'help' or command[0] == '?':
        print_instructions()
    elif command[0] == 'i':
        print misc.ERROR('Already in interactive mode')
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera control functions
    elif command[0] == 'info':
        if len(command) == 1:
            get_info_summary()
        elif len(command) == 2 and command[1] in ['v','V','-v','-V']:
            get_info()
        else:
            print misc.ERROR('Invalid arguments')
    
    elif command[0] == 'image':
        if len(command) < 4:
            print misc.ERROR('Need at least: exptime filter bins')
        elif misc.is_num(command[1]) and misc.is_num(command[3]):
            # exptime filter bins [object] [imgtype]
            exptime = float(command[1])
            filt = command[2]
            bins = int(command[3])
            if len(command) == 4:
                take_image(params.TEL_DICT.keys(),exptime,filt,bins)
            elif len(command) == 5:
                target = command[4]
                take_image(params.TEL_DICT.keys(),exptime,filt,bins,target)
            elif len(command) == 6:
                target = command[4]
                imgtype = command[5]
                take_image(params.TEL_DICT.keys(),exptime,filt,bins,target,imgtype)
        elif misc.is_num(command[2]) and misc.is_num(command[4]):
            # tels exptime filter bins [object] [imgtype]
            valid = misc.valid_ints(command[1].split(','),params.TEL_DICT.keys())
            exptime = float(command[2])
            filt = command[3]
            bins = int(command[4])
            if len(command) == 5 and len(valid) > 0:
                take_image(valid,exptime,filt,bins)
            elif len(command) == 6 and len(valid) > 0:
                target = command[5]
                take_image(valid,exptime,filt,bins,target)
            elif len(command) == 7 and len(valid) > 0:
                target = command[5]
                imgtype = command[6]
                take_image(valid,exptime,filt,bins,target,imgtype)
        else:
            print misc.ERROR('Invalid arguments')
    
    elif command[0] == 'multimage':
        if len(command) < 5:
            print misc.ERROR('Need at least: Nexp exptime filter bins')
        elif misc.is_num(command[1]) and misc.is_num(command[2]) and misc.is_num(command[4]):
            # Nexp exptime filter bins [object] [imgtype]
            Nexp = int(command[1])
            exptime = float(command[2])
            filt = command[3]
            bins = int(command[4])
            if len(command) == 5:
                for i in range(Nexp):
                    take_image(params.TEL_DICT.keys(),exptime,filt,bins)
            elif len(command) == 6:
                target = command[5]
                for i in range(Nexp):
                    take_image(params.TEL_DICT.keys(),exptime,filt,bins,target)
            elif len(command) == 7:
                target = command[5]
                imgtype = command[6]
                for i in range(Nexp):
                    take_image(params.TEL_DICT.keys(),exptime,filt,bins,target,imgtype)
        elif misc.is_num(command[1]) and misc.is_num(command[3]) and misc.is_num(command[5]):
            # Nexp tels exptime filter bins [object] [imgtype]
            Nexp = int(command[1])
            valid = misc.valid_ints(command[2].split(','),params.TEL_DICT.keys())
            exptime = float(command[3])
            filt = command[4]
            bins = int(command[5])
            if len(command) == 6 and len(valid) > 0:
                for i in range(Nexp):
                    take_image(valid,exptime,filt,bins)
            elif len(command) == 7 and len(valid) > 0:
                target = command[6]
                for i in range(Nexp):
                    take_image(valid,exptime,filt,bins,target)
            elif len(command) == 8 and len(valid) > 0:
                target = command[6]
                imgtype = command[7]
                for i in range(Nexp):
                    take_image(valid,exptime,filt,bins,target,imgtype)
        else:
            print misc.ERROR('Invalid arguments')
    
    elif command[0] == 'dark':
        if len(command) < 3:
            print misc.ERROR('Need at least: exptime bins')
        elif misc.is_num(command[1]) and misc.is_num(command[2]) and len(command) == 3:
            # exptime bins
            exptime = float(command[1])
            bins = int(command[2])
            take_dark(params.TEL_DICT.keys(),exptime,bins)
        elif misc.is_num(command[2]) and misc.is_num(command[3]) and len(command) == 4:
            # tels exptime bins
            valid = misc.valid_ints(command[1].split(','),params.TEL_DICT.keys())
            exptime = float(command[2])
            bins = int(command[3])
            if len(valid) > 0:
                take_dark(valid,exptime,bins)
        else:
            print misc.ERROR('Invalid arguments')
    
    elif command[0] == 'multdark':
        if len(command) < 4:
            print misc.ERROR('Need at least: exptime bins')
        elif misc.is_num(command[2]) and misc.is_num(command[3]) and len(command) == 4:
            # Nexp exptime bins
            Nexp = int(command[1])
            exptime = float(command[2])
            bins = int(command[3])
            for i in range(Nexp):
                take_dark(params.TEL_DICT.keys(),exptime,bins)
        elif misc.is_num(command[3]) and misc.is_num(command[4]) and len(command) == 5:
            # Nexp tels exptime bins
            Nexp = int(command[1])
            valid = misc.valid_ints(command[2].split(','),params.TEL_DICT.keys())
            exptime = float(command[3])
            bins = int(command[4])
            if len(valid) > 0:
                for i in range(Nexp):
                    take_dark(valid,exptime,bins)
        else:
            print misc.ERROR('Invalid arguments')
    
    elif command[0] == 'bias':
        if len(command) < 2:
            print misc.ERROR('Need at least: bins')
        elif misc.is_num(command[1]) and len(command) == 2:
            # bins
            bins = int(command[1])
            take_bias(params.TEL_DICT.keys(),bins)
        elif misc.is_num(command[2]) and len(command) == 3:
            # tels bins
            valid = misc.valid_ints(command[1].split(','),params.TEL_DICT.keys())
            bins = int(command[2])
            if len(valid) > 0:
                take_bias(valid,bins)
        else:
            print misc.ERROR('Invalid arguments')
    
    elif command[0] == 'multbias':
        if len(command) < 3:
            print misc.ERROR('Need at least: bins')
        elif misc.is_num(command[2]) and len(command) == 3:
            # Nexp bins
            Nexp = int(command[1])
            bins = int(command[2])
            for i in range(Nexp):
                take_bias(params.TEL_DICT.keys(),bins)
        elif misc.is_num(command[3]) and len(command) == 4:
            # Nexp tels bins
            Nexp = int(command[1])
            valid = misc.valid_ints(command[2].split(','),params.TEL_DICT.keys())
            bins = int(command[3])
            if len(valid) > 0:
                for i in range(Nexp):
                    take_bias(valid,bins)
        else:
            print misc.ERROR('Invalid arguments')
    
    elif command[0] == 'pause':
        pause()
    elif command[0] == 'resume' or command[0] == 'unpause':
        resume()
    elif command[0] == 'get' or command[0] == 'list' or command[0] == 'ls':
        if len(command) == 1:
            get_queue_summary()
        elif len(command) == 2 and command[1] in ['v','V','-v','-V']:
            get_queue()
        else:
            print misc.ERROR('Invalid arguments')
    elif command[0] == 'clear':
        clear()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print 'exq> Command not recognized:',command[0]

def print_instructions():
    help_str = misc.bold('Usage:') + ' exq [command]' + '\n' +\
    ' ' + misc.undl('Daemon commands') + ':' + '\n' +\
    '  exq ' + misc.bold('start') + '          - start the daemon' + '\n' +\
    '  exq ' + misc.bold('shutdown') + '       - shutdown the daemon' + '\n' +\
    '  exq ' + misc.bold('kill') + '           - kill the daemon (' + misc.rtxt('emergency use') + ')' + '\n' +\
    '  exq ' + misc.bold('ping') + '           - ping the daemon' + '\n' +\
    ' ' + misc.undl('Exposure queue commands') + ':' + '\n' +\
    '  exq ' + misc.bold('image') + ' [tels] exptime filter bins [object] [imgtype]' + '\n' +\
    '  exq ' + misc.bold('multimage') + ' Nexp [tels] exptime filter bins [object] [imgtype]' + '\n' +\
    '  exq ' + misc.bold('dark') + '  [tels] exptime bins' + '\n' +\
    '  exq ' + misc.bold('multdark') + ' Nexp [tels] exptime bins' + '\n' +\
    '  exq ' + misc.bold('bias') + '  [tels] bins' + '\n' +\
    '  exq ' + misc.bold('multbias') + ' Nexp [tels] bins' + '\n' +\
    '  exq ' + misc.bold('pause') + '          - pause taking exposures' + '\n' +\
    '  exq ' + misc.bold('unpause') + '/' + misc.bold('resume') + ' - resumes taking exposures' + '\n' +\
    '  exq ' + misc.bold('list') + ' [v]' + '       - lists the current queue' + '\n' +\
    '  exq ' + misc.bold('clear') + '          - empty the queue' + '\n' +\
    '  exq ' + misc.bold('info') + ' [v]' + '       - report current status' + '\n' +\
    ' ' + misc.undl('Control commands') + ':' + '\n' +\
    '  exq ' + misc.bold('i') + '              - enter interactive mode' + '\n' +\
    '  exq ' + misc.bold('q') + '/' + misc.bold('exit') + '         - quit interactive mode' + '\n' +\
    '  exq ' + misc.bold('?') + '/' + misc.bold('help') + '         - print these instructions'
    print help_str
    
########################################################################
# Control System

if len(sys.argv) == 1:
    print_instructions()
else:
    EXQ_DAEMON_PROCESS = params.DAEMONS['exq']['PROCESS']
    EXQ_DAEMON_HOST = params.DAEMONS['exq']['HOST']
    EXQ_DAEMON_ADDRESS = params.DAEMONS['exq']['ADDRESS']
    EXQ_DAEMON_OUTPUT = params.LOG_PATH + 'exq_daemon-stdout.log'
    
    command = sys.argv[1:]
    if command[0] == 'i':
        interactive()
    else:
        query(command)
