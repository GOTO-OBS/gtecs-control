#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                                misc.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#   G-TeCS module containing common functions used by TeCS processes   #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
import os, sys, commands
from math import *
import time
import Pyro4
# TeCS modules
import X_params as params

########################################################################
## Command functions
def getHostname():
    '''Get the hostname of this machine - should work from within a cron job'''
    if os.environ.has_key('HOSTNAME'):
        return os.environ['HOSTNAME']
    else:
        tmp = commands.getoutput('hostname')
        return tmp.strip()

def getProcessID(process_name, node):
    '''Retrieve ID numbers of python processes with specified name'''
    processID=[]
    username = os.environ["LOGNAME"]  #is this reliable if caled from within a cron job?

    if node == getHostname():
        all_processes = commands.getoutput('ps j -w -u '+username)
    else:
        all_processes = commands.getoutput('ssh '+node+' ps j -w -u '+username)
    #print all_processes
    lines = all_processes.split('\n')
    pyflag = 0
    for line in lines:
        entries=line.split()
        for entry in entries:
            if entry == 'python2' or entry == 'python':
                pyflag = 2
            else:
                pyflag -= 1
            if pyflag == 1:
                n = len(process_name)
                if entry[-n:] == process_name:
                    processID.append(entries[1])
    return processID

########################################################################
# Core Daemon functions 
def startDaemon(daemonProcess,daemonHost,stdout='/dev/null'):
    '''Start a daemon (unless it is running already)'''
    localHost = getHostname()
    processID = getProcessID(daemonProcess,daemonHost)
    if len(processID) == 0:
        if localHost == daemonHost:
            os.system('python2 '+params.SCRIPT_PATH+daemonProcess+' >'+stdout+' 2>&1 &')
            processIDn = getProcessID(daemonProcess,daemonHost)
            if len(processIDn) == 0:
                'Error starting daemon, check logs'
            else:
                print 'Daemon running: process', processIDn[0]
        else:
            os.system('ssh '+daemonHost+' python /home/slodar/scripts/'+daemonProcess+' >'+stdout+' 2>&1 &')
    else:
        print 'Aborted. Daemon is already running: process', processID[0]

def pingDaemon(daemonAddress):
    '''Ping a daemon'''
    daemon = Pyro4.Proxy(daemonAddress)
    try:
        ping = daemon.ping()
        if ping == 'ping':
            print 'Daemon is alive at', daemonAddress 
        else:
            print ping
    except:
        print 'No response from daemon'

def shutdownDaemon(daemonAddress):
    '''Shut a daemon down nicely'''
    daemon = Pyro4.Proxy(daemonAddress)
    try:
        daemon.shutdown()
        print 'Daemon is shutting down'
        # Have to request status again to close loop
        daemon = Pyro4.Proxy(daemonAddress)
        daemon.prod()
        daemon._pyroRelease()
    except:
        print 'No response from daemon'

def killDaemon(daemonProcess,daemonHost):
    '''Kill a specified daemon (should be used as a last resort)'''
    localHost = getHostname()
    username = os.environ["LOGNAME"]
    processID_list=[]

    if localHost == daemonHost:
        processes = commands.getoutput('ps j -w -u '+username)
    else:
        processes = commands.getoutput('ssh '+daemHostname+' ps j -w -u '+username)
    
    lines = processes.split('\n')
    pyflag = 0
    for line in lines:
        entries = line.split()
        for entry in entries:
            if entry == 'python':
                pyflag = 2
            else:
                pyflag -= 1
            if pyflag == 1:
                n = len(daemonProcess)
                if entry[-n:] == daemonProcess:
                    processID_list.append(entries[1])

    if localHost == daemonHost:
        for processID in processID_list:
            os.system('kill -9 '+processID)
            print 'Daemon killed: process', processID
    else:
        for processID in processID_list:
            os.system('ssh '+daemonHost+' kill -9 '+processID)

########################################################################
# Astronomy functions
def eq_to_hor(ha_hrs,dec_deg,lat_deg):    # from starlink slalib  sla_DE2H

    ha = ha_hrs*15.*2.*pi/360.
    dec= dec_deg*2.*pi/360.
    phi= lat_deg*2.*pi/360.
    sh = sin(ha)
    ch = cos(ha)
    sd = sin(dec)
    cd = cos(dec)
    sp = sin(phi)
    cp = cos(phi)
    x  = -1.*ch*cd*sp + sd*cp
    y  = -1.*sh*cd
    z  = ch*cd*cp + sd*sp
    r  = sqrt(x*x+y*y)
    if r == 0.0:
       a = 0.0
    else:
       a = atan2(y,x)
    if a < 0.0: a = a + 2.*pi
    az_rad = a
    el_rad = atan2(z,r)
    az = az_rad*360./(2.*pi)
    el = el_rad*360./(2.*pi)

    return el,az

def find_ha(ra_hrs,lst):
    ha_hrs = lst - ra_hrs
    return ha_hrs

def find_lst(tel_long_deg,ut): #Local sidereal time
    gst = find_gst(ut)
    lst_hour = gst + tel_long_deg/15.
    if lst_hour  > 24.0:
        lst_hour  = lst_hour  - 24.0
    if lst_hour  < 0.0:
        lst_hour  = lst_hour  + 24.0
    return lst_hour

def find_gst(ut): #Greenwich sidereal time
    (year,month,mday,hours,mins,sec,week,jd,d)=time.gmtime(ut)
    ut_hours = hours + (mins + sec/60.)/60.
    mday = mday + ut_hours/24.
    if  month <= 2 :
        year = year - 1
        month = month + 12
    a = int(year/100.)
    b = 2. - a + int(a/4.)
    if year < 0 :
        c = int((365.2500*year)-0.7500)
    else:
        c = int(365.2500*year)

    d = int(30.600100*(month+1))
    jd = b + c + d + int(mday) + 1720994.500

    s = jd - 2451545.000
    t = s/36525.000
    t0 = 6.697374558 + (2400.051336*t) + (0.000025862*(t*t));
    t0 = (t0 - int(t0/24.)*24)
    if t0 < 0.0: t0 = t0 + 24.
    ut = 1.002737909*ut_hours
    tmp = int((ut + t0)/24.)
    gst = ut + t0 - tmp*24.
    return gst

def check_alt_limit(targ_ra,targ_dec,lst):
    targ_ha = find_ha(targ_ra,lst)
    targ_alt,targ_az = eq_to_hor(targ_ha,targ_dec,params.SITE_LATITUDE)
    if targ_alt < params.MIN_ELEVATION:
        return 1
    else:        
        return 0
        
def ang_sep(ra_1,dec_1,ra_2,dec_2):
    alt_1 = 90-dec_1
    alt_2 = 90-dec_2
    ra_dif = (ra_1-ra_2)*360./24.
    c1 = cos(radians(alt_1))
    c2 = cos(radians(alt_2))
    s1 = sin(radians(alt_1))
    s2 = sin(radians(alt_2))
    cR = cos(radians(ra_dif))
    cS = c1*c2 + s1*s2*cR
    S = degrees(acos(cS))
    return S













