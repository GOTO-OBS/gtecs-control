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
import subprocess
# TeCS modules
import X_params as params

########################################################################
## Command functions
def get_hostname():
    '''Get the hostname of this machine'''
    if os.environ.has_key('HOSTNAME'):
        return os.environ['HOSTNAME']
    else:
        tmp = commands.getoutput('hostname')
        return tmp.strip()

def get_process_ID(process_name, host):
    '''Retrieve ID numbers of python processes with specified name'''
    process_ID = []
    username = os.environ["LOGNAME"]

    if host == get_hostname():
        all_processes = commands.getoutput('ps j -w -u ' + username)
    else:
        all_processes = commands.getoutput('ssh ' + host + ' ps j -w -u ' + username)

    lines = all_processes.split('\n')
    pyflag = 0
    for line in lines:
        entries = line.split()
        for entry in entries:
            if entry == 'python' or entry == 'python2':
                pyflag = 2
            else:
                pyflag -= 1
            if pyflag == 1:
                n = len(process_name)
                if entry[-n:] == process_name:
                    process_ID.append(entries[1])
    return process_ID

def cmd_timeout(command, timeout, bufsize=-1):
    """
    Execute command and limit execution time to 'timeout' seconds.
    Found online and slightly modified
    """
    
    p = subprocess.Popen(command, bufsize=bufsize, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    start_time = time.time()
    seconds_passed = 0
    
    while p.poll() is None and seconds_passed < timeout:
        time.sleep(0.1)
        seconds_passed = time.time() - start_time
    
    if seconds_passed >= timeout:
        try:
            p.stdout.close()
            p.stderr.close()
            p.terminate()
            p.kill()
        except:
            pass
        out = None
    else:
        out = p.stdout.read().strip()
        err = p.stderr.read()
    returncode = p.returncode
    return out #(returncode, err, out)

def kill_processes(process, host):
    '''Kill any specified processes'''
    local_host = get_hostname()
    process_ID_list = get_process_ID(process, host)
    
    if local_host == host:
        for process_ID in process_ID_list:
            os.system('kill -9 ' + process_ID)
            print 'Killed process', process_ID
    else:
        for process_ID in process_ID_list:
            os.system('ssh ' + host + ' kill -9 ' + process_ID)

########################################################################
# Core Daemon functions 
def start_daemon(process, host, stdout='/dev/null'):
    '''Start a daemon (unless it is already running)'''
    local_host = get_hostname()
    process_ID = get_process_ID(process, host)
    if len(process_ID) == 0:
        if local_host == host:
            os.system('python2 ' + params.SCRIPT_PATH + process + ' >' + stdout + ' 2>&1 &')
            process_ID_n = get_process_ID(process, host)
            if len(process_ID_n) == 0:
                print 'ERROR: Daemon did not start, check logs'
            else:
                print 'Daemon running as process', process_ID_n[0]
        else:
            os.system('ssh ' + host + ' python2 ' + params.SCRIPT_PATH + process + ' >' + stdout + ' 2>&1 &')
    else:
        print 'ERROR: Daemon is already running as process', process_ID[0]

def ping_daemon(address):
    '''Ping a daemon'''
    daemon = Pyro4.Proxy(address)
    try:
        ping = daemon.ping()
        if ping == 'ping':
            print 'Daemon is alive at', address 
        else:
            print ping
    except:
        print 'ERROR: No response from daemon'

def shutdown_daemon(address):
    '''Shut a daemon down nicely'''
    daemon = Pyro4.Proxy(address)
    try:
        daemon.shutdown()
        print 'Daemon is shutting down'
        # Have to request status again to close loop
        daemon = Pyro4.Proxy(address)
        daemon.prod()
        daemon._pyroRelease()
    except:
        print 'ERROR: No response from daemon'

def kill_daemon(process, host):
    '''Kill a daemon (should be used as a last resort)'''
    local_host = get_hostname()
    process_ID_list = get_process_ID(process, host)

    if local_host == host:
        for process_ID in process_ID_list:
            os.system('kill -9 ' + process_ID)
            print 'Killed daemon at process', process_ID
    else:
        for process_ID in process_ID_list:
            os.system('ssh ' + host + ' kill -9 ' + process_ID)

def start_win(process, host, stdout='/dev/null'):
    os.system('ssh goto@'+host+' '+params.CYGWIN_PYTHON_PATH+' "'+params.WIN_PATH+process+' >'+stdout+' 2>&1 &"')
    
########################################################################
# Astronomy functions
def eq_to_hor(ha_hrs,dec_deg,lat_deg):
    # from starlink slalib  sla_DE2H
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













