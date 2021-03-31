"""Classes to control telescope domes and dehumidifiers."""

import json
import os
import subprocess
import threading
import time

import serial

from .power import ETH002
from .. import params


class FakeDome(object):
    """Fake AstroHaven dome class."""

    def __init__(self, dome_port, heartbeat_port=None, log=None, log_debug=False):
        self.fake = True
        self.dome_serial_port = dome_port
        self.heartbeat_serial_port = heartbeat_port
        self.output_thread_running = False
        self.side = ''
        self.frac = 1
        self.command = ''
        self.timeout = 0

        self.log = log
        self.log_debug = log_debug

        self.plc_error = False
        self.arduino_error = False

        self.heartbeat_status = 'enabled'
        self.heartbeat_enabled = True
        self.heartbeat_error = False

        # fake stuff
        self._temp_file = '/tmp/dome'
        self._status_arr = [0, 0, 0]
        self._writing = False  # had problems with reading and writing overlapping

        self._read_temp()

    def _read_temp(self):
        while self._writing:
            time.sleep(0.1)
        if not os.path.exists(self._temp_file):
            self._status_arr = [0, 0, 0]
            self._write_temp()
        else:
            with open(self._temp_file, 'r') as f:
                string = f.read().strip()
                if self.log and self.log_debug:
                    self.log.debug('RECV:"{}"'.format(string))
                if not string == '':  # I don't know why or how that happens
                    self._status_arr = list(map(int, list(string)))

    def _write_temp(self):
        self._writing = True
        with open(self._temp_file, 'w') as f:
            string = ''.join(str(i) for i in self._status_arr)
            if self.log and self.log_debug:
                self.log.debug('SEND:"{}"'.format(string))
            f.write(string)
        self._writing = False

    @property
    def status(self):
        """Return the current status of the dome."""
        return self._check_status()

    def _check_status(self):
        status = {'north': 'ERROR', 'south': 'ERROR', 'hatch': 'ERROR'}
        self._read_temp()
        # north
        if self._status_arr[0] == 0:
            status['north'] = 'closed'
        elif self._status_arr[0] == 9:
            status['north'] = 'full_open'
        elif self.output_thread_running and self.command == 'open' and self.side == 'north':
            status['north'] = 'opening'
        elif self.output_thread_running and self.command == 'close' and self.side == 'north':
            status['north'] = 'closing'
        else:
            status['north'] = 'part_open'

        # south
        if self._status_arr[1] == 0:
            status['south'] = 'closed'
        elif self._status_arr[1] == 9:
            status['south'] = 'full_open'
        elif self.output_thread_running and self.command == 'open' and self.side == 'south':
            status['south'] = 'opening'
        elif self.output_thread_running and self.command == 'close' and self.side == 'south':
            status['south'] = 'closing'
        else:
            status['south'] = 'part_open'

        # hatch (never actually opens...)
        if self._status_arr[2] == 0:
            status['hatch'] = 'closed'
        else:
            status['hatch'] = 'open'

        return status

    def halt(self):
        """Stop the dome moving."""
        self.output_thread_running = False

    def set_heartbeat(self, command):
        """Enable or disable the heartbeat."""
        if command:
            if self.heartbeat_enabled:
                return 'Heartbeat already enabled'
            else:
                self.heartbeat_enabled = True
                self.heartbeat_status = 'enabled'
                return 'Heartbeat enabled'
        else:
            if not self.heartbeat_enabled:
                return 'Heartbeat already disabled'
            else:
                self.heartbeat_enabled = False
                self.heartbeat_status = 'disabled'
                return 'Heartbeat disabled'

    def _output_thread(self):
        if self.side == 'north':
            side = 0
        elif self.side == 'south':
            side = 1
        else:
            raise ValueError('Invalid side: {}'.format(self.side))
        frac = self.frac
        command = self.command
        timeout = self.timeout
        start_time = time.time()

        self._read_temp()
        start_position = self._status_arr[side]

        while self.output_thread_running:
            # store running time for timeout
            running_time = time.time() - start_time

            # check reasons to break out and stop the thread
            if command == 'open' and self._status_arr[side] == 9:
                if self.log:
                    self.log.info('Dome at limit')
                self.output_thread_running = 0
                break
            elif command == 'close' and self._status_arr[side] == 0:
                if self.log:
                    self.log.info('Dome at limit')
                self.output_thread_running = 0
                break
            elif (frac != 1 and
                  abs(start_position - self._status_arr[side]) > frac * 9):
                if self.log:
                    self.log.info('Dome moved requested fraction')
                self.output_thread_running = 0
                break
            elif running_time > timeout:
                if self.log:
                    self.log.info('Dome moving timed out')
                self.output_thread_running = 0
                break
            elif self.status[self.side] == 'ERROR':
                if self.log:
                    self.log.warning('All sensors failed, stopping movement')
                self.output_thread_running = 0
                break

            # if we're still going, send the command to "the serial port"
            if command == 'open':
                self._status_arr[side] += 1
                self._write_temp()
                time.sleep(3)
            elif command == 'close':
                self._status_arr[side] -= 1
                self._write_temp()
                time.sleep(3)

            time.sleep(0.5)

        # finished moving for whatever reason, reset before exiting
        self.side = ''
        self.command = ''
        self.timeout = 40.

    def _move_dome(self, side, command, frac, timeout=40.):
        self.side = side
        self.frac = frac
        self.command = command
        self.timeout = timeout

        # Don't interupt!
        if self.status[side] in ['opening', 'closing']:
            return

        # start output thread
        if not self.output_thread_running:
            if self.log:
                self.log.info('starting to move:', side, command, frac)
            self.output_thread_running = 1
            ot = threading.Thread(target=self._output_thread)
            ot.daemon = True
            ot.start()
            return

    def open_side(self, side, frac=1, sound_alarm=True):
        """Open one side of the dome."""
        if sound_alarm:
            self.sound_alarm()
        self._move_dome(side, 'open', frac)
        return

    def close_side(self, side, frac=1, sound_alarm=True):
        """Close one side of the dome."""
        if sound_alarm:
            self.sound_alarm()
        self._move_dome(side, 'close', frac)
        return

    def sound_alarm(self, duration=params.DOME_ALARM_DURATION):
        """Sound the dome alarm."""
        # Note this is always blocking
        bell = 'play -qn --channels 1 synth {} sine 440 vol 0.1'.format(duration)
        subprocess.getoutput(bell)
        return


class AstroHavenDome(object):
    """New AstroHaven dome class (based on Warwick 1m control)."""

    def __init__(self, dome_port, heartbeat_port=None, log=None, log_debug=False):
        self.dome_serial_port = dome_port
        self.dome_serial_baudrate = 9600
        self.dome_serial_timeout = 1

        self.heartbeat_serial_port = heartbeat_port
        self.heartbeat_serial_baudrate = 9600
        self.heartbeat_serial_timeout = 1

        self.move_code = {'south': {'open': b'a', 'close': b'A'},
                          'north': {'open': b'b', 'close': b'B'}}
        self.move_time = {'south': {'open': 36., 'close': 26.},
                          'north': {'open': 24., 'close': 24.}}

        self.fake = False

        self.log = log
        self.log_debug = log_debug

        self.status = None

        self.plc_status = {'north': 'ERROR', 'south': 'ERROR'}
        self.old_plc_status = None
        self.plc_error = False

        self.arduino_status = {'north': 'ERROR', 'south': 'ERROR', 'hatch': 'ERROR'}
        self.old_arduino_status = None
        self.arduino_error = False

        self.honeywell_was_triggered = {'north': 0, 'south': 0}

        self.heartbeat_enabled = True
        self.heartbeat_timeout = params.DOME_HEARTBEAT_PERIOD
        self.heartbeat_status = 'ERROR'
        self.old_heartbeat_status = None
        self.heartbeat_error = False

        self.side = ''
        self.frac = 1
        self.command = ''
        self.timeout = 40.

        self.output_thread_running = 0
        self.status_thread_running = 0
        self.heartbeat_thread_running = 0

        # serial connection to the dome
        self.dome_serial = serial.Serial(self.dome_serial_port,
                                         baudrate=self.dome_serial_baudrate,
                                         timeout=self.dome_serial_timeout)
        # start thread
        self._check_status()

        # serial connection to the dome monitor box
        if self.heartbeat_serial_port:
            try:
                self.heartbeat_serial = serial.Serial(self.heartbeat_serial_port,
                                                      baudrate=self.heartbeat_serial_baudrate,
                                                      timeout=self.heartbeat_serial_timeout)
                # start thread
                self.heartbeat_thread_running = 1
                ht = threading.Thread(target=self._heartbeat_thread)
                ht.daemon = True
                ht.start()
            except Exception:
                if self.log:
                    self.log.error('Error connecting to heartbeat monitor')
                    self.log.debug('', exc_info=True)
                self.heartbeat_error = True
                self.heartbeat_status = 'ERROR'
        else:
            self.heartbeat_status = 'disabled'

    def __del__(self):
        try:
            self.dome_serial.close()
        except AttributeError:
            pass

    def _read_plc(self):
        try:
            if self.dome_serial.in_waiting:
                out = self.dome_serial.read(self.dome_serial.in_waiting)
                x = out.decode('ascii')[-1]
                if self.log and self.log_debug:
                    self.log.debug('plc RECV:"{}"'.format(x))
                self._parse_plc_status(x)
        except Exception:
            if self.log:
                self.log.error('Error communicating with the PLC')
                self.log.debug('', exc_info=True)
                self.log.debug('Previous status: {}'.format(self.old_plc_status))
            self.plc_error = True
            self.plc_status['north'] = 'ERROR'
            self.plc_status['south'] = 'ERROR'

    def _parse_plc_status(self, status_character):
        # save previous status
        self.old_plc_status = self.plc_status.copy()
        # Non-moving statuses
        # returned when we're NOT sending command bytes
        if status_character == '0':
            self.plc_status['north'] = 'closed'
            self.plc_status['south'] = 'closed'
        elif status_character == '1':
            self.plc_status['north'] = 'part_open'
            self.plc_status['south'] = 'closed'
        elif status_character == '2':
            self.plc_status['north'] = 'closed'
            self.plc_status['south'] = 'part_open'
        elif status_character == '3':
            self.plc_status['north'] = 'part_open'
            self.plc_status['south'] = 'part_open'
        # Moving statuses
        # returned when we ARE sending command bytes
        elif status_character == 'a':
            self.plc_status['south'] = 'opening'
        elif status_character == 'A':
            self.plc_status['south'] = 'closing'
        elif status_character == 'b':
            self.plc_status['north'] = 'opening'
        elif status_character == 'B':
            self.plc_status['north'] = 'closing'
        elif status_character == 'x':
            self.plc_status['south'] = 'full_open'
        elif status_character == 'X':
            self.plc_status['south'] = 'closed'
        elif status_character == 'y':
            self.plc_status['north'] = 'full_open'
        elif status_character == 'Y':
            self.plc_status['north'] = 'closed'
        else:
            raise ValueError('Unable to parse reply from the PLC: {}'.format(status_character))

    def _read_arduino(self):
        loc = params.ARDUINO_LOCATION
        try:
            arduino = subprocess.getoutput('curl -s {}'.format(loc))
            data = json.loads(arduino)
            if self.log and self.log_debug:
                self.log.debug('arduino RECV:"{}"'.format(data))
            self._parse_arduino_status(data)
        except Exception:
            if self.log:
                self.log.error('Error communicating with the arduino')
                self.log.debug('', exc_info=True)
                self.log.debug('Previous status: {}'.format(self.old_arduino_status))
            self.arduino_error = True
            self.arduino_status['north'] = 'ERROR'
            self.arduino_status['south'] = 'ERROR'
            self.arduino_status['hatch'] = 'ERROR'

    def _parse_arduino_status(self, status_dict):
        # save previous status
        self.old_arduino_status = self.arduino_status.copy()
        try:
            assert status_dict['switch_a'] in [0, 1]
            assert status_dict['switch_b'] in [0, 1]
            assert status_dict['switch_c'] in [0, 1]
            assert status_dict['switch_d'] in [0, 1]

            all_closed = status_dict['switch_a']
            north_open = status_dict['switch_b']
            south_open = status_dict['switch_c']
            hatch_closed = status_dict['switch_d']

            if all_closed:
                if not north_open:
                    self.arduino_status['north'] = 'closed'
                else:
                    self.arduino_status['north'] = 'ERROR'
                if not south_open:
                    self.arduino_status['south'] = 'closed'
                else:
                    self.arduino_status['south'] = 'ERROR'
            else:
                if north_open:
                    self.arduino_status['north'] = 'full_open'
                else:
                    self.arduino_status['north'] = 'part_open'
                if south_open:
                    self.arduino_status['south'] = 'full_open'
                else:
                    self.arduino_status['south'] = 'part_open'

            if hatch_closed:
                self.arduino_status['hatch'] = 'closed'
            else:
                self.arduino_status['hatch'] = 'open'

            # the Honeywells need memory, in case the built-in
            # sensors fail again

            # NOTE
            # THIS LOGIC HASN'T BEEN TESTED
            # YOU'D HAVE TO DISENGAGE THE DOME LIMIT SWITCHES
            # OR WAIT FOR IT TO HAPPEN
            # FINGERS CROSSED

            for side in ['north', 'south']:
                # find the current status
                if side == 'north':
                    honeywell_triggered = north_open
                else:
                    honeywell_triggered = south_open

                # if the honeywell is triggered now, store it
                if honeywell_triggered:
                    self.honeywell_was_triggered[side] = 1

                # but if it's not currently triggered,
                # it might have gone past
                else:
                    if self.honeywell_was_triggered[side]:
                        if self.plc_status[side] == 'opening':
                            # Oh dear, it's flicked past the Honeywells
                            # and it's still going!!
                            if self.log:
                                self.log.warning('Honeywell limit error, stopping!')
                            self.arduino_status[side] == 'full_open'
                            self.output_thread_running = 0  # to be sure
                        else:
                            # It's moving back, clear the memory
                            self.honeywell_was_triggered[side] = 0

        except Exception:
            raise ValueError('Unable to parse reply from the arduino: {}'.format(status_dict))

    def _read_status(self):
        """Check the dome status reported by both the dome plc and the arduino."""
        # check plc
        self._read_plc()

        # check arduino
        self._read_arduino()

        if self.log and self.log_debug:
            self.log.debug('plc:{} arduino:{}'.format(self.plc_status, self.arduino_status))

        status = {}

        # Only the arduino reports the hatch
        status['hatch'] = self.arduino_status['hatch']

        # dome logic
        for side in ['north', 'south']:
            plc_status = self.plc_status[side]
            arduino_status = self.arduino_status[side]

            # Chose which dome status to report
            if plc_status == arduino_status:
                # arbitrary
                status[side] = plc_status
            elif plc_status == 'ERROR' and arduino_status != 'ERROR':
                # go with the one that is still working
                status[side] = arduino_status
            elif arduino_status == 'ERROR' and plc_status != 'ERROR':
                # go with the one that is still working
                status[side] = plc_status
            elif plc_status[-3:] == 'ing':
                if arduino_status == 'part_open':
                    # arduino can't tell if it's moving
                    status[side] = plc_status
                else:  # closed or full_open
                    # arduino says it's reached the limit,
                    # but it hasn't stopped!!
                    status[side] = arduino_status
            elif plc_status == 'part_open':
                # arduino says closed or full_open
                status[side] = arduino_status
            elif arduino_status == 'part_open':
                # plc says closed or full_open
                status[side] = plc_status
            else:
                # if one says closed and the other says full_open
                # or something totally unexpected
                status[side] = 'ERROR'
        return status

    def _check_status(self):
        # start status check thread
        self.status_thread_running = 1
        st = threading.Thread(target=self._status_thread)
        st.daemon = True
        st.start()

    def _status_thread(self):
        while self.status_thread_running:
            self.status = self._read_status()
            if self.output_thread_running:
                time.sleep(0.5)
            else:
                time.sleep(2)

    def _parse_heartbeat_status(self, status_character):
        # save previous status
        self.old_heartbeat_status = self.heartbeat_status
        # parse value from heartbeat box
        if status_character == 254:
            self.heartbeat_status = 'closing'
        elif status_character == 255:
            self.heartbeat_status = 'closed'
        elif status_character == 0:
            self.heartbeat_status = 'disabled'
        elif 0 < status_character < 254:
            self.heartbeat_status = 'enabled'
        else:
            raise ValueError('Unable to parse reply from the heartbeat monitor: {}'.format(
                status_character))
        return

    def _read_heartbeat(self):
        try:
            if self.heartbeat_serial.in_waiting:
                out = self.heartbeat_serial.read(self.heartbeat_serial.in_waiting)
                x = out[-1]
                if self.log and self.log_debug:
                    self.log.debug('heartbeat RECV:"{}"'.format(x))
                self._parse_heartbeat_status(x)
        except Exception:
            if self.log:
                self.log.error('Error communicating with the heartbeat monitor')
                self.log.debug('', exc_info=True)
                self.log.debug('Previous status: {}'.format(self.old_heartbeat_status))
            self.heartbeat_error = True
            self.heartbeat_status = 'ERROR'

    def _heartbeat_thread(self):
        while self.heartbeat_thread_running:
            # check heartbeat status
            self._read_heartbeat()

            if not self.heartbeat_enabled:
                # send a 0 to make sure the system is disabled
                # if it's in the closed state it's already disabled, so leave it
                if self.heartbeat_status not in ['disabled', 'closed']:
                    if self.log:
                        self.log.debug('disabling heartbeat (status = {})'.format(
                            self.heartbeat_status))
                    v = chr(0).encode('ascii')
                    self.heartbeat_serial.write(v)
                    if self.log and self.log_debug:
                        self.log.debug('heartbeat SEND:"{}"'.format(v))
            else:
                if self.heartbeat_status == 'closed':
                    # send a 0 to reset it
                    if self.log:
                        self.log.debug('resetting heartbeat (status = {})'.format(
                            self.heartbeat_status))
                    v = chr(0).encode('ascii')
                    self.heartbeat_serial.write(v)
                    if self.log and self.log_debug:
                        self.log.debug('heartbeat SEND:"{}"'.format(v))
                else:
                    # send the heartbeat time to the serial port
                    # NB the timeout param is in s, but the board takes .5 second intervals
                    v = chr(self.heartbeat_timeout * 2).encode('ascii')
                    self.heartbeat_serial.write(v)
                    if self.log and self.log_debug:
                        self.log.debug('heartbeat SEND:"{}"'.format(v))

            # Sleep for halt the timeout period
            time.sleep(self.heartbeat_timeout / 2)

    def set_heartbeat(self, command):
        """Enable or disable the heartbeat."""
        if command:
            if self.heartbeat_enabled:
                return 'Heartbeat already enabled'
            else:
                self.heartbeat_enabled = True
                return 'Heartbeat enabled'
        else:
            if not self.heartbeat_enabled:
                return 'Heartbeat already disabled'
            else:
                self.heartbeat_enabled = False
                return 'Heartbeat disabled'

    def _output_thread(self):
        side = self.side
        frac = self.frac
        command = self.command
        timeout = self.timeout
        start_time = time.time()

        while self.output_thread_running:
            # store running time for timeout
            running_time = time.time() - start_time

            # check reasons to break out and stop the thread
            if command == 'open' and self.status[side] == 'full_open':
                if self.log:
                    self.log.info('Dome at limit')
                self.output_thread_running = 0
                break
            elif command == 'close' and self.status[side] == 'closed':
                if self.log:
                    self.log.info('Dome at limit')
                self.output_thread_running = 0
                break
            elif (frac != 1 and running_time > self.move_time[side][command] * frac):
                if self.log:
                    self.log.info('Dome moved requested fraction')
                self.output_thread_running = 0
                break
            elif running_time > timeout:
                if self.log:
                    self.log.info('Dome moving timed out')
                self.output_thread_running = 0
                break
            elif self.status[side] == 'ERROR':
                if self.log:
                    self.log.warning('All sensors failed, stopping movement')
                self.output_thread_running = 0
                break

            # if we're still going, send the command to the serial port
            self.dome_serial.write(self.move_code[side][command])
            if self.log and self.log_debug:
                self.log.debug('plc SEND:"{}" ({} {} {})'.format(
                    self.move_code[side][command], side, frac, command))

            if (side == 'south' and command == 'open' and running_time < 12.5):
                time.sleep(1.5)
            else:
                time.sleep(0.5)

        # finished moving for whatever reason, reset before exiting
        self.side = ''
        self.command = ''
        self.timeout = 40.

    def halt(self):
        """To stop the output thread."""
        self.output_thread_running = 0

    def _move_dome(self, side, command, frac, timeout=40.):
        """Move the dome until it reaches its limit."""
        self.side = side
        self.frac = frac
        self.command = command
        self.timeout = timeout

        # Don't interupt!
        if self.status[side] in ['opening', 'closing']:
            return

        # start output thread
        if not self.output_thread_running:
            if self.log:
                self.log.info('starting to move:', side, command, frac)
            self.output_thread_running = 1
            ot = threading.Thread(target=self._output_thread)
            ot.daemon = True
            ot.start()
            return

    def open_side(self, side, frac=1, sound_alarm=True):
        """Open one side of the dome."""
        if sound_alarm:
            self.sound_alarm()
        self._move_dome(side, 'open', frac)
        return

    def close_side(self, side, frac=1, sound_alarm=True):
        """Close one side of the dome."""
        if sound_alarm:
            self.sound_alarm()
        self._move_dome(side, 'close', frac)
        return

    def sound_alarm(self, duration=params.DOME_ALARM_DURATION, sleep=True):
        """Sound the dome alarm using the Arduino.

        duration : int [0-9]
            The time to sound the alarm for (seconds)
            default = 3

        sleep : bool
            Whether to sleep for the duration of the alarm
            or return immediately
            default = True
        """
        loc = params.ARDUINO_LOCATION
        subprocess.getoutput('curl -s {}?s{}'.format(loc, duration))
        if sleep:
            time.sleep(duration)
        return


class FakeDehumidifier(object):
    """Fake dehumidifier class."""

    def __init__(self):
        self._status = '0'

    def on(self):
        """Turn on the dehumidifier."""
        self._status = '1'

    def off(self):
        """Turn off the dehumidifier."""
        self._status = '0'

    @property
    def status(self):
        """Get the dehumidifier status."""
        return self._status


class Dehumidifier(object):
    """Dehumidifier class (using a ETH002 relay)."""

    def __init__(self, address, port):
        self.address = address
        self.port = port
        self.power = ETH002(self.address, self.port)

    def on(self):
        """Turn on the dehumidifier."""
        self.power.on(1)

    def off(self):
        """Turn off the dehumidifier."""
        self.power.off(1)

    @property
    def status(self):
        """Get the dehumidifier status."""
        return self.power.status()[0]
