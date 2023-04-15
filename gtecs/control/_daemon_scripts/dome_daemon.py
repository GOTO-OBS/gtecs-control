#!/usr/bin/env python3
"""Daemon to control an AstroHaven dome."""

import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import errors
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon
from gtecs.control.flags import Conditions, Status
from gtecs.control.hardware.dome import AstroHavenDome, FakeDome
from gtecs.control.hardware.dome import Dehumidifier, ETH002Dehumidifier, FakeDehumidifier
from gtecs.control.hardware.dome import DomeHeartbeat, FakeHeartbeat
from gtecs.control.observing import get_conditions
from gtecs.control.slack import send_slack_msg

import numpy as np

import serial  # noqa: I900


class DomeDaemon(BaseDaemon):
    """Dome hardware daemon class."""

    def __init__(self):
        super().__init__('dome')

        # hardware
        self.dome = None
        self.heartbeat = None
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
        self.autoclosing = False

        self.hatch_open_time = 0

        self.alarm_enabled = True
        self.heartbeat_enabled = True
        self.windshield_enabled = False

        self.autodehum_enabled = True
        self.autoclose_enabled = True
        self.autoshield_enabled = True

        self.autoclose_timeout = None

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

        while self.running:
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

                # Only run checks if not in engineering mode
                if self.info['mode'] != 'engineering':

                    # Check if we need to trigger a lockdown due to conditions
                    self._lockdown_check()

                    # Check if we need to close due to lockdown
                    self._autoclose_check()

                    # Check if we need to turn on/off the dehumidifier
                    self._autodehum_check()

                    if params.DOME_WINDSHIELD_PERMITTED:
                        # Check if we should enable shielding due to high wind
                        self._autoshield_check()

                        # Check if we need to raise the shields
                        self._windshield_check()

            # control functions
            # open dome
            if self.open_flag:
                try:
                    # chose the side to move
                    if self.move_side == 'a_side':
                        side = 'a_side'
                    elif self.move_side == 'b_side':
                        side = 'b_side'
                    elif self.move_side == 'both':
                        side = 'a_side'  # start with 3-shutter side, so top shutter goes first
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
                            self.log.info('The "{}" side is already open'.format(side[0]))
                            if self.move_side == 'both':
                                self.move_side = 'b_side'  # 2-shutter side second
                            else:
                                self.move_side = 'none'
                        # otherwise ready to start moving
                        else:
                            try:
                                self.log.info('Opening "{}" side of dome'.format(side[0]))
                                if params.DOME_HAS_BUMPERGUARD:
                                    self.dome.reset_bumperguard()
                                if self.alarm_enabled:
                                    self._sound_alarm()
                                if params.DOME_HAS_BUMPERGUARD or self.alarm_enabled:
                                    time.sleep(5)

                                c = self.dome.open_side(side, self.move_frac)
                                if c:
                                    self.log.info(c)
                                self.move_started = 1
                                self.move_start_time = time.time()
                                self.force_check_flag = True
                            except Exception:
                                self.log.error('Failed to open dome')
                                self.log.debug('', exc_info=True)

                    elif self.move_started and not self.dome.output_thread_running:
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
                            self.log.info('The "{}" side is open'.format(side[0]))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'b_side'  # 2-shutter side second
                            else:
                                self.move_side = 'none'
                        elif self.move_frac != 1:
                            self.log.info('The "{}" side moved requested fraction'.format(side[0]))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'b_side'  # 2-shutter side second
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
                    if self.move_side == 'a_side':
                        side = 'a_side'
                    elif self.move_side == 'b_side':
                        side = 'b_side'
                    elif self.move_side == 'both':
                        side = 'b_side'  # start with 2-shutter side, then top shutter goes over
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
                            self.log.info('The "{}" side is already closed'.format(side[0]))
                            if self.move_side == 'both':
                                self.move_side = 'a_side'   # 3-shutter side second
                            else:
                                self.move_side = 'none'
                        # otherwise ready to start moving
                        else:
                            try:
                                self.log.info('Closing "{}" side of dome'.format(side[0]))
                                if params.DOME_HAS_BUMPERGUARD:
                                    self.dome.reset_bumperguard()
                                if self.alarm_enabled:
                                    self._sound_alarm()
                                if params.DOME_HAS_BUMPERGUARD or self.alarm_enabled:
                                    time.sleep(5)

                                c = self.dome.close_side(side, self.move_frac)
                                if c:
                                    self.log.info(c)
                                self.move_started = 1
                                self.move_start_time = time.time()
                                self.force_check_flag = True
                            except Exception:
                                self.log.error('Failed to close dome')
                                self.log.debug('', exc_info=True)

                    elif self.move_started and not self.dome.output_thread_running:
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
                            self.log.info('The "{}" side is closed'.format(side[0]))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'a_side'   # 3-shutter side second
                            else:
                                self.move_side = 'none'
                        elif self.move_frac != 1:
                            self.log.info('The "{}" side moved requested fraction'.format(side[0]))
                            self.move_started = 0
                            self.move_start_time = 0
                            if self.move_side == 'both':
                                self.move_side = 'a_side'   # 3-shutter side second
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
                        c = self.heartbeat.enable()
                    else:
                        self.log.info('Disabling heartbeat')
                        c = self.heartbeat.disable()
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
                self.dome = FakeDome(self.log, params.DOME_DEBUG)
                self.log.info('Connected to dome')
            else:
                try:
                    self.dome = AstroHavenDome(params.DOME_LOCATION,
                                               domealert_uri=params.DOMEALERT_URI,
                                               log=self.log,
                                               log_debug=params.DOME_DEBUG,
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

        # Connect to the heartbeat monitor
        if self.heartbeat is None:
            if params.FAKE_DOME:
                self.heartbeat = FakeHeartbeat()
                self.log.info('Connected to heartbeat')
            else:
                try:
                    self.heartbeat = DomeHeartbeat(params.DOME_HEARTBEAT_LOCATION,
                                                   params.DOME_HEARTBEAT_PERIOD,
                                                   self.log,
                                                   params.DOME_DEBUG,
                                                   )
                    self.log.info('Connected to heartbeat')
                    if 'heartbeat' in self.bad_hardware:
                        self.bad_hardware.remove('heartbeat')
                    # sleep briefly, to make sure the connection has started
                    time.sleep(3)
                except Exception:
                    self.heartbeat.disconnect()
                    self.heartbeat = None
                    if 'heartbeat' not in self.bad_hardware:
                        self.log.error('Failed to connect to heartbeat')
                        self.bad_hardware.add('heartbeat')

        # Check the device connections
        if self.dome is not None:
            if self.dome.plc_error:
                self.log.error('Failed to connect to dome PLC')
                self.dome.disconnect()
                self.dome = None
                self.bad_hardware.add('dome')
            if self.dome.switch_error and not params.DOME_IGNORE_SWITCH_ERRORS:
                self.log.error('Failed to connect to dome switches')
                self.dome.disconnect()
                self.dome = None
                self.bad_hardware.add('dome')
            if ((not self.dome.status_thread_running) or
                    (time.time() - self.dome.status_update_time) > params.DOME_CHECK_PERIOD):
                self.log.error('Failed to check dome status')
                if params.DOME_DEBUG:
                    msg = 'running={}, delta={}/{}'.format(
                        self.dome.status_thread_running,
                        (time.time() - self.dome.status_update_time),
                        params.DOME_CHECK_PERIOD)
                    self.log.debug(msg)
                self.dome.disconnect()
                self.dome = None
                self.bad_hardware.add('dome')
        if self.heartbeat is not None:
            if self.heartbeat.connection_error:
                self.log.error('Failed to connect to dome heartbeat monitor')
                self.heartbeat.disconnect()
                self.heartbeat = None
                self.bad_hardware.add('heartbeat')

        # Connect to the dehumidifer
        if self.dehumidifier is None:
            if params.FAKE_DOME:
                self.dehumidifier = FakeDehumidifier()
                self.log.info('Connected to dehumidifier')
            elif 'DEHUMIDIFIER' in params.POWER_UNITS:
                try:
                    dehumidifier_address = params.POWER_UNITS['DEHUMIDIFIER']['IP']
                    dehumidifier_port = int(params.POWER_UNITS['DEHUMIDIFIER']['PORT'])
                    self.dehumidifier = ETH002Dehumidifier(dehumidifier_address, dehumidifier_port)
                    self.log.info('Connected to dehumidifier')
                    if 'dehumidifier' in self.bad_hardware:
                        self.bad_hardware.remove('dehumidifier')
                except Exception:
                    self.dehumidifier = None
                    if 'dehumidifier' not in self.bad_hardware:
                        self.log.exception('Failed to connect to dehumidifier')
                        self.bad_hardware.add('dehumidifier')
            else:
                try:
                    self.dehumidifier = Dehumidifier(params.DOMEALERT_URI)
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
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from the dome
        try:
            dome_status = self.dome.status
            temp_info['a_side'] = dome_status['a_side']
            temp_info['b_side'] = dome_status['b_side']
            temp_info['hatch'] = dome_status['hatch']
            temp_info['status_update_time'] = self.dome.status_update_time

            # general, backwards-compatible open/closed
            if ('open' in temp_info['a_side']) or ('open' in temp_info['b_side']):
                temp_info['dome'] = 'open'
            elif (temp_info['a_side'] == 'closed') and (temp_info['b_side'] == 'closed'):
                temp_info['dome'] = 'closed'
            else:
                temp_info['dome'] = 'ERROR'

            # hatch info
            temp_info['hatch_closed'] = temp_info['hatch'] == 'closed'
            temp_info['hatch_open_time'] = self.hatch_open_time

            # heartbeat info
            heartbeat_status = self.heartbeat.status
            temp_info['heartbeat_status'] = heartbeat_status
        except Exception:
            self.log.error('Failed to get dome info')
            self.log.debug('', exc_info=True)
            temp_info['a_side'] = None
            temp_info['b_side'] = None
            temp_info['hatch'] = None
            temp_info['dome'] = None
            temp_info['hatch_closed'] = None
            temp_info['hatch_open_time'] = None
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
        except Exception:
            self.log.error('Failed to get dehumidifier info')
            self.log.debug('', exc_info=True)
            temp_info['dehumidifier_on'] = None
            # Report the connection as failed
            self.dehumidifier = None
            if 'dehumidifier' not in self.bad_hardware:
                self.bad_hardware.add('dehumidifier')

        # Get the conditions values and limits
        try:
            conditions = get_conditions(timeout=10)
            # Windspeed - take the maximum gust from all stations
            temp_info['windspeed'] = np.max([conditions['weather'][source]['windmax']
                                             for source in conditions['weather']])
            # Internal
            temp_info['temperature'] = conditions['internal']['temperature']
            temp_info['humidity'] = conditions['internal']['humidity']
        except Exception:
            self.log.error('Failed to fetch conditions')
            self.log.debug('', exc_info=True)
            temp_info['windspeed'] = None
            temp_info['temperature'] = None
            temp_info['humidity'] = None
        temp_info['windspeed_upper'] = params.SHIELD_WINDGUST
        temp_info['windspeed_lower'] = params.SHIELD_WINDGUST - 5
        temp_info['temperature_lower'] = params.MIN_INTERNAL_TEMPERATURE
        temp_info['temperature_upper'] = params.MIN_INTERNAL_TEMPERATURE + 1
        temp_info['humidity_upper'] = params.MAX_INTERNAL_HUMIDITY
        temp_info['humidity_lower'] = params.MAX_INTERNAL_HUMIDITY - 10

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
            if self.info is not None and 'mode' in self.info:
                temp_info['old_mode'] = self.info['mode']
            else:
                temp_info['old_mode'] = status.mode
        except Exception:
            self.log.error('Failed to get status info')
            self.log.debug('', exc_info=True)
            temp_info['emergency'] = None
            temp_info['emergency_time'] = None
            temp_info['emergency_reasons'] = None
            temp_info['mode'] = None
            temp_info['old_mode'] = None

        # Get other internal info
        temp_info['last_move_time'] = self.last_move_time
        temp_info['shielding'] = self.shielding
        temp_info['lockdown'] = self.lockdown
        temp_info['lockdown_reasons'] = self.lockdown_reasons
        temp_info['ignoring_lockdown'] = self.ignoring_lockdown
        temp_info['autoclosing'] = self.autoclosing
        temp_info['alarm_enabled'] = self.alarm_enabled
        temp_info['heartbeat_enabled'] = self.heartbeat_enabled
        temp_info['windshield_enabled'] = self.windshield_enabled
        temp_info['autodehum_enabled'] = self.autodehum_enabled
        temp_info['autoclose_enabled'] = self.autoclose_enabled
        temp_info['autoshield_enabled'] = self.autoshield_enabled
        temp_info['autoclose_timeout'] = self.autoclose_timeout

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
                self.autoclose_timeout = None
            if not self.autoshield_enabled:
                self.log.info('System is in robotic mode, enabling autoshield')
                self.autoshield_enabled = True

        elif self.info['mode'] == 'manual':
            # In manual mode the heartbeat should always be enabled,
            # everything else should turn on when manual mode is enabled
            # but can then be turned off if desired
            if not self.heartbeat_enabled:
                self.log.info('System is in manual mode, enabling heartbeat')
                self.heartbeat_enabled = True
                self.heartbeat_set_flag = 1
            if self.info['old_mode'] != 'manual':
                # This will turn everything on when switching from engineering to manual
                # (if we're switching from robotic they should all be on anyway!)
                if not self.alarm_enabled:
                    self.log.info('System is in manual mode, enabling alarm')
                    self.alarm_enabled = True
                if not self.autodehum_enabled:
                    self.log.info('System is in manual mode, enabling autodehum')
                    self.autodehum_enabled = True
                if not self.autoclose_enabled:
                    self.log.info('System is in manual mode, enabling autoclose')
                    self.autoclose_enabled = True
                    self.autoclose_timeout = None
                if not self.autoshield_enabled:
                    self.log.info('System is in manual mode, enabling autoshield')
                    self.autoshield_enabled = True

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
                self.autoclose_timeout = None

            if self.windshield_enabled or self.autoshield_enabled or self.shielding:
                self.log.info('System is in engineering mode, disabling windshielding')
                self.windshield_enabled = False
                self.autoshield_enabled = False
                self.shielding = False

    def _lockdown_check(self):
        """Check the current conditions and set or clear the lockdown flag."""
        lockdown = False
        reasons = []

        # Safety check: ignore lockdowns in engineering mode
        if self.info['mode'] == 'engineering':
            return

        # Check if the quick-close button has been pressed
        if self.info['button_pressed']:
            lockdown = True
            reason = 'quick-close button pressed'
            reasons.append(reason)
            if reason not in self.lockdown_reasons:
                send_slack_msg('Dome quick-close button has been pressed!')

        # Check if the hatch is open in robotic mode
        if not self.info['hatch_closed']:
            if self.hatch_open_time == 0:
                self.hatch_open_time = self.loop_time
            if (self.info['mode'] == 'robotic' and
                    (self.loop_time - self.hatch_open_time) > params.HATCH_OPEN_DELAY):
                lockdown = True
                reason = 'hatch open in robotic mode'
                reasons.append(reason)
                if reason not in self.lockdown_reasons:
                    send_slack_msg('Dome hatch is open in robotic mode!')
        else:
            if self.hatch_open_time != 0:
                self.hatch_open_time = 0

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
                if self.autoclose_timeout is not None:
                    delta = self.autoclose_timeout - self.loop_time
                    self.log.warning(f'Autoclose will reactivate in {delta:.1f} seconds')
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

        # Safety check: never move in engineering mode
        if self.info['mode'] == 'engineering':
            return

        # Return if autoclose disabled
        if not self.autoclose_enabled:
            # Check timeout (if set)
            if self.autoclose_timeout is None:
                return
            else:
                if self.loop_time < self.autoclose_timeout:
                    # Return if a timeout is set and hasn't been exceeded yet
                    return
                else:
                    self.log.warning('Autoclose timeout exceeded, turning autoclose on')
                    self.autoclose_enabled = True
                    self.autoclose_timeout = None

        # Decide if we need to autoclose
        if self.lockdown and self.info['dome'] != 'closed' and not self.close_flag:
            self.log.warning('Autoclosing dome due to lockdown')
            # Stop any opening
            if self.open_flag:
                self.log.warning('Stopping opening')
                # We can't use the halt flag since that would clear our close flag!
                try:
                    self.dome.halt()
                except Exception:
                    self.log.error('Failed to halt dome')
                    self.log.debug('', exc_info=True)
                self.open_flag = 0
            # Make sure the alarm sounds, since we're moving automatically
            self.alarm_enabled = True
            # Close the dome
            self.log.warning('Closing the dome')
            self.close_flag = 1
            self.move_side = 'both'
            self.move_frac = 1
            self.autoclosing = True
            # Now send message to Slack, at the end so we don't delay anything
            send_slack_msg('Dome is autoclosing: {}'.format('; '.join(self.lockdown_reasons)))

        # Check if autoclose has finished
        if self.autoclosing and self.info['dome'] == 'closed':
            self.log.warning('Autoclose complete')
            send_slack_msg('Autoclose complete')
            self.autoclosing = False

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

        # Safety check: never switch automatically in engineering mode
        if self.info['mode'] == 'engineering':
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

    def _autoshield_check(self):
        """Check if the wind is high and the dome should be in windshield mode."""
        if not self.dome:
            self.log.warning('Autoshield disabled while no connection to dome')
            return

        if not params.DOME_WINDSHIELD_PERMITTED:
            # windshield mode is disabled system-wide
            return

        if (isinstance(self.info, dict) and
                (self.info.get('windspeed') is None or self.info['windspeed'] == -999)):
            # Note dict.get() returns None if it is not in the dictionary
            self.log.warning('No windspeed reading: auto windshield control unavailable')
            return

        # Safety check: never move in engineering mode
        if self.info['mode'] == 'engineering':
            return

        # Return if autoshield disabled
        if not self.autoshield_enabled:
            return

        # Check the windspeed and decide if we need to enable or disable windshield mode
        if not self.windshield_enabled and self.info['windspeed'] > self.info['windspeed_upper']:
            self.log.info('Windspeed {} km/h > {} km/h'.format(self.info['windspeed'],
                                                               self.info['windspeed_upper']))
            self.log.info('Turning windshielding on')
            self.windshield_enabled = True
        elif self.windshield_enabled and self.info['windspeed'] < self.info['windspeed_lower']:
            self.log.info('Windspeed {} km/h < {} km/h'.format(self.info['windspeed'],
                                                               self.info['windspeed_lower']))
            self.log.info('Turning windshielding off')
            self.windshield_enabled = False

    def _windshield_check(self):
        """Check if the dome is open and needs to raise shields."""
        if not self.dome:
            self.log.warning('Shielding disabled while no connection to dome')
            return

        if not params.DOME_WINDSHIELD_PERMITTED:
            # windshield mode is disabled system-wide
            return

        # Safety check: never move in engineering mode
        if self.info['mode'] == 'engineering':
            return

        # Check if we are currently shielding
        if (self.shielding and
                self.info['a_side'] in ['full_open', 'closed'] and
                self.info['b_side'] in ['full_open', 'closed'] and
                not self.open_flag and not self.close_flag):
            # The dome must have moved some other way, either manually or via autoclose
            # Or the flag has just been set and it hasn't started moving yet (the alarm is going)
            self.log.warning('Disabling shielding flag')
            self.shielding = False

        # Decide if we need to raise or lower shields
        if (self.windshield_enabled and
                (self.info['a_side'] == 'full_open' or self.info['b_side'] == 'full_open') and
                not self.open_flag and not self.close_flag):
            self.log.warning('Moving dome shutters to windshield position')
            self.shielding = True
            # Make sure the alarm sounds, since we're moving automatically
            self.alarm_enabled = True
            # Partially close the dome
            self.close_flag = 1
            self.move_side = 'both'
            self.move_frac = params.DOME_WINDSHIELD_POSITION

        elif (self.shielding and not self.windshield_enabled and
              (self.info['a_side'] == 'part_open' or self.info['b_side'] == 'part_open') and
              not self.open_flag and not self.close_flag):
            self.log.warning('Moving dome shutters to full open')
            self.shielding = False
            # Make sure the alarm sounds, since we're moving automatically
            self.alarm_enabled = True
            # Fully open the dome
            self.open_flag = 1
            self.move_side = 'both'
            self.move_frac = 1

    def _sound_alarm(self):
        """Sound the dome siren."""
        if not self.alarm_enabled:
            # TODO: we should have a separate override for automatic moves
            return

        # Sound the alarm through the heartbeat box
        # Note the heartbeat siren always sounds for 5s
        self.heartbeat.sound_alarm()

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
        if side not in ['a_side', 'b_side', 'both']:
            raise ValueError('Side must be one of "a_side", "b_side" or "both"')
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
        a_side_status = self.info['a_side']
        b_side_status = self.info['b_side']
        if side == 'a_side' and a_side_status == 'full_open':
            return 'The "a" side is already fully open'
        elif side == 'b_side' and b_side_status == 'full_open':
            return 'The "b"" side is already fully open'
        elif side == 'both':
            if a_side_status == 'full_open' and b_side_status == 'full_open':
                return 'The dome is already fully open'
            elif a_side_status == 'full_open' and b_side_status != 'full_open':
                side = 'b_side'
            elif a_side_status != 'full_open' and b_side_status == 'full_open':
                side = 'a_side'

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
        if side not in ['a_side', 'b_side', 'both']:
            raise ValueError('Side must be one of "a_side", "b_side" or "both"')
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
        a_side_status = self.info['a_side']
        b_side_status = self.info['b_side']
        if side == 'a_side' and a_side_status == 'closed':
            return 'The "a" side is already fully closed'
        elif side == 'b_side' and b_side_status == 'closed':
            return 'The "b" side is already fully closed'
        elif side == 'both':
            if a_side_status == 'closed' and b_side_status == 'closed':
                return 'The dome is already fully closed'
            elif a_side_status != 'closed' and b_side_status == 'closed':
                side = 'a_side'
            elif a_side_status == 'closed' and b_side_status != 'closed':
                side = 'b_side'

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

    def set_autoclose(self, command, timeout=None):
        """Enable or disable the dome autoclosing in bad conditions."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")
        if timeout is not None and not isinstance(timeout, (int, float)):
            raise ValueError("Timeout must be a number (time in seconds)")

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
            elif not self.autoclose_enabled and timeout == self.autoclose_timeout:
                return 'Autoclose is already disabled'

        # Set flag
        if command == 'on':
            self.log.info('Enabling autoclose')
            self.autoclose_enabled = True
            self.autoclose_timeout = None
        elif command == 'off':
            msg = 'Disabling autoclose'
            if timeout is not None:
                msg += f' for {timeout / 60:.1f} minutes'
            self.log.info(msg)
            self.autoclose_enabled = False
            if timeout is not None:
                self.autoclose_timeout = time.time() + timeout
            else:
                self.autoclose_timeout = None

        if command == 'on':
            return 'Enabling autoclose, dome will close in bad conditions'
        elif command == 'off':
            msg = 'Disabling autoclose'
            if timeout is not None:
                msg += f' for {timeout / 60:.1f} minutes'
            msg += ', dome will NOT close in bad conditions'
            return msg

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

    def sound_alarm(self):
        """Sound the dome alarm."""
        # Check current status
        self.wait_for_info()
        if not self.alarm_enabled:
            raise errors.HardwareStatusError('Alarm is disabled')

        # Just call the internal command
        self._sound_alarm()

    def reset_bumperguard(self):
        """Reset the dome bumper guard."""
        if not params.DOME_HAS_BUMPERGUARD:
            raise errors.HardwareStatusError('Dome does not have a bumper guard to reset')

        self.dome.reset_bumperguard()

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
            raise errors.HardwareStatusError('Cannot disable heartbeat in robotic mode')

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
        if command == 'on' and self.info['mode'] == 'engineering':
            raise errors.HardwareStatusError('Cannot enable windshielding in engineering mode')
        windshield_enabled = self.info['windshield_enabled']
        if command == 'on' and windshield_enabled:
            return 'Windshielding is already enabled'
        elif command == 'off' and not windshield_enabled:
            return 'Windshielding is already disabled'
        elif command == 'on' and not params.DOME_WINDSHIELD_PERMITTED:
            return 'Windshielding is disabled system-wide (DOME_WINDSHIELD_PERMITTED = False)'

        # Set flag
        if command == 'on':
            self.log.info('Enabling windshield mode (manual command)')
            self.windshield_enabled = True
        elif command == 'off':
            self.log.info('Disabling windshield mode (manual command)')
            self.windshield_enabled = False

        if command == 'on':
            s = 'Enabling windshield mode'
            if self.autoshield_enabled:
                s += ' (autoshield is enabled, so the daemon may turn it off again)'
            return s
        elif command == 'off':
            s = 'Disabling windshield mode'
            if self.autoshield_enabled:
                s += ' (autoshield is enabled, so the daemon may turn it on again)'
            return s

    def set_autoshield(self, command):
        """Enable or disable the dome automatically raising shields in high wind."""
        # Check input
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        # Check current status
        self.wait_for_info()
        if command == 'on':
            if self.info['mode'] == 'engineering':
                raise errors.HardwareStatusError('Cannot enable autoshield in engineering mode')
            elif not params.DOME_WINDSHIELD_PERMITTED:
                return 'Windshielding is disabled system-wide (DOME_WINDSHIELD_PERMITTED = False)'
            elif self.autoshield_enabled:
                return 'Autoshield is already enabled'
        else:
            if self.info['mode'] == 'robotic':
                raise errors.HardwareStatusError('Cannot disable autoshield in robotic mode')
            elif not self.autoshield_enabled:
                return 'Autoshield is already disabled'

        # Set flag
        if command == 'on':
            self.log.info('Enabling autoshield')
            self.autoshield_enabled = True
        elif command == 'off':
            self.log.info('Disabling autoshield')
            self.autoshield_enabled = False

        if command == 'on':
            return 'Enabling autoshield, the dome will raise and lower shields automatically'
        elif command == 'off':
            return 'Disabling autoshield, the dome will NOT raise and lower shields automatically'


if __name__ == '__main__':
    with make_pid_file('dome'):
        DomeDaemon()._run()
