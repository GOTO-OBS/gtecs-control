#!/usr/bin/env python3
"""Daemon to control an AstroHaven dome."""

import threading
import time

from astropy.time import Time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon
from gtecs.flags import Conditions, Status
from gtecs.hardware.dome import AstroHavenDome, Dehumidifier
from gtecs.hardware.dome import FakeDehumidifier, FakeDome
from gtecs.observing import get_internal_conditions
from gtecs.slack import send_slack_msg

import serial


class DomeDaemon(BaseDaemon):
    """Dome hardware daemon class."""

    def __init__(self):
        super().__init__('dome')

        # hardware
        self.dome = None
        self.dehumidifier = None

        # command flags
        self.open_flag = 0
        self.close_flag = 0
        self.halt_flag = 0
        self.heartbeat_set_flag = 0
        self.dehumidifier_on_flag = 0
        self.dehumidifier_off_flag = 0

        # dome variables
        self.dome_timeout = 40.

        self.move_side = 'none'
        self.move_frac = 1
        self.move_started = 0
        self.move_start_time = 0
        self.last_move_time = None

        self.shielding = False
        self.lockdown = False
        self.lockdown_reasons = []
        self.ignoring_lockdown = False

        self.alarm_enabled = True
        self.heartbeat_enabled = True
        self.windshield_enabled = False

        self.autodehum_enabled = True
        self.autoclose_enabled = True

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

        while(self.running):
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time
                self.force_check_flag = False

                # Try to connect to the hardware
                self._connect()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's successful
                if self.hardware_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

                # If there is an error then getting info failed.
                # Restart the loop to try reconnecting above.
                if self.hardware_error:
                    continue

                # Check if the system mode has changed
                self._mode_check()

                # Check if we need to trigger a lockdown due to conditions
                self._lockdown_check()

                # Check if we need to close due to lockdown
                self._autoclose_check()

                # Check if we need to turn on/off the dehumidifier
                self._autodehum_check()

                # Check if we need to raise the shields
                self._windshield_check()

            # control functions
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
                        self.log.info('Finished moving')
                        self.last_move_time = self.loop_time
                        side = None
                        self.move_frac = 1
                        self.open_flag = 0
                        self.force_check_flag = True
                    else:
                        raise ValueError('Invalid side: {}'.format(self.move_side))

                    if self.open_flag and not self.move_started:
                        # before we start check if it's already there
                        if self.info[side] == 'full_open':
                            self.log.info('The {} side is already open'.format(side))
                            if self.move_side == 'both':
                                self.move_side = 'north'
                            else:
                                self.move_side = 'none'
                        # otherwise ready to start moving
                        else:
                            try:
                                self.log.info('Opening {} side of dome'.format(side))
                                c = self.dome.open_side(side, self.move_frac, self.alarm_enabled)
                                if c:
                                    self.log.info(c)
                                self.move_started = 1
                                self.move_start_time = time.time()
                                self.force_check_flag = True
                            except Exception:
                                self.log.error('Failed to open dome')
                                self.log.debug('', exc_info=True)

                    if self.move_started and not self.dome.output_thread_running:
                        # we've finished
                        # check if we timed out
                        if time.time() - self.move_start_time > self.dome_timeout:
                            self.log.info('Moving timed out')
                            self.move_started = 0
                            self.move_side = 'none'
                            self.move_frac = 1
                            self.open_flag = 0
                        # we should be at the target
                        elif self.move_frac == 1:
                            self.log.info('The {} side is open'.format(side))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'north'
                            else:
                                self.move_side = 'none'
                        elif self.move_frac != 1:
                            self.log.info('The {} side moved requested fraction'.format(side))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'north'
                            else:
                                self.move_side = 'none'
                except Exception:
                    self.log.error('open command failed')
                    self.log.debug('', exc_info=True)
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
                        self.log.info('Finished moving')
                        self.last_move_time = self.loop_time
                        side = None
                        self.move_frac = 1
                        self.close_flag = 0
                        self.force_check_flag = True
                    else:
                        raise ValueError('Invalid side: {}'.format(self.move_side))

                    if self.close_flag and not self.move_started:
                        # before we start check if it's already there
                        if self.info[side] == 'closed':
                            self.log.info('The {} side is already closed'.format(side))
                            if self.move_side == 'both':
                                self.move_side = 'south'
                            else:
                                self.move_side = 'none'
                        # otherwise ready to start moving
                        else:
                            try:
                                self.log.info('Closing {} side of dome'.format(side))
                                c = self.dome.close_side(side, self.move_frac, self.alarm_enabled)
                                if c:
                                    self.log.info(c)
                                self.move_started = 1
                                self.move_start_time = time.time()
                                self.force_check_flag = True
                            except Exception:
                                self.log.error('Failed to close dome')
                                self.log.debug('', exc_info=True)

                    if self.move_started and not self.dome.output_thread_running:
                        # we've finished
                        # check if we timed out
                        if time.time() - self.move_start_time > self.dome_timeout:
                            self.log.info('Moving timed out')
                            self.move_started = 0
                            self.move_side = 'none'
                            self.move_frac = 1
                            self.close_flag = 0
                        # we should be at the target
                        elif self.move_frac == 1:
                            self.log.info('The {} side is closed'.format(side))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'south'
                            else:
                                self.move_side = 'none'
                        elif self.move_frac != 1:
                            self.log.info('The {} side moved requested fraction'.format(side))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'south'
                            else:
                                self.move_side = 'none'
                except Exception:
                    self.log.error('close command failed')
                    self.log.debug('', exc_info=True)
                    self.close_flag = 0

            # halt dome motion
            if self.halt_flag:
                try:
                    try:
                        self.log.info('Halting dome')
                        c = self.dome.halt()
                        if c:
                            self.log.info(c)
                    except Exception:
                        self.log.error('Failed to halt dome')
                        self.log.debug('', exc_info=True)
                    # set move time now, since it's usually set when moving stops
                    self.last_move_time = self.loop_time
                    # reset everything
                    self.open_flag = 0
                    self.close_flag = 0
                    self.move_side = 'none'
                    self.move_frac = 1
                    self.move_started = 0
                    self.move_start_time = 0
                except Exception:
                    self.log.error('halt command failed')
                    self.log.debug('', exc_info=True)
                self.halt_flag = 0
                self.force_check_flag = True

            # set heartbeat
            if self.heartbeat_set_flag:
                try:
                    if self.heartbeat_enabled:
                        self.log.info('Enabling heartbeat')
                        c = self.dome.set_heartbeat(True)
                    else:
                        self.log.info('Disabling heartbeat')
                        c = self.dome.set_heartbeat(False)
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('set heartbeat command failed')
                    self.log.debug('', exc_info=True)
                self.heartbeat_set_flag = 0
                self.force_check_flag = True

            # turn on dehumidifer
            if self.dehumidifier_on_flag:
                try:
                    self.log.info('Turning dehumidifer on')
                    c = self.dehumidifier.on()
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('dehumidifer on command failed')
                    self.log.debug('', exc_info=True)
                self.dehumidifier_on_flag = 0
                self.force_check_flag = True

            # turn off dehumidifer
            if self.dehumidifier_off_flag:
                try:
                    self.log.info('Turning dehumidifer off')
                    c = self.dehumidifier.off()
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('dehumidifer off command failed')
                    self.log.debug('', exc_info=True)
                self.dehumidifier_off_flag = 0
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _connect(self):
        """Connect to hardware."""
        # Connect to the dome
        if self.dome is None:
            if params.FAKE_DOME:
                self.dome = FakeDome('/dev/fake', '/dev/fake2', self.log, params.DOME_DEBUG)
                self.log.info('Connected to dome')
            else:
                try:
                    self.dome = AstroHavenDome(params.DOME_LOCATION,
                                               params.DOME_HEARTBEAT_LOCATION,
                                               self.log,
                                               params.DOME_DEBUG,
                                               )
                    self.log.info('Connected to dome')
                    if 'dome' in self.bad_hardware:
                        self.bad_hardware.remove('dome')
                    # sleep briefly, to make sure the connection has started
                    time.sleep(3)
                except Exception:
                    self.dome.disconnect()
                    self.dome = None
                    if 'dome' not in self.bad_hardware:
                        self.log.error('Failed to connect to dome')
                        self.bad_hardware.add('dome')

        # Check the connections within the dome
        if self.dome is not None:
            if self.dome.plc_error:
                self.log.error('Failed to connect to dome PLC')
                self.dome.disconnect()
                self.dome = None
                self.bad_hardware.add('dome')
            elif self.dome.arduino_error:
                self.log.error('Failed to connect to dome arduino')
                self.dome.disconnect()
                self.dome = None
                self.bad_hardware.add('dome')
            elif self.dome.heartbeat_error:
                self.log.error('Failed to connect to dome heartbeat monitor')
                self.dome.disconnect()
                self.dome = None
                self.bad_hardware.add('dome')

        # Connect to the dehumidifer
        if self.dehumidifier is None:
            if params.FAKE_DOME:
                self.dehumidifier = FakeDehumidifier()
                self.log.info('Connected to dehumidifier')
            else:
                try:
                    dehumidifier_address = params.DEHUMIDIFIER_IP
                    dehumidifier_port = params.DEHUMIDIFIER_PORT
                    self.dehumidifier = Dehumidifier(dehumidifier_address, dehumidifier_port)
                    self.log.info('Connected to dehumidifier')
                    if 'dehumidifier' in self.bad_hardware:
                        self.bad_hardware.remove('dehumidifier')
                except Exception:
                    self.dehumidifier = None
                    if 'dehumidifier' not in self.bad_hardware:
                        self.log.error('Failed to connect to dehumidifier')
                        self.bad_hardware.add('dehumidifier')

        # Finally check if we need to report an error
        self._check_errors()

    def _get_info(self):
        """Get the latest status info from the heardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from the dome
        try:
            dome_status = self.dome.status
            temp_info['north'] = dome_status['north']
            temp_info['south'] = dome_status['south']
            temp_info['hatch'] = dome_status['hatch']

            # general, backwards-compatible open/closed
            if ('open' in temp_info['north']) or ('open' in temp_info['south']):
                temp_info['dome'] = 'open'
            elif (temp_info['north'] == 'closed') and (temp_info['south'] == 'closed'):
                temp_info['dome'] = 'closed'
            else:
                temp_info['dome'] = 'ERROR'

            heartbeat_status = self.dome.heartbeat_status
            temp_info['heartbeat_status'] = heartbeat_status
        except Exception:
            self.log.error('Failed to get dome info')
            self.log.debug('', exc_info=True)
            temp_info['north'] = None
            temp_info['south'] = None
            temp_info['hatch'] = None
            temp_info['dome'] = None
            temp_info['heartbeat_status'] = None
            # Report the connection as failed
            self.dome.disconnect()
            self.dome = None
            if 'dome' not in self.bad_hardware:
                self.bad_hardware.add('dome')

        # Get dehumidifier info
        try:
            dehumidifier_status = self.dehumidifier.status
            temp_info['dehumidifier_on'] = bool(int(dehumidifier_status))
            temp_info['humidity_upper'] = params.MAX_INTERNAL_HUMIDITY
            temp_info['humidity_lower'] = params.MAX_INTERNAL_HUMIDITY - 10
            temp_info['temperature_lower'] = params.MIN_INTERNAL_TEMPERATURE
            temp_info['temperature_upper'] = params.MIN_INTERNAL_TEMPERATURE + 1
        except Exception:
            self.log.error('Failed to get dehumidifier info')
            self.log.debug('', exc_info=True)
            temp_info['dehumidifier_on'] = None
            temp_info['humidity_upper'] = None
            temp_info['humidity_lower'] = None
            temp_info['temperature_lower'] = None
            temp_info['temperature_upper'] = None
            # Report the connection as failed
            self.dehumidifier = None
            if 'dehumidifier' not in self.bad_hardware:
                self.bad_hardware.add('dehumidifier')

        # Get the dome internal conditions
        try:
            int_conditions = get_internal_conditions(timeout=10)
            temp_info['temperature'] = int_conditions['temperature']
            temp_info['humidity'] = int_conditions['humidity']
        except Exception:
            self.log.error('Failed to get dome internal conditions')
            self.log.debug('', exc_info=True)
            temp_info['temperature'] = None
            temp_info['humidity'] = None

        # Get button info
        try:
            button_pressed = self._button_pressed(params.QUICK_CLOSE_BUTTON_PORT)
            temp_info['button_pressed'] = bool(button_pressed)
        except Exception:
            self.log.error('Failed to get quick close button info')
            self.log.debug('', exc_info=True)
            temp_info['button_pressed'] = None

        # Get conditions info
        try:
            conditions = Conditions()
            temp_info['conditions_bad'] = bool(conditions.bad)
            temp_info['conditions_bad_reasons'] = ', '.join(conditions.bad_flags)
        except Exception:
            self.log.error('Failed to get conditions info')
            self.log.debug('', exc_info=True)
            temp_info['conditions_bad'] = None
            temp_info['conditions_bad_reasons'] = None

        # Get status info
        try:
            status = Status()
            temp_info['emergency'] = status.emergency_shutdown
            temp_info['emergency_time'] = status.emergency_shutdown_time
            temp_info['emergency_reasons'] = ', '.join(status.emergency_shutdown_reasons)
            temp_info['mode'] = status.mode
        except Exception:
            self.log.error('Failed to get status info')
            self.log.debug('', exc_info=True)
            temp_info['emergency'] = None
            temp_info['emergency_time'] = None
            temp_info['emergency_reasons'] = None
            temp_info['mode'] = None

        # Get other internal info
        temp_info['last_move_time'] = self.last_move_time
        temp_info['shielding'] = self.shielding
        temp_info['lockdown'] = self.lockdown
        temp_info['lockdown_reasons'] = self.lockdown_reasons
        temp_info['ignoring_lockdown'] = self.ignoring_lockdown
        temp_info['alarm_enabled'] = self.alarm_enabled
        temp_info['heartbeat_enabled'] = self.heartbeat_enabled
        temp_info['windshield_enabled'] = self.windshield_enabled
        temp_info['autodehum_enabled'] = self.autodehum_enabled
        temp_info['autoclose_enabled'] = self.autoclose_enabled

        # Write debug log line
        try:
            if not self.info:
                self.log.debug('Dome is {}'.format(temp_info['dome']))
            elif temp_info['dome'] != self.info['dome']:
                self.log.debug('Dome is {}'.format(temp_info['dome']))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    def _mode_check(self):
        """Check the current system mode and make sure the alarm is on/off."""
        if self.info['mode'] == 'robotic':
            # In robotic everything should always be enabled
            if not self.alarm_enabled:
                self.log.info('System is in robotic mode, enabling alarm')
                self.alarm_enabled = True
            if not self.heartbeat_enabled:
                self.log.info('System is in robotic mode, enabling heartbeat')
                self.heartbeat_enabled = True
                self.heartbeat_set_flag = 1
            if not self.autodehum_enabled:
                self.log.info('System is in robotic mode, enabling autodehum')
                self.autodehum_enabled = True
            if not self.autoclose_enabled:
                self.log.info('System is in robotic mode, enabling autoclose')
                self.autoclose_enabled = True

        elif self.info['mode'] == 'manual':
            # In manual mode the heartbeat should be enabled, everything else can be set
            if not self.heartbeat_enabled:
                self.log.info('System is in manual mode, enabling heartbeat')
                self.heartbeat_enabled = True
                self.heartbeat_set_flag = 1

        elif self.info['mode'] == 'engineering':
            # In engineering mode everything should always be disabled
            if self.alarm_enabled:
                self.log.info('System is in engineering mode, disabling alarm')
                self.alarm_enabled = False
            if self.heartbeat_enabled:
                self.log.info('System is in engineering mode, disabling heartbeat')
                self.heartbeat_enabled = False
                self.heartbeat_set_flag = 1
            if self.autodehum_enabled:
                self.log.info('System is in engineering mode, disabling autodehum')
                self.autodehum_enabled = False
            if self.autoclose_enabled:
                self.log.info('System is in engineering mode, disabling autoclose')
                self.autoclose_enabled = False

    def _lockdown_check(self):
        """Check the current conditions and set or clear the lockdown flag."""
        lockdown = False
        reasons = []

        # Check if the quick-close button has been pressed
        if self.info['button_pressed']:
            lockdown = True
            reasons.append('quick-close button pressed')
            send_slack_msg('Dome quick-close button has been pressed')

        # Check if the emergency shutdown file has been created
        if self.info['emergency']:
            lockdown = True
            reasons.append('emergency shutdown ({})'.format(self.info['emergency_reasons']))

        # Check if the conditions are bad
        if self.info['conditions_bad']:
            lockdown = True
            reasons.append('conditions bad ({})'.format(self.info['conditions_bad_reasons']))

        # Set the flag
        if lockdown:
            if self.autoclose_enabled:
                # Activate lockdown
                self.lockdown = True
                if reasons != self.lockdown_reasons or self.ignoring_lockdown:
                    self.log.warning('Lockdown: {}'.format(', '.join(reasons)))
                    self.lockdown_reasons = reasons
                    self.ignoring_lockdown = False
            else:
                # Autoclose disabled, ignore lockdown
                self.lockdown = False
                if reasons != self.lockdown_reasons or not self.ignoring_lockdown:
                    self.log.warning('IGNORING Lockdown: {}'.format(', '.join(reasons)))
                    self.lockdown_reasons = reasons
                    self.ignoring_lockdown = True
        else:
            if self.lockdown or self.lockdown_reasons:
                # Clear lockdown
                self.lockdown = False
                self.log.info('Lockdown lifted')
                self.lockdown_reasons = []
                self.ignoring_lockdown = False

    def _autoclose_check(self):
        """Check if the dome is in lockdown and needs to autoclose."""
        if not self.dome:
            self.log.warning('Autoclose disabled while no connection to dome')
            return

        # Return if autoclose disabled
        if not self.autoclose_enabled:
            return

        # Decide if we need to autoclose
        if self.lockdown and self.info['dome'] != 'closed' and not self.close_flag:
            self.log.warning('Autoclosing dome due to lockdown')
            send_slack_msg('Dome is autoclosing')
            # Stop any opening
            if self.open_flag:
                self.halt_flag = 1
                time.sleep(2)
            # Make sure the alarm sounds, since we're moving automatically
            self.alarm_enabled = True
            # Close the dome
            self.close_flag = 1
            self.move_side = 'both'
            self.move_frac = 1

    def _autodehum_check(self):
        """Check the current internal conditions, then turn on the dehumidifer if needed."""
        if not self.dehumidifier:
            self.log.warning('Auto humidity control disabled while no connection to dehumidifier')
            return

        if (isinstance(self.info, dict) and
                (self.info.get('humidity') is None or self.info.get('temperature') is None)):
            # Note dict.get() returns None if it is not in the dictionary
            self.log.warning('No internal conditions readings: auto humidity control unavailable')
            return

        # Return if autodehum disabled
        if not self.autodehum_enabled:
            return

        # Check if the dehumidifier should be on or off
        if self.info['dome'] != 'closed' and self.info['dehumidifier_on']:
            self.log.info('Dome is open, turning off dehumidifier')
            self.dehumidifier_off_flag = 1

        elif self.info['dome'] == 'closed' and not self.info['dehumidifier_on']:
            if self.info['humidity'] > self.info['humidity_upper']:
                self.log.info('Dome humidity {}% > {}%'.format(self.info['humidity'],
                                                               self.info['humidity_upper']))
                self.dehumidifier_on_flag = 1

            if self.info['temperature'] < self.info['temperature_lower']:
                self.log.info('Dome temperature {}C < {}C'.format(self.info['temperature'],
                                                                  self.info['temperature_lower']))
                self.dehumidifier_on_flag = 1

        elif self.info['dome'] == 'closed' and self.info['dehumidifier_on']:
            if (self.info['humidity'] < self.info['humidity_lower'] and
                    self.info['temperature'] > self.info['temperature_upper']):
                self.log.info('Dome humidity {}% < {}%'.format(self.info['humidity'],
                                                               self.info['humidity_lower']))
                self.log.info('Dome temperature {}C > {}C'.format(self.info['temperature'],
                                                                  self.info['temperature_upper']))
                self.dehumidifier_off_flag = 1

    def _windshield_check(self):
        """Check if the dome is open and needs to raise shields."""
        if not self.dome:
            self.log.warning('Shielding disabled while no connection to dome')
            return

        # Check if we are currently shielding
        if (self.shielding and
                self.info['north'] in ['full_open', 'closed'] and
                self.info['south'] in ['full_open', 'closed']):
            # The dome must have moved some other way, either manually or via autoclose
            self.shielding = False

        # Decide if we need to raise or lower shields
        if (self.windshield_enabled and
                (self.info['north'] == 'full_open' or self.info['south'] == 'full_open') and
                not self.open_flag and not self.close_flag):
            self.log.warning('Moving dome shutters to windshield position')
            self.shielding = True
            # Make sure the alarm sounds, since we're moving automatically
            self.alarm_enabled = True
            # Partially close the dome
            self.close_flag = 1
            self.move_side = 'both'
            self.move_frac = 0.3

        elif (self.shielding and not self.windshield_enabled and
              (self.info['north'] == 'part_open' or self.info['south'] == 'part_open') and
              not self.open_flag and not self.close_flag):
            self.log.warning('Moving dome shutters to full open')
            self.shielding = False
            # Make sure the alarm sounds, since we're moving automatically
            self.alarm_enabled = True
            # Fully open the dome
            self.open_flag = 1
            self.move_side = 'both'
            self.move_frac = 1

    def _button_pressed(self, port='/dev/ttyS3'):
        """Send a message to the serial port and try to read it back."""
        if not params.QUICK_CLOSE_BUTTON:
            return False
        button_port = serial.Serial(port, timeout=1, xonxoff=True)
        chances = 3
        for _ in range(chances):
            button_port.write(b'bob\n')
            reply = button_port.readlines()
            for x in reply:
                if x.find(b'bob') >= 0:
                    button_port.close()
                    return False
        button_port.close()
        return True

    # Control functions
    def open_dome(self, side='both', frac=1):
        """Open the dome."""
        # Check restrictions
        if self.lockdown:
            raise errors.HardwareStatusError('Dome is in lockdown')

        # Check input
        if side not in ['north', 'south', 'both']:
            raise ValueError('Side must be one of "north", "south" or "both"')
        if not (0 < frac <= 1):
            raise ValueError('Fraction must be between 0 and 1')

        # Check current status
        if self.open_flag:
            return 'The dome is already opening'
        elif self.close_flag:
            # We want to overwrite the previous command
            self.halt_flag = 1
            time.sleep(3)
        self.wait_for_info()
        north_status = self.info['north']
        south_status = self.info['south']
        if side == 'north' and north_status == 'full_open':
            return 'The north side is already fully open'
        elif side == 'south' and south_status == 'full_open':
            return 'The south side is already fully open'
        elif side == 'both':
            if north_status == 'full_open' and south_status == 'full_open':
                return 'The dome is already fully open'
            elif north_status == 'full_open' and south_status != 'full_open':
                side == 'south'
            elif north_status != 'full_open' and south_status == 'full_open':
                side == 'north'

        # Set values
        self.move_side = side
        self.move_frac = frac

        # Set flag
        self.log.info('Starting: Opening dome')
        self.open_flag = 1

        return 'Opening dome'

    def close_dome(self, side='both', frac=1):
        """Close the dome."""
        # Check input
        if side not in ['north', 'south', 'both']:
            raise ValueError('Side must be one of "north", "south" or "both"')
        if not (0 < frac <= 1):
            raise ValueError('Fraction must be between 0 and 1')

        # Check current status
        if self.close_flag:
            return 'The dome is already closing'
        elif self.open_flag:
            # We want to overwrite the previous command
            self.halt_flag = 1
            time.sleep(3)
        self.wait_for_info()
        north_status = self.info['north']
        south_status = self.info['south']
        if side == 'north' and north_status == 'closed':
            return 'The north side is already fully closed'
        elif side == 'south' and south_status == 'closed':
            return 'The south side is already fully closed'
        elif side == 'both':
            if north_status == 'closed' and south_status == 'closed':
                return 'The dome is already fully closed'
            elif north_status == 'closed' and south_status != 'closed':
                side == 'south'
            elif north_status != 'closed' and south_status == 'closed':
                side == 'north'

        # Set values
        self.move_side = side
        self.move_frac = frac

        # Set flag
        self.log.info('Starting: Closing dome')
        self.close_flag = 1

        return 'Closing dome'

    def halt_dome(self):
        """Stop the dome moving."""
        # Set flag
        self.halt_flag = 1

        return 'Halting dome'

    def override_dehumidifier(self, command):
        """Turn the dehumidifier on or off manually."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.wait_for_info()
        dehumidifier_on = self.info['dehumidifier_on']
        currently_open = self.info['dome'] != 'closed'
        if command == 'on' and currently_open:
            raise errors.HardwareStatusError("Dome is open, dehumidifier won't turn on")
        elif command == 'on' and dehumidifier_on:
            return 'Dehumidifier is already on'
        elif command == 'off' and not dehumidifier_on:
            return 'Dehumidifier is already off'

        # Set flag
        if command == 'on':
            self.log.info('Turning on dehumidifier (manual command)')
            self.dehumidifier_on_flag = 1
        elif command == 'off':
            self.log.info('Turning off dehumidifier (manual command)')
            self.dehumidifier_off_flag = 1

        if command == 'on':
            s = 'Turning on dehumidifier'
            if self.autodehum_enabled:
                s += ' (autodehum is enabled, so the daemon may turn it off again)'
            return s
        elif command == 'off':
            s = 'Turning off dehumidifier'
            if self.autodehum_enabled:
                s += ' (autodehum is enabled, so the daemon may turn it on again)'
            return s

    def set_autodehum(self, command):
        """Enable or disable the dome automatically turning the dehumidifier on and off."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.wait_for_info()
        if command == 'on':
            if self.info['mode'] == 'engineering':
                raise errors.HardwareStatusError('Cannot enable autodehum in engineering mode')
            elif self.autodehum_enabled:
                return 'Autodehum is already enabled'
        else:
            if self.info['mode'] == 'robotic':
                raise errors.HardwareStatusError('Cannot disable autodehum in robotic mode')
            elif not self.autodehum_enabled:
                return 'Autodehum is already disabled'

        # Set flag
        if command == 'on':
            self.log.info('Enabling autodehum')
            self.autodehum_enabled = True
        elif command == 'off':
            self.log.info('Disabling autodehum')
            self.autodehum_enabled = False

        if command == 'on':
            return 'Enabling autodehum, the dehumidifier will turn on and off automatically'
        elif command == 'off':
            return 'Disabling autodehum, the dehumidifier will NOT turn on or off automatically'

    def set_autoclose(self, command):
        """Enable or disable the dome autoclosing in bad conditions."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.wait_for_info()
        if command == 'on':
            if self.info['mode'] == 'engineering':
                raise errors.HardwareStatusError('Cannot enable autoclose in engineering mode')
            elif self.autoclose_enabled:
                return 'Autoclose is already enabled'
        else:
            if self.info['mode'] == 'robotic':
                raise errors.HardwareStatusError('Cannot disable autoclose in robotic mode')
            elif not self.autoclose_enabled:
                return 'Autoclose is already disabled'

        # Set flag
        if command == 'on':
            self.log.info('Enabling autoclose')
            self.autoclose_enabled = True
        elif command == 'off':
            self.log.info('Disabling autoclose')
            self.autoclose_enabled = False

        if command == 'on':
            return 'Enabling autoclose, dome will close in bad conditions'
        elif command == 'off':
            return 'Disabling autoclose, dome will NOT close in bad conditions'

    def set_alarm(self, command):
        """Enable or disable the dome alarm when moving."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.wait_for_info()
        if command == 'on':
            if self.info['mode'] == 'engineering':
                raise errors.HardwareStatusError('Cannot enable alarm in engineering mode')
            elif self.alarm_enabled:
                return 'Alarm is already enabled'
        else:
            if self.info['mode'] == 'robotic':
                raise errors.HardwareStatusError('Cannot disable alarm in robotic mode')
            elif not self.alarm_enabled:
                return 'Alarm is already disabled'

        # Set flag
        if command == 'on':
            self.log.info('Enabling alarm')
            self.alarm_enabled = True
        elif command == 'off':
            self.log.info('Disabling alarm')
            self.alarm_enabled = False

        if command == 'on':
            return 'Enabling dome alarm'
        elif command == 'off':
            return 'Disabling dome alarm'

    def set_heartbeat(self, command):
        """Enable or disable the dome heartbeat system."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.wait_for_info()
        if command == 'on' and self.info['mode'] == 'engineering':
            raise errors.HardwareStatusError('Cannot enable heartbeat in engineering mode')
        elif command == 'off' and self.info['mode'] == 'manual':
            raise errors.HardwareStatusError('Cannot disable heartbeat in manual mode')
        elif command == 'off' and self.info['mode'] == 'robotic':
            raise errors.HardwareStatusError('Cannot disable heartbeat in manual mode')

        # Set flag
        if command == 'on':
            self.heartbeat_enabled = True
            self.heartbeat_set_flag = 1
        elif command == 'off':
            self.heartbeat_enabled = False
            self.heartbeat_set_flag = 1

        if command == 'on':
            return 'Enabling dome heartbeat'
        elif command == 'off':
            return 'Disabling dome heartbeat'

    def override_windshield(self, command):
        """Turn windshielding on or off manually."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.wait_for_info()
        windshield_enabled = self.info['windshield_enabled']
        if command == 'on' and windshield_enabled:
            return 'Windshielding is already enabled'
        elif command == 'off' and not windshield_enabled:
            return 'Windshielding is already disabled'

        # Set flag
        if command == 'on':
            self.log.info('Enabling windshield mode (manual command)')
            self.windshield_enabled = True
        elif command == 'off':
            self.log.info('Disabling windshield mode (manual command)')
            self.windshield_enabled = False

        if command == 'on':
            s = 'Enabling windshield mode'
            return s
        elif command == 'off':
            s = 'Disabling windshield mode'
            return s


if __name__ == '__main__':
    daemon_id = 'dome'
    with misc.make_pid_file(daemon_id):
        DomeDaemon()._run()
