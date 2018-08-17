#!/usr/bin/env python
"""Daemon to control an AstroHaven dome."""

import threading
import time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import HardwareDaemon
from gtecs.flags import Conditions, Power, Status
from gtecs.hardware.dome import AstroHavenDome, Dehumidifier
from gtecs.hardware.dome import FakeDehumidifier, FakeDome
from gtecs.slack import send_slack_msg

import serial


class DomeDaemon(HardwareDaemon):
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
        self.override_dehumid_flag = 0

        # dome variables
        self.dome_timeout = 40.
        self.lockdown = False

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
        self.warnings_check_period = 3  # params.DOME_CHECK_PERIOD

        self.check_conditions_flag = 1
        self.conditions_check_time = 0
        self.conditions_check_period = 60

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

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
        temp_info['timestamp'] = self.loop_time
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
            temp_info['dehumidifier'] = dehumidifier_status
        except Exception:
            self.log.error('Failed to get dehumidifier info')
            self.log.debug('', exc_info=True)
            temp_info['dehumidifier'] = None
            # Report the connection as failed
            self.dehumidifier = None
            if 'dehumidifier' not in self.bad_hardware:
                self.bad_hardware.add('dehumidifier')

        # Get conditions info
        try:
            conditions = Conditions()
            temp_info['conditions_bad'] = bool(conditions.bad)
            temp_info['conditions_reasons'] = conditions.bad_flags
        except Exception:
            self.log.error('Failed to get conditions info')
            self.log.debug('', exc_info=True)
            temp_info['conditions_bad'] = None
            temp_info['conditions_reasons'] = None

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

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    # Primary control thread
    def _control_thread(self):
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

            # autocheck warnings every Y seconds (if not already forced)
            delta = self.loop_time - self.warnings_check_time
            if delta > self.warnings_check_period:
                self.check_warnings_flag = 1

            # check warnings
            if self.check_warnings_flag:
                try:
                    # Get external flags
                    conditions = Conditions()
                    status = Status()

                    # Create emergency file if needed
                    if self._button_pressed(params.QUICK_CLOSE_BUTTON_PORT):
                        self.log.info('Quick close button pressed!')
                        status.create_shutdown_file(['quick close button pressed'])
                    if conditions.critical:
                        flags = conditions.critical_flags
                        self.log.info('Conditions critical ({})'.format(flags))
                        status.create_shutdown_file(conditions._crit_flags)

                    # Act on an emergency
                    if status.emergency_shutdown:
                        self.lockdown = True
                        if (self.info['north'] != 'closed' or
                                self.info['south'] != 'closed'):
                            reasons = ', '.join(status.emergency_shutdown_reasons)
                            self.log.warning('Closing dome (emergency: {})'.format(reasons))
                            if not self.close_flag:
                                reason = '(emergency shutdown: {})'.format(reasons)
                                send_slack_msg('dome_daemon is closing dome {}'.format(reason))
                                if self.open_flag:  # stop opening!
                                    self.halt_flag = 1
                                    time.sleep(2)
                                status.alarm = True  # make sure the alarm sounds
                                self.close_flag = 1
                                self.move_side = 'both'
                                self.move_frac = 1
                    elif conditions.bad:
                        # Don't close in manual mode if autoclose is disabled
                        # NB: Always close in robotic mode
                        if status.mode == 'manual' and not status.autoclose:
                            reason = 'Conditions bad ({})'.format(conditions.bad_flags)
                            but = 'but in manual mode and autoclose disabled!'
                            self.log.warning('{}, {}'.format(reason, but))
                        else:
                            self.lockdown = True
                            if (self.info['north'] != 'closed' or
                                    self.info['south'] != 'closed'):
                                reason = 'Conditions bad ({})'.format(conditions.bad_flags)
                                self.log.warning('{}, auto-closing dome'.format(reason))
                                if not self.close_flag:
                                    if self.open_flag:  # stop opening!
                                        self.halt_flag = 1
                                        time.sleep(2)
                                    status.alarm = True  # make sure the alarm sounds
                                    self.close_flag = 1
                                    self.move_side = 'both'
                                    self.move_frac = 1

                    # Check if we're okay to reopen
                    if self.lockdown:
                        if not conditions.bad and not status.emergency_shutdown:
                            self.log.info('Conditions are clear, lockdown lifted')
                            self.lockdown = False
                        else:
                            self.log.warning('Dome is in lockdown state')

                    self.warnings_check_time = time.time()
                except Exception:
                    self.log.error('check_warnings command failed')
                    self.log.debug('', exc_info=True)
                self.check_warnings_flag = 0

            # autocheck dome conditions every Z seconds (if not already forced)
            delta = self.loop_time - self.conditions_check_time
            if delta > self.conditions_check_period:
                self.check_conditions_flag = 1

            # check dome internal conditions
            if self.check_conditions_flag:
                try:
                    # get current dome conditions
                    conditions = self.dehumidifier.conditions()
                    print(conditions, self.dehumidifier.status())
                    humidity = conditions['humidity']
                    temperature = conditions['temperature']

                    currently_open = (self.info['north'] != 'closed' or
                                      self.info['south'] != 'closed')

                    if self.dehumidifier.status() == '0' and not currently_open:
                        if humidity > params.MAX_INTERNAL_HUMIDITY:
                            string = 'Internal humidity {}% is above {}%'
                            string = string.format(humidity, params.MAX_INTERNAL_HUMIDITY)
                            self.log.info(string)
                        if temperature < params.MIN_INTERNAL_TEMPERATURE:
                            string = 'Internal temperature {}C is below {}C'
                            string = string.format(temperature, params.MIN_INTERNAL_TEMPERATURE)
                            self.log.info(string)
                        if (humidity > params.MAX_INTERNAL_HUMIDITY or
                                temperature < params.MIN_INTERNAL_TEMPERATURE):
                            self.log.info('Turning on dehumidifier')
                            self.dehumidifier.on()
                        elif self.override_dehumid_flag and self.dehumid_command == 'on':
                            self.log.info('Turning on dehumidifier (manual)')
                            self.dehumidifier.on()
                            self.override_dehumid_flag = 0
                            self.dehumid_command = 'none'

                    elif self.dehumidifier.status() == '1' and not currently_open:
                        if (humidity < params.MAX_INTERNAL_HUMIDITY - 10 and
                                temperature > params.MIN_INTERNAL_TEMPERATURE + 1):
                            string = 'Internal humidity {}% is below {}%'
                            string = string.format(humidity, params.MAX_INTERNAL_HUMIDITY - 10)
                            self.log.info(string)
                            string = 'and internal temperature {}C is above {}C'
                            string = string.format(temperature, params.MIN_INTERNAL_TEMPERATURE + 1)
                            self.log.info(string)
                            self.log.info('Turning off dehumidifier')
                            self.dehumidifier.off()
                        elif self.override_dehumid_flag and self.dehumid_command == 'off':
                            self.log.info('Turning off dehumidifier (manual)')
                            self.dehumidifier.off()
                            self.override_dehumid_flag = 0
                            self.dehumid_command = 'none'

                    if self.dehumidifier.status() == '1' and currently_open:
                        self.log.info('Dome is open')
                        self.log.info('Turning off dehumidifier')
                        self.dehumidifier.off()

                    self.conditions_check_time = time.time()
                except Exception:
                    self.log.error('check_humidity command failed')
                    self.log.debug('', exc_info=True)
                self.check_conditions_flag = 0

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
                        self.check_status_flag = 1
                        self.check_warnings_flag = 1

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
                                self.check_status_flag = 1
                            except Exception:
                                self.log.error('Failed to open dome')
                                self.log.debug('', exc_info=True)
                            # make sure dehumidifier is off
                            self.dehumidifier.off()

                    if self.move_started and not self.dome.output_thread_running:
                        # we've finished
                        # check if we timed out
                        if time.time() - self.move_start_time > self.dome_timeout:
                            self.log.info('Moving timed out')
                            self.move_started = 0
                            self.move_side = 'none'
                            self.move_frac = 1
                            self.open_flag = 0
                            self.check_status_flag = 1
                            self.check_warnings_flag = 1
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
                        self.check_status_flag = 1
                        self.check_warnings_flag = 1
                        # whenever the dome is closed, re-enable autoclose
                        status = Status()
                        if (not status.autoclose and
                                self.info['north'] == 'closed' and
                                self.info['south'] == 'closed'):
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
                                self.check_status_flag = 1
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
                            self.check_status_flag = 1
                            self.check_warnings_flag = 1
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
                self.check_status_flag = 1

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Dome control functions
    def get_info(self):
        """Return dome status info."""
        return self.info

    def get_info_simple(self):
        """Return plain status dict, or None."""
        try:
            info = self.get_info()
        except Exception:
            return None
        return info

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

        # We want new commands to overwrite the old ones
        if self.open_flag or self.close_flag:
            self.halt_flag = 1
            time.sleep(3)

        # Check current status
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

        # We want new commands to overwrite the old ones
        if self.open_flag or self.close_flag:
            self.halt_flag = 1
            time.sleep(3)

        # Check current status
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
        dehumid_status = self.info['dehumidifier']
        currently_open = self.info['dome'] != 'closed'
        if command == 'on' and currently_open:
            raise errors.HardwareStatusError("Dome is open, dehumidifier won't turn on")
        elif command == 'on' and dehumid_status == 'on':
            return 'Dehumidifier is already on'
        elif command == 'off' and dehumid_status == 'off':
            return 'Dehumidifier is already off'

        # Set values
        self.dehumid_command = command

        # Set flag
        if command == 'on':
            self.log.info('Turning on dehumidifier (manual command)')
        elif command == 'off':
            self.log.info('Turning off dehumidifier (manual command)')
        self.override_dehumid_flag = 1
        self.check_conditions_flag = 1

        if command == 'on':
            return 'Turning on dehumidifier (the daemon may turn it off again)'
        elif command == 'off':
            return 'Turning off dehumidifier (the daemon may turn it on again)'

    # Internal functions
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


if __name__ == "__main__":
    daemon_id = 'dome'
    with misc.make_pid_file(daemon_id):
        DomeDaemon()._run()
