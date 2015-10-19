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
import os, sys, commands
from string import split
import readline
import time
import Pyro4
# TeCS modules
import X_params as params
import X_misc as misc

########################################################################
# Mount control functions
def get_info():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.get_info()
        time.sleep(0.1) # Wait for it to update
        info = mnt.report_to_UI('info')
        
        if info['targ_ra'] != None and info['targ_dec'] != None:
            targ_dist = misc.ang_sep(info['tel_ra'],info['tel_dec'],info['targ_ra'],info['targ_dec'])
        else:
            targ_dist = None
        
        print '#### MOUNT INFO ####'
        if info['status'] != 'Slewing':
            print 'Status: %s' %info['status']
        else:
            print 'Status: %s (%.2f)' %(info['status'],targ_dist)
        print '~~~~~~~'
        print 'Telescope RA:  %.4f' %info['tel_ra']
        print 'Telescope Dec: %.4f' %info['tel_dec']
        if info['targ_ra']:
            print 'Target RA:     %.4f' %info['targ_ra']
        else:
            print 'Target RA:     NONE'
        if info['targ_dec']:
            print 'Target Dec:    %.4f' %info['targ_dec']
        else:
            print 'Target Dec:    NONE'
        if targ_dist != None:
            print 'Target dist:   %.4f' %targ_dist
        print 'Telescope Alt: %.2f' %info['tel_alt']
        print 'Telescope Az:  %.2f' %info['tel_az']
        print '~~~~~~~'
        print 'LST: %.2f' %info['lst']
        print 'Tel Time: %s' %info['teltime']
        print '~~~~~~~'
        print 'Site Long: %.2f' %info['long']
        print 'Site Lat: %.2f' %info['lat']
        print 'Site Eliv: %.2f' %info['eliv']
        print '####################'
        #print info
    except:
        print 'No response from mount daemon'
    
def pingS():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.pingS()
    except:
        print 'No response from mount daemon'
    
def start_tracking():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.start_tracking()
    except:
        print 'No response from mount daemon'
    
def full_stop():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.full_stop()
    except:
        print 'No response from mount daemon'
    
def park():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.park()
    except:
        print 'No response from mount daemon'
    
def unpark():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.unpark()
    except:
        print 'No response from mount daemon'
    
def ra(h,m,s):
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.ra(h,m,s)
    except:
        print 'No response from mount daemon'
    
def dec(sign,d,m,s):
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.dec(sign,d,m,s)
    except:
        print 'No response from mount daemon'
    
def slew(ra=None,dec=None):
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        slew = mnt.slew(ra,dec)
        if slew != None: print slew
    except:
        print 'No response from mount daemon'
        
def load_position():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.get_info()
        time.sleep(0.1)
        info = mnt.report_to_UI('info')
        tel_ra = info['tel_ra']
        tel_dec = info['tel_dec']
        mnt.ra(tel_ra,0,0)
        if tel_dec > 0:
            mnt.dec('+',tel_dec,0,0)
        else:
            mnt.dec('-',tel_dec,0,0)
    except:
        print 'No response from mount daemon'
    
def offset_n():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        offset = mnt.offset_dec('north')
        if offset != None: print offset
    except:
        print 'No response from mount daemon'
    
def offset_s():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        offset = mnt.offset_dec('south')
        if offset != None: print offset
    except:
        print 'No response from mount daemon'
    
def offset_e():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        offset = mnt.offset_ra('east')
        if offset != None: print offset
    except:
        print 'No response from mount daemon'
    
def offset_w():
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        offset = mnt.offset_ra('west')
        if offset != None: print offset
    except:
        print 'No response from mount daemon'
    
def set_step(offset):
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    try:
        mnt.set_step(offset)
    except:
        print 'No response from mount daemon'

########################################################################
# Define interactive mode
def interactive():
    while 1:
        command=split(raw_input('mnt> '))
        if(len(command)>0):
            if command[0]=='q':
                return
            else:
                query(command)

def query(command):
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control functions
    if command[0]=='start':
        misc.startDaemon(MNT_DAEMON_PROCESS,MNT_DAEMON_HOST,stdout=MNT_DAEMON_OUTPUT)
    elif command[0]=='pingD': #'ping':
        misc.pingDaemon(MNT_DAEMON_ADDRESS)
    elif command[0]=='shutdown':
        misc.shutdownDaemon(MNT_DAEMON_ADDRESS)
    elif command[0]=='kill':
        misc.killDaemon(MNT_DAEMON_PROCESS,MNT_DAEMON_HOST)
    elif command[0]=='help':
        printInstructions()
    elif command[0]=='i':
        print 'Already in interactive mode'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount control functions
    elif command[0]=='info':
        get_info()
    elif command[0]=='pingS':
        pingS()
    elif command[0]=='track':
        start_tracking()
    elif command[0]=='stop':
        full_stop()
    elif command[0]=='park':
        park()
    elif command[0]=='unpark':
        unpark()
    elif command[0]=='ra':
        ra(float(command[1]),float(command[2]),float(command[3]))
    elif command[0]=='dec':
        if len(command) == 5:
            dec(command[1],float(command[2]),float(command[3]),float(command[4]))
        else:
            print 'ERROR: You probably forgot the sign!'
    elif command[0]=='slew':
        slew()
    elif command[0]=='load':
        load_position()
    elif command[0]=='slewR':
        slew(ra=10.15, dec=11.88)
    elif command[0]=='slewP':
        slew(ra=02.88, dec=89.31)
    elif command[0]=='n':
        offset_n()
    elif command[0]=='s':
        offset_s()
    elif command[0]=='e':
        offset_e()
    elif command[0]=='w':
        offset_w()
    elif command[0]=='step':
        set_step(float(command[1]))

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Unrecognized function
    elif command[0]=='exit':
        print 'mnt> Use "shutdown" to close the daemon or "q" to quit interactive mode'
    elif command[0]=='ping':
        print 'mnt> [pingD] to ping mnt daemon, [pingS] to ping SiTech server'
    else:
        print 'mnt> Command not recognized:',command[0]

def printInstructions():
    print 'Usage: mnt start              - starts the mount daemon'
    print '       mnt shutdown           - shuts down the mount daemon cleanly'
    print '       mnt kill               - kills the mount daemon (emergency use only!)'
    print '       mnt pingD              - pings the mount daemon'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       mnt info               - reports current mount data'
    print '       mnt pingS              - pings the SiTech daemon'
    print '       mnt ra [h m s]         - set target ra'
    print '       mnt dec [sign d m s]   - set target dec'
    print '       mnt slew               - slew to target ra/dec'
    print '       mnt load               - load current position as target'
    print '       mnt slewR              - slew to Regulus (Alpha Leo)'
    print '       mnt slewP              - slew to Polaris (Alpha UMi)'
    print '       mnt n                  - offset telescope north by one step'
    print '       mnt s                  - offset telescope south by one step'
    print '       mnt e                  - offset telescope east by one step'
    print '       mnt w                  - offset telescope west by one step'
    print '       mnt step [value]       - set offset step size (arcsec, defult=10)'
    print '       mnt track              - start tracking'
    print '       mnt stop               - stop tracking/slewing'
    print '       mnt park               - park scope'
    print '       mnt unpark             - leave park state'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       mnt i                  - enter interactive (command line) usage'
    print '       mnt q                  - quit interactive (command line) usage'
    print '       ~~~~~~~~~~~~~~~~~~~~~~~~'
    print '       mnt help               - prints these instructions'

########################################################################
# Control System

if len(sys.argv)==1:
    printInstructions()
else:
    MNT_DAEMON_PROCESS=params.DAEMONS['mnt']['PROCESS']
    MNT_DAEMON_HOST=params.DAEMONS['mnt']['HOST']
    MNT_DAEMON_ADDRESS=params.DAEMONS['mnt']['ADDRESS']
    MNT_DAEMON_OUTPUT=params.LOG_PATH+'mnt_daemon-stdout.log'
    
    command=sys.argv[1:]    

    if command[0]=='i':
        interactive()
    else:
        query(command)
