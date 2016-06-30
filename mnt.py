#!/usr/bin/env python

########################################################################
#                                mnt.py                                #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS script to provide control over mnt_daemon           #
#                     Martin Dyer, Sheffield, 2015                     #
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
# Mount control functions
def get_info():
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = mnt.get_info()
        print('####### MOUNT INFO ########')
        if info['status'] != 'Slewing':
            print('Status: %s' %info['status'])
        else:
            print('Status: %s (%.2f)' %(info['status'],info['target_dist']))
        print('~~~~~~~')
        print('Mount Alt:        %.2f' %info['mount_alt'])
        print('Mount Az:         %.2f' %info['mount_az'])
        print('Telescope RA:     %.4f' %info['mount_ra'])
        print('Telescope Dec:    %.4f' %info['mount_dec'])
        if info['target_ra'] != None:
            print('Target RA:        %.4f' %info['target_ra'])
        else:
            print('Target RA:        TARGET NOT SET')
        if info['target_dec'] != None:
            print('Target Dec:       %.4f' %info['target_dec'])
        else:
            print('Target Dec:       TARGET NOT SET')
        if info['target_dist'] != None:
            print('Target distance:  %.3f' %info['target_dist'])
        print('Offset step size: %.2f arcsec' %info['step'])
        print('~~~~~~~')
        print('LST:              %.2f' %info['lst'])
        print('Hour Angle:       %.2f' %info['ha'])
        print('UTC:              %s' %info['utc'])
        print('~~~~~~~')
        print('Uptime: %.1fs' %info['uptime'])
        print('Ping: %.5fs' %info['ping'])
        print('Timestamp: %s' %info['timestamp'])
        print('###########################')
    except:
        print(misc.ERROR('No response from mount daemon'))

def slew_to_radec(ra,dec):
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = mnt.slew_to_radec(ra,dec)
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def slew_to_target():
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = mnt.slew_to_target()
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def start_tracking():
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = mnt.start_tracking()
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def full_stop():
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = mnt.full_stop()
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def park():
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = mnt.park()
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def unpark():
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = mnt.unpark()
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def set_target_ra(h,m,s):
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    ra = h + m/60. + s/3600.
    try:
        c = mnt.set_target_ra(ra)
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def set_target_dec(sign,d,m,s):
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    if sign == '+':
        dec = d + m/60. + s/3600.
    else:
        dec = -1*(d + m/60. + s/3600.)
    try:
        c = mnt.set_target_dec(dec)
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def offset(direction):
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = mnt.offset(direction)
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

def set_step(offset):
    mnt = Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        c = mnt.set_step(offset)
        if c: print(c)
    except:
        print(misc.ERROR('No response from mount daemon'))

########################################################################
# Interactive mode
def interactive():
    while True:
        command = input('mnt> ').split()
        if len(command) > 0:
            if command[0] == 'q' or command[0] == 'exit':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0] == 'start':
        misc.start_daemon(MNT_DAEMON_PROCESS, MNT_DAEMON_HOST, stdout=MNT_DAEMON_OUTPUT)
    elif command[0] == 'shutdown':
        misc.shutdown_daemon(MNT_DAEMON_ADDRESS)
    elif command[0] == 'kill':
        misc.kill_daemon(MNT_DAEMON_PROCESS, MNT_DAEMON_HOST)
    elif command[0] == 'ping':
        misc.ping_daemon(MNT_DAEMON_ADDRESS)

    elif command[0] == 'startS':
        misc.start_win(SITECH_PROCESS, SITECH_HOST, stdout=SITECH_OUTPUT)
    elif command[0] == 'shutdownS':
        misc.shutdown_daemon(SITECH_ADDRESS)
    elif command[0] == 'pingS':
        misc.ping_daemon(SITECH_ADDRESS)

    elif command[0] == 'help' or command[0] == '?':
        print_instructions()
    elif command[0] == 'i':
        print(misc.ERROR('Already in interactive mode'))

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount control functions
    elif command[0]=='info':
        get_info()
    elif command[0]=='slew':
        slew_to_target()
    elif command[0]=='track':
        start_tracking()
    elif command[0]=='stop':
        full_stop()
    elif command[0]=='park':
        park()
    elif command[0]=='unpark':
        unpark()
    elif command[0]=='ra':
        set_target_ra(float(command[1]),float(command[2]),float(command[3]))
    elif command[0]=='dec':
        if len(command) == 5:
            set_target_dec(command[1],float(command[2]),float(command[3]),float(command[4]))
        else:
            print('ERROR: You probably forgot the sign!')
    elif command[0]=='n':
        offset('north')
    elif command[0]=='s':
        offset('south')
    elif command[0]=='e':
        offset('east')
    elif command[0]=='w':
        offset('west')
    elif command[0]=='step':
        set_step(float(command[1]))
    elif command[0]=='slewR':
        slew_to_radec(ra=10.15, dec=11.88)
    elif command[0]=='slewP':
        slew_to_radec(ra=02.88, dec=89.31)

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    else:
        print(misc.ERROR('Unrecognized command "%s"' %command[0]))

def print_instructions():
    help_str = misc.bold('Usage:') + ' mnt [command]' + '\n' +\
    ' ' + misc.undl('Daemon commands') + ':' + '\n' +\
    '  mnt ' + misc.bold('start') + '          - start the daemon' + '\n' +\
    '  mnt ' + misc.bold('shutdown') + '       - shutdown the daemon' + '\n' +\
    '  mnt ' + misc.bold('kill') + '           - kill the daemon (' + misc.rtxt('emergency use') + ')' + '\n' +\
    '  mnt ' + misc.bold('ping') + '           - ping the daemon' + '\n' +\
    ' ' + misc.undl('SiTech interface commands') + ':' + '\n' +\
    '  mnt ' + misc.bold('startS') + '         - start the daemon' + '\n' +\
    '  mnt ' + misc.bold('shutdownS') + '      - shutdown the daemon' + '\n' +\
    '  mnt ' + misc.bold('pingS') + '          - ping the daemon' + '\n' +\
    ' ' + misc.undl('Mount commands') + ':' + '\n' +\
    '  mnt ' + misc.bold('ra') + ' h m s' + '       - set target ra' + '\n' +\
    '  mnt ' + misc.bold('dec') + ' sign d m s' + ' - set target dec' + '\n' +\
    '  mnt ' + misc.bold('slew') + '           - slew to target ra/dec' + '\n' +\
    misc.ytxt('  mnt ' + misc.bold('slewR')) + '          - slew to Regulus (' + misc.ytxt('TEST') + ')' + '\n' +\
    misc.ytxt('  mnt ' + misc.bold('slewP')) + '          - slew to Polaris (' + misc.ytxt('TEST') + ')' + '\n' +\
    '  mnt ' + misc.bold('track') + '          - start tracking' + '\n' +\
    '  mnt ' + misc.bold('stop') + '           - stop moving (tracking/slewing)' + '\n' +\
    '  mnt ' + misc.bold('park') + '           - enter park state' + '\n' +\
    '  mnt ' + misc.bold('unpark') + '         - leave park state' + '\n' +\
    '  mnt ' + misc.bold('n') + '/' + misc.bold('s') + '/' + misc.bold('e') + '/' + misc.bold('w') + '        - offset in direction by one step' + '\n' +\
    '  mnt ' + misc.bold('step') + ' size' + '      - set offset step size (arcsec, default=10)' + '\n' +\
    '  mnt ' + misc.bold('info') + ' [v]' + '       - report current status' + '\n' +\
    ' ' + misc.undl('Control commands') + ':' + '\n' +\
    '  mnt ' + misc.bold('i') + '              - enter interactive mode' + '\n' +\
    '  mnt ' + misc.bold('q') + '/' + misc.bold('exit') + '         - quit interactive mode' + '\n' +\
    '  mnt ' + misc.bold('?') + '/' + misc.bold('help') + '         - print these instructions'
    print(help_str)


########################################################################
# Control System

if len(sys.argv) == 1:
    print_instructions()
else:
    MNT_DAEMON_PROCESS = params.DAEMONS['mnt']['PROCESS']
    MNT_DAEMON_HOST = params.DAEMONS['mnt']['HOST']
    MNT_DAEMON_ADDRESS = params.DAEMONS['mnt']['ADDRESS']
    if params.REDIRECT_STDOUT:
        MNT_DAEMON_OUTPUT = params.LOG_PATH + 'mnt_daemon-stdout.log'
    else:
        MNT_DAEMON_OUTPUT = '/dev/stdout'

    SITECH_PROCESS = params.SITECH_PROCESS
    SITECH_HOST = params.WIN_HOST
    SITECH_ADDRESS = params.SITECH_ADDRESS
    # don't redirect on windows
    SITECH_OUTPUT = params.CYGWIN_PATH + 'sitech-stdout.log'

    command=sys.argv[1:]
    if command[0] == 'i':
        interactive()
    else:
        query(command)
