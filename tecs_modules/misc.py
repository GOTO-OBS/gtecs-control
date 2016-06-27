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
from __future__ import absolute_import
from __future__ import print_function
import os, sys
import six
if six.PY2:
    from commands import getoutput
else:
    from subprocess import getoutput
from math import *
import time
import Pyro4
import subprocess
import serial
import re
import smtplib
# TeCS modules
from . import params
from six.moves import range

########################################################################
## Command functions
def get_hostname():
    '''Get the hostname of this machine'''
    if 'HOSTNAME' in os.environ:
        return os.environ['HOSTNAME']
    else:
        tmp = getoutput('hostname')
        return tmp.strip()

def get_process_ID(process_name, host):
    '''Retrieve ID numbers of python processes with specified name'''
    process_ID = []
    username = os.environ["LOGNAME"]

    if host == get_hostname():
        all_processes = getoutput('ps -jwu %s | grep -i python' % username)
    else:
        all_processes = getoutput('ssh ' + host + ' ps -jwu %s | grep -i python' % username)

    for line in all_processes.split('\n'):
        if line.endswith(process_name):
            process_ID.append(line.split()[1])

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
            print('Killed process', process_ID)
    else:
        for process_ID in process_ID_list:
            os.system('ssh ' + host + ' kill -9 ' + process_ID)

def python_command(filename, command):
    '''Send a command to a control script as if using the terminal'''
    command_string = 'python ' + filename + ' ' + command
    proc = subprocess.Popen(command_string, shell=True, stdout=subprocess.PIPE)
    output = proc.communicate()[0]
    return output

def ping_host(hostname,count=1,ttl=1):
    '''Ping a network address and return the number of responses'''
    ping = getoutput('ping -q -t ' + str(int(ttl)) + ' -c ' + str(count) + ' ' + hostname)

    out = ping.split('\n')
    packets_received = 0
    for line in range(len(out)):
        if 'ping statistics' in out[line]:
            stats_line = out[line + 1].split()
            packets_received = int(stats_line[3])
            break
    return packets_received

def check_hosts(hostlist):
    '''Ping list of hosts until one responds or the list is exhausted'''
    for hostname in hostlist:
        if ping_host(hostname) > 0:
            return 0 # success
    return 1 # failure

def loopback_test(serialport='/dev/ttyS3', message='bob', chances=3):
    '''Send a message to a serial port and try to read it back'''
    s = serial.Serial(serialport, 9600, parity='N', bytesize=8, stopbits=1, rtscts=0, xonxoff=1, timeout=1)
    for i in range(chances):
        s.write(message + '\n')
        reply = s.readlines()
        for x in reply:
            if x.find(message) >= 0:
                s.close()
                return 0   # success
    s.close()
    return 1   # failure

########################################################################
# Core Daemon functions
def start_daemon(process, host, stdout='/dev/null'):
    '''Start a daemon (unless it is already running)'''
    local_host = get_hostname()
    process_ID = get_process_ID(process, host)
    if len(process_ID) == 0:
        if local_host == host:
            cmd = ' '.join((sys.executable, params.SCRIPT_PATH+process,
                            '>', stdout, '2>&1 &'))
            os.system(cmd)
            process_ID_n = get_process_ID(process, host)
            if len(process_ID_n) == 0:
                print('ERROR: Daemon did not start, check logs')
            else:
                print('Daemon running as process', process_ID_n[0])
        else:
            os.system('ssh ' + host + ' python2 ' + params.SCRIPT_PATH + process + ' >' + stdout + ' 2>&1 &')
    else:
        print('ERROR: Daemon is already running as process', process_ID[0])

def ping_daemon(address):
    '''Ping a daemon'''
    daemon = Pyro4.Proxy(address)
    daemon._pyroTimeout = params.PROXY_TIMEOUT
    try:
        ping = daemon.ping()
        if ping == 'ping':
            print('Daemon is alive at', address)
        else:
            print(ping)
    except:
        print('ERROR: No response from daemon')

def shutdown_daemon(address):
    '''Shut a daemon down nicely'''
    daemon = Pyro4.Proxy(address)
    daemon._pyroTimeout = params.PROXY_TIMEOUT
    try:
        daemon.shutdown()
        print('Daemon is shutting down')
        # Have to request status again to close loop
        daemon = Pyro4.Proxy(address)
        daemon._pyroTimeout = params.PROXY_TIMEOUT
        daemon.prod()
        daemon._pyroRelease()
    except:
        print('ERROR: No response from daemon')

def kill_daemon(process, host):
    '''Kill a daemon (should be used as a last resort)'''
    local_host = get_hostname()
    process_ID_list = get_process_ID(process, host)

    if local_host == host:
        for process_ID in process_ID_list:
            os.system('kill -9 ' + process_ID)
            print('Killed daemon at process', process_ID)
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

########################################################################
## Text formatting functions
def rtxt(text):
    return '\033[91m' + str(text) + '\033[0m'
def gtxt(text):
    return '\033[92m' + str(text) + '\033[0m'
def ytxt(text):
    return '\033[93m' + str(text) + '\033[0m'
def btxt(text):
    return '\033[94m' + str(text) + '\033[0m'
def ptxt(text):
    return '\033[95m' + str(text) + '\033[0m'
def bold(text):
    return '\033[1m' + str(text) + '\033[0m'
def undl(text):
    return '\033[4m' + str(text) + '\033[0m'

########################################################################
# Misc functions
def ERROR(message):
    return rtxt(bold('ERROR')) + ': ' + str(message)

def adz(num):
    num = repr(num)
    if len(num) == 1:
        num = '0' + num
    return num

def valid_ints(array, allowed):
    valid = []
    for i in array:
        if i == '':
            pass
        elif not i.isdigit():
            print('ERROR: "' + str(i) + '" is invalid, must be in',allowed)
        elif i not in [str(x) for x in list(params.TEL_DICT.keys())]:
            print('ERROR: "' + str(i) + '" is invalid, must be in',allowed)
        elif int(i) not in valid:
            valid += [int(i)]
    valid.sort()
    return valid

def is_num(value):
    try:
        float(value)
        return True
    except ValueError:
        return False

def remove_html_tags(data):
    p = re.compile(r'<.*?>')
    return p.sub('', data)

def send_email(recipients=params.EMAIL_LIST, subject='GOTO', message='Test'):
    to_address = ', '.join(recipients)
    from_address = params.EMAIL_ADDRESS
    header = 'To:%s\nFrom:%s\nSubject:%s\n' % (to_address,from_address,subject)
    timestamp = time.strftime('%Y-%m-%dT%H:%M:%S',time.gmtime())
    text = '%s\n\nMessage sent at %s' % (message,timestamp)

    server = smtplib.SMTP(EMAIL_SERVER)
    server.starttls()
    server.login('goto-observatory@gmail.com', 'password')
    server.sendmail(fromaddr, recipients, header + '\n' + text + '\n\n')
    server.quit()
    print('Sent mail to',recipients)
