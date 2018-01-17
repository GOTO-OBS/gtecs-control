#!/usr/bin/env python
"""
Daemon to control an AstroHaven dome
"""

import os
import sys
import time
import datetime
from math import *
import Pyro4
import threading

from gtecs import flags
from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs.slack import send_slack_msg
from gtecs.controls import dome_control
from gtecs.daemons import HardwareDaemon


DAEMON_ID = 'dome'
DAEMON_HOST = params.DAEMONS[DAEMON_ID]['HOST']
DAEMON_PORT = params.DAEMONS[DAEMON_ID]['PORT']


class DomeDaemon(HardwareDaemon):
    """Dome hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        self.daemon_id = DAEMON_ID
        HardwareDaemon.__init__(self, self.daemon_id)

        ### command flags
        self.get_info_flag = 0
        self.open_flag = 0
        self.close_flag = 0
        self.halt_flag = 0
        self.override_dehumid_flag = 0

        ### dome variables
        self.info = None
        self.dome_status = {'dome':'unknown', 'hatch':'unknown', 'estop':'unknown', 'monitorlink':'unknown'}

        self.count = 0
        self.last_hatch_status = None
        self.last_estop_status = None
        self.power_status = None
        self.dome_timeout = 40.

        self.move_side = 'none'
        self.move_frac = 1
        self.move_started = 0
        self.move_start_time = 0

        self.dehumid_command = 'none'

        self.check_status_flag = 1
        self.status_check_time = 0
        self.status_check_period = 1

        self.check_warnings_flag = 1
        self.warnings_check_time = 0
        self.warnings_check_period = 3 #params.DOME_CHECK_PERIOD

        self.check_conditions_flag = 1
        self.conditions_check_time = 0
        self.conditions_check_period = 60

        self.dependency_error = 0
        self.dependency_check_time = 0

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()


    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

        ### connect to dome object
        loc = params.DOME_LOCATION
        if params.FAKE_DOME == 1:
            dome = dome_control.FakeDome()
        else:
            dome = dome_control.AstroHavenDome(loc)

        ### connect to dehumidifier object
        ip = params.DEHUMIDIFIER_IP
        port = params.DEHUMIDIFIER_PORT
        if params.FAKE_DOME == 1:
            dehumidifier = dome_control.FakeDehumidifier()
        else:
            dehumidifier = dome_control.Dehumidifier(ip, port)

        while(self.running):
            self.time_check = time.time()

            ### check dependencies
            if (self.time_check - self.dependency_check_time) > 2:
                if not misc.dependencies_are_alive(self.daemon_id):
                    if not self.dependency_error:
                        self.logfile.error('Dependencies are not responding')
                        self.dependency_error = 1
                else:
                    if self.dependency_error:
                        self.logfile.info('Dependencies responding again')
                        self.dependency_error = 0
                self.dependency_check_time = time.time()

            if self.dependency_error:
                time.sleep(5)
                continue

            # autocheck dome status every X seconds (if not already forced)
            delta = self.time_check - self.status_check_time
            if delta > self.status_check_period:
                self.check_status_flag = 1

            # check dome status
            if self.check_status_flag:
                try:
                    # get current dome status
                    self.dome_status = dome.status
                    if self.dome_status == None:
                        dome._check_status()
                        time.sleep(1)
                        self.dome_status = dome.status

                    print(self.dome_status, self.move_started, self.move_side,
                          self.open_flag, self.close_flag)

                    self.status_check_time = time.time()
                except:
                    self.logfile.error('check_status command failed')
                    self.logfile.debug('', exc_info=True)
                self.check_status_flag = 0

            # autocheck warnings every Y seconds (if not already forced)
            delta = self.time_check - self.warnings_check_time
            if delta > self.warnings_check_period:
                self.check_warnings_flag = 1

            # check warnings
            if self.check_warnings_flag:
                try:
                    # Get external flags
                    conditions = flags.Conditions()
                    overrides = flags.Overrides()

                    # Loop through QC button to see if it's triggered
                    button_pressed = False
                    if params.QUICK_CLOSE_BUTTON:
                        port = params.QUICK_CLOSE_BUTTON_PORT
                        button_pressed = misc.loopback_test(port)

                    # Create emergency file if needed
                    if button_pressed:
                        self.logfile.info('Quick close button pressed!')
                        os.system('touch {}'.format(params.EMERGENCY_FILE))
                    if conditions.critical:
                        self.logfile.info('Conditions critical!')
                        os.system('touch {}'.format(params.EMERGENCY_FILE))

                    # Act on an emergency
                    if (self.dome_status['north'] != 'closed' or
                        self.dome_status['south'] != 'closed'):
                        if os.path.isfile(params.EMERGENCY_FILE):
                            self.logfile.info('Closing dome (emergency!)')
                            if not self.close_flag:
                                send_slack_msg('dome_daemon is closing dome (emergency shutdown)')
                                self.close_flag = 1
                                self.move_side = 'both'
                                self.move_frac = 1
                        elif (conditions.bad and not overrides.autoclose):
                            self.logfile.info('Conditions bad, auto-closing dome')
                            if not self.close_flag:
                                self.close_flag = 1
                                self.move_side = 'both'
                                self.move_frac = 1

                    self.warnings_check_time = time.time()
                except:
                    self.logfile.error('check_warnings command failed')
                    self.logfile.debug('', exc_info=True)
                self.check_warnings_flag = 0

            # autocheck dome conditions every Z seconds (if not already forced)
            delta = self.time_check - self.conditions_check_time
            if delta > self.conditions_check_period:
                self.check_conditions_flag = 1

            # check dome internal conditions
            if self.check_conditions_flag:
                try:
                    # get current dome conditions
                    conditions = dehumidifier.conditions()
                    print(conditions, dehumidifier.status())
                    humidity = conditions['humidity']
                    temperature = conditions['temperature']

                    currently_open = (self.dome_status['north'] != 'closed' or
                                      self.dome_status['south'] != 'closed')

                    if dehumidifier.status() == '0' and not currently_open:
                        if humidity > params.MAX_INTERNAL_HUMIDITY:
                            string = 'Internal humidity {}% is above {}%'
                            string = string.format(humidity, params.MAX_INTERNAL_HUMIDITY)
                            self.logfile.info(string)
                        if temperature < params.MIN_INTERNAL_TEMPERATURE:
                            string = 'Internal temperature {}C is below {}C'
                            string = string.format(temperature, params.MIN_INTERNAL_TEMPERATURE)
                            self.logfile.info(string)
                        if (humidity > params.MAX_INTERNAL_HUMIDITY or
                            temperature < params.MIN_INTERNAL_TEMPERATURE):
                            self.logfile.info('Turning on dehumidifier')
                            dehumidifier.on()
                        elif self.override_dehumid_flag and self.dehumid_command == 'on':
                            self.logfile.info('Turning on dehumidifier (manual)')
                            dehumidifier.on()
                            self.override_dehumid_flag = 0
                            self.dehumid_command = 'none'

                    elif dehumidifier.status() == '1' and not currently_open:
                        if (humidity < params.MAX_INTERNAL_HUMIDITY-10 and
                            temperature > params.MIN_INTERNAL_TEMPERATURE+1):
                            string = 'Internal humidity {}% is below {}%'
                            string = string.format(humidity, params.MAX_INTERNAL_HUMIDITY-10)
                            self.logfile.info(string)
                            string = 'and internal temperature {}C is above {}C'
                            string = string.format(temperature, params.MIN_INTERNAL_TEMPERATURE+1)
                            self.logfile.info(string)
                            self.logfile.info('Turning off dehumidifier')
                            dehumidifier.off()
                        elif self.override_dehumid_flag and self.dehumid_command == 'off':
                            self.logfile.info('Turning off dehumidifier (manual)')
                            dehumidifier.off()
                            self.override_dehumid_flag = 0
                            self.dehumid_command = 'none'

                    if dehumidifier.status() == '1' and currently_open:
                        self.logfile.info('Dome is open')
                        self.logfile.info('Turning off dehumidifier')
                        dehumidifier.off()

                    self.conditions_check_time = time.time()
                except:
                    self.logfile.error('check_humidity command failed')
                    self.logfile.debug('', exc_info=True)
                self.check_conditions_flag = 0

            ### control functions
            # request info
            if self.get_info_flag:
                try:
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

                    # add dehumidifier status
                    dehumidifier_status = dehumidifier.status()
                    if dehumidifier_status == '0':
                        info['dehumidifier'] = 'off'
                    elif dehumidifier_status == '1':
                        info['dehumidifier'] = 'on'
                    else:
                        info['dehumidifier'] = 'ERROR'

                    info['uptime'] = time.time() - self.start_time
                    info['ping'] = time.time() - self.time_check
                    now = datetime.datetime.utcnow()
                    info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")
                    if os.path.isfile(params.EMERGENCY_FILE):
                        info['emergency'] = 1
                    else:
                        info['emergency'] = 0
                    self.info = info
                except:
                    self.logfile.error('get_info command failed')
                    self.logfile.debug('', exc_info=True)
                self.get_info_flag = 0

            # open dome
            if self.open_flag:
                try:
                    # chose the side to move
                    if self.move_side == 'south':
                        side = 'south'
                    elif self.move_side == 'north':
                        side = 'north'
                    elif self.move_side == 'both':
                        side = 'south'
                    elif self.move_side == 'none':
                        self.logfile.info('Finished: Dome is open')
                        self.move_frac = 1
                        self.open_flag = 0
                        self.check_status_flag = 1
                        self.check_warnings_flag = 1

                    if self.open_flag and not self.move_started:
                        # before we start check if it's already there
                        if self.dome_status[side] == 'full_open':
                            self.logfile.info('The {} side is already open'.format(side))
                            if self.move_side == 'both':
                                self.move_side = 'north'
                            else:
                                self.move_side = 'none'
                        # otherwise ready to start moving
                        else:
                            try:
                                self.logfile.info('Opening {} side of dome'.format(side))
                                c = dome.open_full(side,self.move_frac)
                                if c: self.logfile.info(c)
                                self.move_started = 1
                                self.move_start_time = time.time()
                                self.check_status_flag = 1
                            except:
                                self.logfile.error('Failed to open dome')
                                self.logfile.debug('', exc_info=True)
                            # make sure dehumidifier is off
                            dehumidifier.off()

                    if self.move_started and not dome.output_thread_running:
                        ## we've finished
                        # check if we timed out
                        if time.time() - self.move_start_time > self.dome_timeout:
                            self.logfile.info('Moving timed out')
                            self.move_started = 0
                            self.move_side = 'none'
                            self.move_frac = 1
                            self.open_flag = 0
                            self.check_status_flag = 1
                            self.check_warnings_flag = 1
                        # we should be at the target
                        elif self.move_frac == 1:
                            self.logfile.info('The {} side is open'.format(side))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'north'
                            else:
                                self.move_side = 'none'
                        elif self.move_frac != 1:
                            self.logfile.info('The {} side moved requested fraction'.format(side))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'north'
                            else:
                                self.move_side = 'none'
                except:
                    self.logfile.error('open command failed')
                    self.logfile.debug('', exc_info=True)
                    self.open_flag = 0

            # close dome
            if self.close_flag:
                try:
                    # chose the side to move
                    if self.move_side == 'south':
                        side = 'south'
                    elif self.move_side == 'north':
                        side = 'north'
                    elif self.move_side == 'both':
                        side = 'north'
                    elif self.move_side == 'none':
                        self.logfile.info('Finished: Dome is closed')
                        self.move_frac = 1
                        self.close_flag = 0
                        self.check_status_flag = 1
                        self.check_warnings_flag = 1

                    if self.close_flag and not self.move_started:
                        # before we start check if it's already there
                        if self.dome_status[side] == 'closed':
                            self.logfile.info('The {} side is already closed'.format(side))
                            if self.move_side == 'both':
                                self.move_side = 'south'
                            else:
                                self.move_side = 'none'
                        # otherwise ready to start moving
                        else:
                            try:
                                self.logfile.info('Closing {} side of dome'.format(side))
                                c = dome.close_full(side,self.move_frac)
                                if c: self.logfile.info(c)
                                self.move_started = 1
                                self.move_start_time = time.time()
                                self.check_status_flag = 1
                            except:
                                self.logfile.error('Failed to close dome')
                                self.logfile.debug('', exc_info=True)

                    if self.move_started and not dome.output_thread_running:
                        ## we've finished
                        # check if we timed out
                        if time.time() - self.move_start_time > self.dome_timeout:
                            self.logfile.info('Moving timed out')
                            self.move_started = 0
                            self.move_side = 'none'
                            self.move_frac = 1
                            self.close_flag = 0
                            self.check_status_flag = 1
                            self.check_warnings_flag = 1
                        # we should be at the target
                        elif self.move_frac == 1:
                            self.logfile.info('The {} side is closed'.format(side))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'south'
                            else:
                                self.move_side = 'none'
                        elif self.move_frac != 1:
                            self.logfile.info('The {} side moved requested fraction'.format(side))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'south'
                            else:
                                self.move_side = 'none'
                except:
                    self.logfile.error('close command failed')
                    self.logfile.debug('', exc_info=True)
                    self.close_flag = 0

            # halt dome motion
            if self.halt_flag:
                try:
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
                    self.move_started = 0
                    self.move_start_time = 0
                except:
                    self.logfile.error('halt command failed')
                    self.logfile.debug('', exc_info=True)
                self.halt_flag = 0
                self.check_status_flag = 1

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return


    # Dome control functions
    def get_info(self):
        """Return dome status info"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set flag
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.1)
        return self.info


    def get_info_simple(self):
        """Return plain status dict, or None"""
        try:
            info = self.get_info()
        except:
            return None
        return info


    def open_dome(self, side='both', frac=1):
        """Open the dome"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')
        if flags.Conditions().bad and not flags.Overrides().autoclose:
            raise misc.HardwareStatusError('Conditions bad, dome will not open')
        elif flags.Power().failed:
            raise misc.HardwareStatusError('No external power, dome will not open')
        elif os.path.isfile(params.EMERGENCY_FILE):
            send_slack_msg('dome_daemon says: someone tried to open dome in emergency state')
            raise misc.HardwareStatusError('In emergency locked state, dome will not open')

        # Check input
        if not side in ['north', 'south', 'both']:
            raise ValueError('Side must be one of "north", "south" or "both"')
        if not (0 < frac <= 1):
            raise ValueError('Fraction must be between 0 and 1')

        # We want new commands to overwrite the old ones
        if self.open_flag or self.close_flag:
            self.halt_flag = 1
            time.sleep(3)

        # Check current status
        north_status = self.dome_status['north']
        south_status = self.dome_status['south']
        if side == 'north' and north_status == 'full_open':
            raise misc.HardwareStatusError('The north side is already fully open')
        elif side == 'south' and south_status == 'full_open':
            raise misc.HardwareStatusError('The south side is already fully open')
        elif side == 'both':
            if north_status == 'full_open' and south_status == 'full_open':
                raise misc.HardwareStatusError('The dome is already fully open')
            elif north_status == 'full_open' and south_status != 'full_open':
                side == 'south'
            elif north_status != 'full_open' and south_status == 'full_open':
                side == 'north'

        # Set values
        self.move_side = side
        self.move_frac = frac

        # Set flag
        self.logfile.info('Starting: Opening dome')
        self.open_flag = 1

        return 'Opening dome'


    def close_dome(self, side='both', frac=1):
        """Close the dome"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if not side in ['north', 'south', 'both']:
            raise ValueError('Side must be one of "north", "south" or "both"')
        if not (0 < frac <= 1):
            raise ValueError('Fraction must be between 0 and 1')

        # We want new commands to overwrite the old ones
        if self.open_flag or self.close_flag:
            self.halt_flag = 1
            time.sleep(3)

        # Check current status
        north_status = self.dome_status['north']
        south_status = self.dome_status['south']
        if side == 'north' and north_status == 'closed':
            raise misc.HardwareStatusError('The north side is already fully closed')
        elif side == 'south' and south_status == 'closed':
            raise misc.HardwareStatusError('The south side is already fully closed')
        elif side == 'both':
            if north_status == 'closed' and south_status == 'closed':
                raise misc.HardwareStatusError('The dome is already fully closed')
            elif north_status == 'closed' and south_status != 'closed':
                side == 'south'
            elif north_status != 'closed' and south_status == 'closed':
                side == 'north'

        # Set values
        self.move_side = side
        self.move_frac = frac

        # Set flag
        self.logfile.info('Starting: Closing dome')
        self.close_flag = 1

        return 'Closing dome'


    def halt_dome(self):
        """Stop the dome moving"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set flag
        self.halt_flag = 1

        return 'Halting dome'


    def override_dehumidifier(self, command):
        """Turn the dehumidifier on or off before the automatic command"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if not command in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.3)
        dehumid_status = self.info['dehumidifier']
        currently_open = self.info['dome'] != 'closed'
        if command == 'on' and currently_open:
            raise misc.HardwareStatusError("Dome is open, dehumidifier won't turn on")
        elif command == 'on' and dehumid_status == 'on':
            raise misc.HardwareStatusError('Dehumidifier is already on')
        elif command == 'off' and dehumid_status == 'off':
            raise misc.HardwareStatusError('Dehumidifier is already off')

        # Set values
        self.dehumid_command = command

        # Set flag
        if command == 'on':
            self.logfile.info('Turning on dehumidifier (manual command)')
        elif command == 'off':
            self.logfile.info('Turning off dehumidifier (manual command)')
        self.override_dehumid_flag = 1
        self.check_conditions_flag = 1

        if command == 'on':
            return 'Turning on dehumidifier (the daemon may turn it off again)'
        elif command == 'off':
            return 'Turning off dehumidifier (the daemon may turn it on again)'


if __name__ == "__main__":
    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(DAEMON_ID):
        sys.exit()

    # Create the daemon object
    daemon = DomeDaemon()

    # Start the daemon
    with Pyro4.Daemon(host=DAEMON_HOST, port=DAEMON_PORT) as pyro_daemon:
        uri = pyro_daemon.register(daemon, objectId=DAEMON_ID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=daemon.status_function)

    # Loop has closed
    daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)
