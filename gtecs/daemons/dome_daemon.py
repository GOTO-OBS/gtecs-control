#!/usr/bin/env python
"""Daemon to control an AstroHaven dome."""

import threading
import time

from astropy.time import Time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon
from gtecs.flags import Conditions, Power, Status
from gtecs.hardware.dome import AstroHavenDome, Dehumidifier
from gtecs.hardware.dome import FakeDehumidifier, FakeDome
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
        self.dehumidifier_on_flag = 0
        self.dehumidifier_off_flag = 0

        # dome variables
        self.dome_timeout = 40.
        self.lockdown = False

        self.move_side = 'none'
        self.move_frac = 1
        self.move_started = 0
        self.move_start_time = 0

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
                # Keep looping, it should retry the connection until it's sucsessful
                if self.hardware_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

                # If there is an error then getting info failed.
                # Restart the loop to try reconnecting above.
                if self.hardware_error:
                    continue

                # Dome automation: close or turn on dehumidifier if nessesary
                self._auto_close()
                self._auto_dehum()

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
                        self.log.info('Finished: Dome is open')
                        self.move_frac = 1
                        self.open_flag = 0
                        self.force_check_flag = True

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
                                c = self.dome.open_side(side, self.move_frac)
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
                        self.log.info('Finished: Dome is closed')
                        self.move_frac = 1
                        self.close_flag = 0
                        self.force_check_flag = True
                        # whenever the dome is closed, re-enable autoclose
                        status = Status()
                        if not status.autoclose and self.info['dome'] == 'closed':
                            status.autoclose = True
                            self.log.info('Re-enabled dome auto-close')

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
                                c = self.dome.close_side(side, self.move_frac)
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
        if not self.dome:
            if params.FAKE_DOME:
                self.dome = FakeDome()
                self.log.info('Connected to dome')
            else:
                try:
                    dome_port = params.DOME_LOCATION
                    heartbeat_port = params.DOME_HEARTBEAT_LOCATION
                    self.dome = AstroHavenDome(dome_port, heartbeat_port)
                    self.log.info('Connected to dome')
                    if 'dome' in self.bad_hardware:
                        self.bad_hardware.remove('dome')
                    # sleep brefly, to make sure the connection has started
                    time.sleep(3)
                except Exception:
                    self.dome = None
                    self.log.error('Failed to connect to dome')
                    if 'dome' not in self.bad_hardware:
                        self.bad_hardware.add('dome')

        # Connect to the dehumidifer
        if not self.dehumidifier:
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
                    self.log.error('Failed to connect to dehumidifier')
                    if 'dehumidifier' not in self.bad_hardware:
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
            temp_info['heartbeat'] = heartbeat_status
        except Exception:
            self.log.error('Failed to get dome info')
            self.log.debug('', exc_info=True)
            temp_info['north'] = None
            temp_info['south'] = None
            temp_info['hatch'] = None
            temp_info['dome'] = None
            temp_info['heartbeat'] = None
            # Report the connection as failed
            self.dome = None
            if 'dome' not in self.bad_hardware:
                self.bad_hardware.add('dome')

        # Get dehumidifier info
        try:
            dehumidifier_status = self.dehumidifier.status()
            temp_info['dehumidifier_on'] = bool(int(dehumidifier_status))
            dome_conditions = self.dehumidifier.conditions()
            temp_info['humidity'] = dome_conditions['humidity']
            temp_info['humidity_upper'] = params.MAX_INTERNAL_HUMIDITY
            temp_info['humidity_lower'] = params.MAX_INTERNAL_HUMIDITY - 10
            temp_info['temperature'] = dome_conditions['temperature']
            temp_info['temperature_lower'] = params.MIN_INTERNAL_TEMPERATURE
            temp_info['temperature_upper'] = params.MIN_INTERNAL_TEMPERATURE + 1
        except Exception:
            self.log.error('Failed to get dehumidifier info')
            self.log.debug('', exc_info=True)
            temp_info['dehumidifier_on'] = None
            temp_info['humidity'] = None
            temp_info['humidity_upper'] = None
            temp_info['humidity_lower'] = None
            temp_info['temperature'] = None
            temp_info['temperature_lower'] = None
            temp_info['temperature_upper'] = None
            # Report the connection as failed
            self.dehumidifier = None
            if 'dehumidifier' not in self.bad_hardware:
                self.bad_hardware.add('dehumidifier')

        # Get button info
        try:
            button_pressed = self._button_pressed(params.QUICK_CLOSE_BUTTON_PORT)
            temp_info['button_pressed'] = button_pressed
        except Exception:
            self.log.error('Failed to get quick close button info')
            self.log.debug('', exc_info=True)
            temp_info['button_pressed'] = None

        # Get conditions info
        try:
            conditions = Conditions()
            temp_info['conditions_bad'] = bool(conditions.bad)
            temp_info['conditions_bad_reasons'] = conditions.bad_flags
            temp_info['conditions_critical'] = bool(conditions.critical)
            temp_info['conditions_critical_reasons'] = conditions.critical_flags
        except Exception:
            self.log.error('Failed to get conditions info')
            self.log.debug('', exc_info=True)
            temp_info['conditions_bad'] = None
            temp_info['conditions_bad_reasons'] = None
            temp_info['conditions_critical'] = None
            temp_info['conditions_critical_reasons'] = None

        # Dome automation - create emergency file if needed
        # TODO: Status() is awkward
        try:
            if temp_info['button_pressed']:
                self.log.warning('Quick close button pressed!')
                Status().create_shutdown_file(['quick close button pressed'])
            if temp_info['conditions_critical']:
                reasons = temp_info['conditions_critical_reasons']
                self.log.warning('Conditions critical ({})'.format(reasons))
                Status().create_shutdown_file(reasons.split(', '))
        except Exception:
            self.log.error('Failed to create emergency shutdown file')
            self.log.debug('', exc_info=True)

        # Get status info
        try:
            status = Status()
            temp_info['emergency'] = status.emergency_shutdown
            temp_info['emergency_time'] = status.emergency_shutdown_time
            temp_info['emergency_reasons'] = status.emergency_shutdown_reasons
            temp_info['mode'] = status.mode
            temp_info['autoclose'] = status.autoclose
            temp_info['alarm'] = status.alarm
        except Exception:
            self.log.error('Failed to get status info')
            self.log.debug('', exc_info=True)
            temp_info['emergency'] = None
            temp_info['emergency_time'] = None
            temp_info['emergency_reasons'] = None
            temp_info['mode'] = None
            temp_info['autoclose'] = None
            temp_info['alarm'] = None

        # Get other internal info
        temp_info['lockdown'] = self.lockdown

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

    def _auto_close(self):
        """Check the current conditions and set warning flags, then close if bad."""
        if not self.dome:
            self.log.warning('Auto close disabled while no connection to dome')
            return

        # Check if we need to set or clear the lockdown
        if self.info['emergency']:
            reasons = ', '.join(self.info['emergency_reasons'])
            self.log.warning('Lockdown: emergency shutdown ({})!'.format(reasons))
            self.lockdown = True
        elif self.info['conditions_bad']:
            reasons = self.info['conditions_bad_reasons']
            if self.info['mode'] == 'manual' and not self.info['autoclose']:
                self.log.warning('Conditions bad ({}), but autoclose is disabled!'.format(reasons))
                self.lockdown = False
            else:
                self.log.warning('Lockdown: conditions bad ({})!'.format(reasons))
                self.lockdown = True
        elif self.lockdown and not self.info['emergency'] and not self.info['conditions_bad']:
            self.log.info('Conditions are clear, lockdown lifted')
            self.lockdown = False

        # React to a lockdown order
        if self.lockdown and self.info['dome'] != 'closed' and self.info['autoclose']:
            self.log.warning('Autoclosing dome due to lockdown')
            if not self.close_flag:
                reasons = ''
                if self.info['emergency'] and self.info['emergency_reasons'][0]:
                    reasons += ', '.join(self.info['emergency_reasons'])
                if self.info['conditions_bad'] and self.info['conditions_bad_reasons']:
                    reasons += ' ' + self.info['conditions_bad_reasons']
                send_slack_msg('Dome is autoclosing: {}'.format(reasons))
                if self.open_flag:  # stop opening!
                    self.halt_flag = 1
                    time.sleep(2)
                Status().alarm = True  # make sure the alarm sounds TODO: Status() is awkward
                self.close_flag = 1
                self.move_side = 'both'
                self.move_frac = 1

    def _auto_dehum(self):
        """Check the current internal conditions, then turn on the dehumidifer if needed."""
        if not self.dehumidifier:
            self.log.warning('Auto humidity control disabled while no connection to dehumidifier')
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
                                                               self.info['humidity_upper']))
                self.log.info('Dome temperature {}C > {}C'.format(self.info['temperature'],
                                                                  self.info['temperature_lower']))
                self.dehumidifier_off_flag = 1

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
        conditions = Conditions()
        status = Status()
        power = Power()
        bad_idea = False
        # Check restrictions
        if conditions.bad:
            if status.mode == 'manual' and not status.autoclose:
                # Allow opening in bad conditions if in manual mode
                # and autoclose is disabled
                bad_idea = True
            else:
                reason = 'Conditions bad ({})'.format(conditions.bad_flags)
                raise errors.HardwareStatusError('{}, dome is in lockdown'.format(reason))
        elif power.failed:
            raise errors.HardwareStatusError('No external power, dome is in lockdown')
        elif status.emergency_shutdown:
            reasons = ', '.join(status.emergency_shutdown_reasons)
            send_slack_msg('dome_daemon says: someone tried to open dome in emergency state')
            reason = 'In emergency locked state ({})'.format(reasons)
            raise errors.HardwareStatusError('{}, dome is in lockdown'.format(reason))
        elif self.lockdown:
            # This should be covered by the above, but just in case...
            raise errors.HardwareStatusError('Dome is in lockdown'.format(reason))

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
        if bad_idea:
            self.log.warning('Breaking through lockdown')
            self.lockdown = False

        # Set flag
        self.log.info('Starting: Opening dome')
        self.open_flag = 1

        if bad_idea:
            return 'Opening dome, even though conditions are bad! BE CAREFUL'
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
        """Turn the dehumidifier on or off before the automatic command."""
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
            return 'Turning on dehumidifier (the daemon may turn it off again)'
        elif command == 'off':
            return 'Turning off dehumidifier (the daemon may turn it on again)'


if __name__ == "__main__":
    daemon_id = 'dome'
    with misc.make_pid_file(daemon_id):
        DomeDaemon()._run()
