#!/usr/bin/env python

########################################################################
#                               lilith.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#       G-TeCS script to provide overall control of the daemons        #
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


def start_daemon(daemon_key):
    DAEMON_PROCESS = params.DAEMONS[daemon_key]['PROCESS']
    DAEMON_HOST = params.DAEMONS[daemon_key]['HOST']
    if params.REDIRECT_STDOUT:
        DAEMON_OUTPUT = params.LOG_PATH + '{}_daemon-stdout.log'.format(daemon_key)
    else:
        DAEMON_OUTPUT = '/dev/stdout'
    misc.start_daemon(DAEMON_PROCESS, DAEMON_HOST, stdout=DAEMON_OUTPUT)


def shutdown_daemon(daemon_key):
    DAEMON_ADDRESS = params.DAEMONS[daemon_key]['ADDRESS']
    misc.shutdown_daemon(DAEMON_ADDRESS)


def kill_daemon(daemon_key):
    DAEMON_PROCESS = params.DAEMONS[daemon_key]['PROCESS']
    DAEMON_HOST = params.DAEMONS[daemon_key]['HOST']
    misc.kill_daemon(DAEMON_PROCESS, DAEMON_HOST)


def ping_daemon(daemon_key):
    DAEMON_ADDRESS = params.DAEMONS[daemon_key]['ADDRESS']
    return misc.ping_daemon(DAEMON_ADDRESS)

def daemon_is_alive(daemon_key):
    DAEMON_ADDRESS = params.DAEMONS[daemon_key]['ADDRESS']
    daemon = Pyro4.Proxy(DAEMON_ADDRESS)
    daemon._pyroTimeout = params.PROXY_TIMEOUT
    try:
        ping = daemon.ping()
        if ping == 'ping':
            return True
        else:
            return False
    except:
        return False

routines = dict(start=start_daemon, shutdown=shutdown_daemon,
                kill=kill_daemon, ping=ping_daemon)


if __name__ == '__main__':
    if sys.argv[1] in ['start', 'shutdown', 'kill', 'ping']:
        if len(sys.argv) > 2:
            daemons = sys.argv[2:]
        else:
            daemons = list(params.DAEMONS)

        # exq daemon needs a little careful handling.
        # it has to start after cam and filt daemon
        # and close before cam and filt daemon
        if 'exq' in daemons:
            if sys.argv[1] == 'start':
                # either cam daemon and filt daemon are running
                cam_up = daemon_is_alive('cam')
                filt_up = daemon_is_alive('filt')
                if (cam_up or 'cam' in daemons) and (filt_up or 'filt' in daemons):
                    # this will work. move exq to end
                    daemons.remove('exq')
                    daemons.append('exq')
                else:
                    # this won't work. raise Exception
                    raise ValueError('If exq daemon is to start, you must start cam and filt daemons first')
            elif sys.argv[1] == 'shutdown':
                    # stop exq first
                    daemons.remove('exq')
                    daemons.insert(0, 'exq')

        for d in daemons:
            print(d+':\t', end='')
            if d == 'exq' and sys.argv[1] == 'start':
                # give the cam and filt daemons a chance to start
                time.sleep(5)
            func = routines[sys.argv[1]]
            func(d)
    else:
        print('Valid commands: start, shutdown, kill, ping')
