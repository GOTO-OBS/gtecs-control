#!/usr/bin/env python

########################################################################
#                            dome_daemon.py                            #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#             G-TeCS daemon to control an AstroHaven dome              #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import os, sys
from math import *
import time, datetime
import Pyro4
import threading
# TeCS modules
from gtecs.tecs_modules import flags
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.controls import dome_control
from gtecs.tecs_modules.daemons import HardwareDaemon

########################################################################
# Dome daemon class

class DomeDaemon(HardwareDaemon):
    """
    Dome daemon class

    Contains x functions:
    - get_info()

    """

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'dome')

        ### command flags
        self.get_info_flag = 0
        self.open_flag = 0
        self.close_flag = 0
        self.halt_flag = 0

        ### dome variables
        self.info = {}
        self.dome_status = {'dome':'unknown', 'hatch':'unknown', 'estop':'unknown', 'monitorlink':'unknown'}

        self.count = 0
        self.last_hatch_status = None
        self.last_estop_status = None
        self.power_status = None
        self.dome_timeout = 40.

        self.move_side = 'none'
        self.move_frac = 1
        self.moving = 0
        self.moving_time = 0

        self.check_status_flag = 1
        self.status_check_time = 0
        self.status_check_period = 1

        self.check_warnings_flag = 1
        self.warnings_check_time = 0
        self.warnings_check_period = 3 #params.DOME_CHECK_PERIOD

        ### start control thread
        t = threading.Thread(target=self.dome_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def dome_control(self):
        self.logfile.info('Daemon control thread started')

        ### connect to dome object
        loc = params.DOME_LOCATION
        if params.FAKE_DOME == 1:
            dome = dome_control.FakeDome()
        else:
            dome = dome_control.AstroHavenDome(loc)

        while(self.running):
            self.time_check = time.time()

            # autocheck dome status every X seconds (if not already forced)
            delta = self.time_check - self.status_check_time
            if delta > self.status_check_period:
                self.check_status_flag = 1

            # check dome status
            if(self.check_status_flag):
                # get current dome status
                self.dome_status = dome.status
                if self.dome_status == None:
                    dome._check_status()
                    time.sleep(1)
                    self.dome_status = dome.status

                print(self.dome_status, self.moving, self.move_side,
                      self.open_flag, self.close_flag)

                self.status_check_time = time.time()
                self.check_status_flag = 0

            # autocheck warnings every Y seconds (if not already forced)
            delta = self.time_check - self.warnings_check_time
            if delta > self.warnings_check_period:
                self.check_warnings_flag = 1

            # check warnings
            if(self.check_warnings_flag):
                # WARNING 1: ON UPS POWER
                # ping the power sources
                #pinglist = ['power1', 'power2', 'power3', 'scope', 'video', 'reg']
                #self.power_status = misc.check_hosts(pinglist)
                #if self.power_status:
                #    self.logfile.info('No external power')
                #    os.system('touch ' + str(params.EMERGENCY_FILE))

                # WARNING 2: WEATHER
                # check any external flags
                condition_flags = flags.Conditions()
                override_flags = flags.Overrides()

                # WARNING 3: QUICK CLOSE BUTTON
                # loop through the button to see if it's triggered
                if params.QUICK_CLOSE_BUTTON:
                    if misc.loopback_test(params.QUICK_CLOSE_BUTTON_PORT,b'bob',chances=3):
                        self.logfile.info('Quick close button pressed')
                        os.system('touch %s' % params.EMERGENCY_FILE)

                # Act on an emergency
                if (self.dome_status['north'] != 'closed' or
                    self.dome_status['south'] != 'closed'):
                    if (condition_flags.summary > 0 and
                        override_flags.dome_auto != 1):
                        self.logfile.info('Conditions bad, auto-closing dome')
                        if not self.close_flag:
                            self.close_flag = 1
                            self.move_side = 'both'
                            self.move_frac = 1
                    elif os.path.isfile(params.EMERGENCY_FILE):
                        self.logfile.info('Closing dome (emergency!)')
                        if not self.close_flag:
                            self.close_flag = 1
                            self.move_side = 'both'
                            self.move_frac = 1

                self.warnings_check_time = time.time()
                self.check_warnings_flag = 0

            ### control functions
            # request info
            if(self.get_info_flag):
                info = {}
                for key in ['north','south','hatch']:
                    info[key] = self.dome_status[key]

                # general, backwards-compatible open/closed
                if ('open' in info['north']) or ('open' in info['south']):
                    info['dome'] = 'open'
                elif (info['north'] == 'closed') and (info['south'] == 'closed'):
                    info['dome'] = 'closed'
                else:
                    info['dome'] = 'ERROR'

                info['uptime'] = time.time() - self.start_time
                info['ping'] = time.time() - self.time_check
                now = datetime.datetime.utcnow()
                info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")
                if os.path.isfile(params.EMERGENCY_FILE):
                    info['emergency'] = 1
                else:
                    info['emergency'] = 0
                self.info = info
                self.get_info_flag = 0

            # open dome
            if(self.open_flag):
                # only open if allowed
                if override_flags.dome_auto <1 and condition_flags.summary > 0:
                    self.logfile.error('Conditions bad, dome will not open')
                #elif self.power_status:
                #    self.logfile.error('No external power, dome will not open')
                elif os.path.isfile(params.EMERGENCY_FILE):
                    self.logfile.error('In emergency locked state, dome will not open')

                # open both sides
                elif self.move_side == 'south':
                    side = 'south'
                elif self.move_side == 'north':
                    side = 'north'
                elif self.move_side == 'both':
                    side = 'south'
                elif self.move_side == 'none':
                    self.logfile.info('Dome open')
                    self.move_frac = 1
                    self.open_flag = 0
                    self.check_status_flag = 1
                    self.check_warnings_flag = 1

                if (self.dome_status[side] != 'full_open' and
                    self.open_flag and not self.moving):
                    try:
                        self.logfile.info('Opening {} side of dome'.format(side))
                        c = dome.open_full(side,self.move_frac)
                        if c: self.logfile.info(c)
                        self.moving = 1
                        self.moving_time = time.time()
                        self.check_status_flag = 1
                    except:
                        self.logfile.error('Failed to open dome')
                        self.logfile.debug('', exc_info=True)
                elif (self.moving and
                      self.dome_status[side] == 'full_open'):
                        self.logfile.info('The {} side is open'.format(side))
                        self.moving = 0
                        self.moving_time = 0
                        if self.move_side == 'both':
                            self.move_side = 'north'
                        else:
                            self.move_side = 'none'
                elif (self.moving and
                      self.dome_status[side] != 'full_open' and
                      self.move_frac != 1 and
                      dome.output_thread_running == 0):
                        self.logfile.info('The {} side moved requested fraction'.format(side))
                        self.moving = 0
                        self.moving_time = 0
                        if self.move_side == 'both':
                            self.move_side = 'north'
                        else:
                            self.move_side = 'none'
                        print(self.move_side)
                elif (self.moving and
                      time.time() - self.moving_time > self.dome_timeout and
                      self.dome_status[side] != 'full_open'):
                        self.logfile.info('Moving timed out')
                        self.moving = 0
                        self.move_side = 'none'
                        self.move_frac = 1
                        self.open_flag = 0
                        self.check_status_flag = 1
                        self.check_warnings_flag = 1

            # close dome
            if(self.close_flag):
                if self.move_side == 'south':
                    side = 'south'
                elif self.move_side == 'north':
                    side = 'north'
                elif self.move_side == 'both':
                    side = 'north'
                elif self.move_side == 'none':
                    self.logfile.info('Dome closed')
                    self.move_frac = 1
                    self.close_flag = 0
                    self.check_status_flag = 1
                    self.check_warnings_flag = 1

                if (self.dome_status[side] != 'closed' and
                    self.close_flag and not self.moving):
                    try:
                        self.logfile.info('Closing {} side of dome'.format(side))
                        c = dome.close_full(side, self.move_frac)
                        if c: self.logfile.info(c)
                        self.moving = 1
                        self.moving_time = time.time()
                        self.check_status_flag = 1
                    except:
                        self.logfile.error('Failed to close dome')
                        self.logfile.debug('', exc_info=True)
                elif (self.moving and
                      self.dome_status[side] == 'closed'):
                        self.logfile.info('The {} side is closed'.format(side))
                        self.moving = 0
                        self.moving_time = 0
                        if self.move_side == 'both':
                            self.move_side = 'south'
                        else:
                            self.move_side = 'none'
                        print(self.move_side)
                elif (self.moving and
                      self.dome_status[side] != 'closed' and
                      self.move_frac != 1 and
                      dome.output_thread_running == 0):
                        self.logfile.info('The {} side moved requested fraction'.format(side))
                        self.moving = 0
                        self.moving_time = 0
                        if self.move_side == 'both':
                            self.move_side = 'south'
                        else:
                            self.move_side = 'none'
                        print(self.move_side)
                elif (self.moving and
                      time.time() - self.moving_time > self.dome_timeout and
                      self.dome_status[side] != 'full_open'):
                        self.logfile.info('Moving timed out')
                        self.moving = 0
                        self.move_side = 'none'
                        self.move_frac = 1
                        self.close_flag = 0
                        self.check_status_flag = 1
                        self.check_warnings_flag = 1

            # halt dome motion
            if(self.halt_flag):
                try:
                    self.logfile.info('Halting dome')
                    c = dome.halt()
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('Failed to halt dome')
                    self.logfile.debug('', exc_info=True)
                # reset everything
                self.open_flag = 0
                self.close_flag = 0
                self.move_side = 'none'
                self.move_frac = 1
                self.moving = 0
                self.halt_flag = 0
                self.check_status_flag = 1

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Dome control functions
    def get_info(self):
        """Return dome status info"""
        self.check_status_flag = 1
        self.get_info_flag = 1
        time.sleep(0.1)
        return self.info

    def open_dome(self,side='both',frac=1):
        """Open the dome"""
        if flags.Overrides().dome_auto < 1 and flags.Conditions().summary > 0:
            return 'ERROR: Conditions bad, dome will not open'
        #elif self.power_status:
        #    return 'ERROR: No external power, dome will not open'
        elif os.path.isfile(params.EMERGENCY_FILE):
            return 'ERROR: In emergency locked state, dome will not open'
        else:
            north_status = self.dome_status['north']
            south_status = self.dome_status['south']
            if side == 'north' and north_status == 'full_open':
                return 'ERROR: The north side is already open'
            elif side == 'south' and south_status == 'full_open':
                return 'ERROR: The south side is already open'
            elif side == 'both':
                if north_status == 'full_open' and south_status != 'full_open':
                    side == 'south'
                elif north_status != 'full_open' and south_status == 'full_open':
                    side == 'north'
            self.open_flag = 1
            self.move_side = side
            self.move_frac = frac
            return 'Opening dome'

    def close_dome(self,side='both',frac=1):
        """Close the dome"""
        self.close_flag = 1
        self.move_side = side
        self.move_frac = frac
        return 'Closing dome'

    def halt_dome(self):
        """Stope the dome moving"""
        self.halt_flag = 1
        return 'Halting dome'

########################################################################

def start():
    '''
    Create Pyro server, register the daemon and enter request loop
    '''
    host = params.DAEMONS['dome']['HOST']
    port = params.DAEMONS['dome']['PORT']
    pyroID = params.DAEMONS['dome']['PYROID']

    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        dome_daemon = DomeDaemon()
        uri = pyro_daemon.register(dome_daemon, objectId=pyroID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        dome_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=dome_daemon.status_function)

    # Loop has closed
    dome_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
