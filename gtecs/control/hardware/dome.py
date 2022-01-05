"""Classes to control telescope domes and dehumidifiers."""

import json
import logging
import os
import subprocess
import threading
import time
import urllib

import serial  # noqa: I900

from .power import ETH002
from .. import params


class FakeDome:
    """Fake AstroHaven dome class."""

    def __init__(self, log=None, log_debug=False):
        # Create a logger if one isn't given
        if log is None:
            logging.basicConfig(level=logging.INFO)
            log = logging.getLogger('dome')
            log.setLevel(level=logging.DEBUG)
        self.log = log
        self.log_debug = log_debug

        self.plc_error = False
        self.switch_error = False

        self.output_thread_running = False
        self.status_thread_running = True

        # fake stuff
        self._temp_file = '/tmp/dome'
        self._status_arr = [0, 0, 0]
        self._writing = False  # had problems with reading and writing overlapping
        self._moving_side = None
        self._moving_command = None

        self._read_temp()

    def __del__(self):
        try:
            # Stop threads
            self.output_thread_running = False
        except AttributeError:
            pass

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
                if string != '':  # I don't know why or how that happens
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

    @property
    def status_update_time(self):
        return time.time() - 2

    def disconnect(self):
        """Shutdown the connection."""
        return

    def _check_status(self):
        status = {'north': 'ERROR', 'south': 'ERROR', 'hatch': 'ERROR'}
        self._read_temp()
        # north
        if self._status_arr[0] == 0:
            status['north'] = 'closed'
        elif self._status_arr[0] == 9:
            status['north'] = 'full_open'
        elif self.output_thread_running and self._moving_side == 'north':
            if self._moving_command == 'open':
                status['north'] = 'opening'
            elif self._moving_command == 'close':
                status['north'] = 'closing'
        else:
            status['north'] = 'part_open'

        # south
        if self._status_arr[1] == 0:
            status['south'] = 'closed'
        elif self._status_arr[1] == 9:
            status['south'] = 'full_open'
        elif self.output_thread_running and self._moving_side == 'south':
            if self._moving_command == 'open':
                status['south'] = 'opening'
            elif self._moving_command == 'close':
                status['south'] = 'closing'
        else:
            status['south'] = 'part_open'

        # hatch (never actually opens...)
        if self._status_arr[2] == 0:
            status['hatch'] = 'closed'
        else:
            status['hatch'] = 'open'

        return status

    def _output_thread(self, side, command, frac):
        if side == 'north':
            i_side = 0
        elif side == 'south':
            i_side = 1
        else:
            raise ValueError('Invalid side: {}'.format(side))
        start_time = time.time()
        if self.log:
            self.log.debug('output thread started')

        self._read_temp()
        start_position = self._status_arr[i_side]

        while self.output_thread_running:
            # store running time for timeout
            running_time = time.time() - start_time

            # check reasons to break out and stop the thread
            if command == 'open' and self._status_arr[i_side] == 9:
                if self.log:
                    self.log.info('Dome at limit')
                self.output_thread_running = False
                break
            elif command == 'close' and self._status_arr[i_side] == 0:
                if self.log:
                    self.log.info('Dome at limit')
                self.output_thread_running = False
                break
            elif (frac != 1 and
                  abs(start_position - self._status_arr[i_side]) > frac * 9):
                if self.log:
                    self.log.info('Dome moved requested fraction')
                self.output_thread_running = False
                break
            elif running_time > params.DOME_MOVE_TIMEOUT:
                if self.log:
                    self.log.info('Dome moving timed out')
                self.output_thread_running = False
                break
            elif self.status[side] == 'ERROR':
                if self.log:
                    self.log.warning('All sensors failed, stopping movement')
                self.output_thread_running = False
                break

            # if we're still going, send the command to "the serial port"
            if command == 'open':
                self._status_arr[i_side] += 1
                self._write_temp()
                time.sleep(3)
            elif command == 'close':
                self._status_arr[i_side] -= 1
                self._write_temp()
                time.sleep(3)

            time.sleep(0.5)

        self._moving_side = None
        self._moving_command = None
        if self.log:
            self.log.debug('output thread finished')

    def _move_dome(self, side, command, frac):
        # Don't interupt!
        if self.status[side] in ['opening', 'closing']:
            return

        # start output thread
        if not self.output_thread_running:
            if self.log:
                self.log.info('starting to move: {} {} {}'.format(side, command, frac))
            self._moving_side = side
            self._moving_command = command
            self.output_thread_running = True
            ot = threading.Thread(target=self._output_thread,
                                  args=[side, command, frac])
            ot.daemon = True
            ot.start()
            return

    def open_side(self, side, frac=1):
        """Open one side of the dome."""
        self._move_dome(side, 'open', frac)
        return

    def close_side(self, side, frac=1):
        """Close one side of the dome."""
        self._move_dome(side, 'close', frac)
        return

    def halt(self):
        """Stop the dome moving."""
        self.output_thread_running = False


class AstroHavenDome:
    """AstroHaven dome control class (based on Warwick 1m control).

    Parameters
    ----------
    port : str
        Device location for the dome (e.g. '/dev/ttyUSB0')
    arduino_ip : str, optional
        Connection IP for the Arduino with additional switches
    roomalert_ip : str, optional
        Connection IP for the RoomAlert with additional switches

    log : logger, optional
        logger to log to
        default = None
    log_debug : bool, optional
        log debug strings?
        default = False

    """

    def __init__(self, port, arduino_ip=None, roomalert_ip=None, log=None, log_debug=False):
        self.serial_port = port
        self.serial_baudrate = 9600
        self.serial_timeout = 1

        if arduino_ip and not arduino_ip.startswith('http'):
            arduino_ip = 'http://' + arduino_ip
        self.arduino_ip = arduino_ip

        if roomalert_ip and not roomalert_ip.startswith('http'):
            roomalert_ip = 'http://' + roomalert_ip
        self.roomalert_ip = roomalert_ip

        if arduino_ip and roomalert_ip:
            raise ValueError('Either `arduino_ip` or `roomalert_ip` can be given, but not both.')

        # Create a logger if one isn't given
        if log is None:
            logging.basicConfig(level=logging.INFO)
            log = logging.getLogger('dome')
            log.setLevel(level=logging.DEBUG)
        self.log = log
        self.log_debug = log_debug

        self.status = None

        self.plc_status = {'north': 'ERROR', 'south': 'ERROR'}
        self.old_plc_status = None
        self.plc_error = False

        self.switch_status = {'north': 'ERROR', 'south': 'ERROR', 'hatch': 'ERROR'}
        self.old_switch_status = None
        self.switch_error = False

        self.full_open = {'north': False, 'south': False}
        self.honeywell_was_triggered = {'north': False, 'south': False}

        self.move_code = {'south': {'open': b'a', 'close': b'A'},
                          'north': {'open': b'b', 'close': b'B'}}
        self.reset_code = b'R'

        self.move_time = {'south': {'open': params.DOME_OPEN_SOUTH_TIME,
                                    'close': params.DOME_CLOSE_SOUTH_TIME},
                          'north': {'open': params.DOME_OPEN_NORTH_TIME,
                                    'close': params.DOME_CLOSE_NORTH_TIME}}

        self.output_thread_running = False
        self.status_thread_running = False
        self.status_update_time = 0

        # serial connection to the dome
        self.dome_serial = serial.Serial(self.serial_port,
                                         baudrate=self.serial_baudrate,
                                         timeout=self.serial_timeout)

        # start status check thread
        st = threading.Thread(target=self._status_thread)
        st.daemon = True
        st.start()

    def __del__(self):
        self.disconnect()

    def disconnect(self):
        """Shutdown the connection."""
        # Stop threads
        self.output_thread_running = False
        self.status_thread_running = False

        # Close serial
        try:
            self.dome_serial.close()
        except AttributeError:
            pass

    def _read_plc(self, attempts=3):
        attempts_remaining = attempts
        while attempts_remaining:
            try:
                if self.dome_serial.in_waiting:
                    out = self.dome_serial.read(self.dome_serial.in_waiting)
                    x = out.decode('ascii')[-1]
                    if self.log and self.log_debug:
                        self.log.debug('plc RECV:"{}"'.format(x))
                    self._parse_plc_status(x)
                return
            except Exception:
                attempts_remaining -= 1
                if self.log:
                    self.log.warning('Error communicating with the PLC')
                    self.log.debug('', exc_info=True)
                    self.log.debug('Previous status: {}'.format(self.old_plc_status))
                if attempts_remaining > 0:
                    self.log.warning('Remaining tries: {}'.format(attempts_remaining))
                    time.sleep(0.5)
                else:
                    if self.log:
                        self.log.error('Could not communicate with the PLC')
                    self.plc_error = True
                    self.plc_status['north'] = 'ERROR'
                    self.plc_status['south'] = 'ERROR'

    def _parse_plc_status(self, status_character):
        # save previous status
        self.old_plc_status = self.plc_status.copy()
        # Non-moving statuses
        # returned when we're NOT sending command bytes
        # note the open status depends on the full_open flags
        if status_character == '0':
            self.plc_status['north'] = 'closed'
            self.plc_status['south'] = 'closed'
        elif status_character == '1':
            if self.full_open['north']:
                self.plc_status['north'] = 'full_open'
            else:
                self.plc_status['north'] = 'part_open'
            self.plc_status['south'] = 'closed'
        elif status_character == '2':
            self.plc_status['north'] = 'closed'
            if self.full_open['south']:
                self.plc_status['south'] = 'full_open'
            else:
                self.plc_status['south'] = 'part_open'
        elif status_character == '3':
            if self.full_open['north']:
                self.plc_status['north'] = 'full_open'
            else:
                self.plc_status['north'] = 'part_open'
            if self.full_open['south']:
                self.plc_status['south'] = 'full_open'
            else:
                self.plc_status['south'] = 'part_open'
        # Moving statuses
        # returned when we ARE sending command bytes
        # note here we set the full_open flag, since we only get that info when a move has finished
        elif status_character == 'a':
            self.plc_status['south'] = 'opening'
            self.full_open['south'] = False
        elif status_character == 'A':
            self.plc_status['south'] = 'closing'
            self.full_open['south'] = False
        elif status_character == 'b':
            self.plc_status['north'] = 'opening'
            self.full_open['north'] = False
        elif status_character == 'B':
            self.plc_status['north'] = 'closing'
            self.full_open['north'] = False
        elif status_character == 'x':
            self.plc_status['south'] = 'full_open'
            self.full_open['south'] = True
        elif status_character == 'X':
            self.plc_status['south'] = 'closed'
            self.full_open['south'] = False
        elif status_character == 'y':
            self.plc_status['north'] = 'full_open'
            self.full_open['north'] = True
        elif status_character == 'Y':
            self.plc_status['north'] = 'closed'
            self.full_open['north'] = False
        # Other return commands
        elif status_character == 'R':
            # We just sent an 'R' to reset the bumper guards
            # I don't have anything to do here, but it's good to know (and we already logged it)
            pass
        else:
            raise ValueError('Unable to parse reply from the PLC: {}'.format(status_character))

    def _read_arduino(self):
        with urllib.request.urlopen(self.arduino_ip) as r:
            data = json.loads(r.read())
        if self.log and self.log_debug:
            self.log.debug('arduino RECV:"{}"'.format(data))

        if 'switch_a' not in data or data['switch_a'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'switch_b' not in data or data['switch_b'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'switch_c' not in data or data['switch_c'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'switch_d' not in data or data['switch_d'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(data))

        switch_dict = {'all_closed': bool(data['switch_a']),
                       'north_open': bool(data['switch_b']),
                       'south_open': bool(data['switch_c']),
                       'hatch_closed': bool(data['switch_d']),
                       }
        return switch_dict

    def _read_roomalert(self):
        with urllib.request.urlopen(self.roomalert_ip + '/getData.json') as r:
            data = json.loads(r.read())

        switches = {d['lab']: d['stat'] for d in data['s_sen'] if 'Switch Sen' not in d['lab']}
        if self.log and self.log_debug:
            self.log.debug('roomalert RECV:"{}"'.format(switches))

        if 'Hatch' not in switches or switches['Hatch'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(switches))
        if 'North Limit' not in switches or switches['North Limit'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(switches))
        if 'South Limit' not in switches or switches['South Limit'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(switches))
        if 'Full Close' not in switches or switches['Full Close'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(switches))

        switch_dict = {'all_closed': bool(switches['Full Close']),
                       'north_open': bool(switches['North Limit']),
                       'south_open': bool(switches['South Limit']),
                       'hatch_closed': bool(switches['Hatch']),
                       }
        return switch_dict

    def _read_switches(self, attempts=3):
        attempts_remaining = attempts
        while attempts_remaining:
            try:
                if self.arduino_ip is not None:
                    switch_dict = self._read_arduino()
                elif self.roomalert_ip is not None:
                    switch_dict = self._read_roomalert()
                else:
                    switch_dict = None
                self._parse_switch_status(switch_dict)
                return
            except Exception:
                attempts_remaining -= 1
                if self.log:
                    self.log.warning('Error communicating with the switches')
                    self.log.debug('', exc_info=True)
                    self.log.debug('Previous status: {}'.format(self.old_switch_status))
                if attempts_remaining > 0:
                    self.log.warning('Remaining tries: {}'.format(attempts_remaining))
                    time.sleep(0.5)
                else:
                    if self.log:
                        self.log.error('Could not communicate with the switches')
                    self.switch_error = True
                    self.switch_status['north'] = 'ERROR'
                    self.switch_status['south'] = 'ERROR'
                    self.switch_status['hatch'] = 'ERROR'

    def _parse_switch_status(self, switch_dict):
        # save previous status
        self.old_switch_status = self.switch_status.copy()

        # no source of switches
        if switch_dict is None:
            self.switch_status = {'north': 'unknown', 'south': 'unknown', 'hatch': 'unknown'}
            return

        # we should have switch info
        try:
            if switch_dict['all_closed']:
                if not switch_dict['north_open']:
                    self.switch_status['north'] = 'closed'
                else:
                    self.switch_status['north'] = 'ERROR'
                if not switch_dict['south_open']:
                    self.switch_status['south'] = 'closed'
                else:
                    self.switch_status['south'] = 'ERROR'
            else:
                if switch_dict['north_open']:
                    self.switch_status['north'] = 'full_open'
                else:
                    self.switch_status['north'] = 'part_open'
                if switch_dict['south_open']:
                    self.switch_status['south'] = 'full_open'
                else:
                    self.switch_status['south'] = 'part_open'

            if switch_dict['hatch_closed']:
                self.switch_status['hatch'] = 'closed'
            else:
                self.switch_status['hatch'] = 'open'

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
                    honeywell_triggered = switch_dict['north_open']
                else:
                    honeywell_triggered = switch_dict['south_open']

                # if the honeywell is triggered now, store it
                if honeywell_triggered:
                    self.honeywell_was_triggered[side] = True

                # but if it's not currently triggered,
                # it might have gone past
                else:
                    if self.honeywell_was_triggered[side]:
                        if self.plc_status[side] == 'opening':
                            # Oh dear, it's flicked past the Honeywells
                            # and it's still going!!
                            if self.log:
                                self.log.warning('Honeywell limit error, stopping!')
                            self.switch_status[side] = 'full_open'
                            self.output_thread_running = False  # to be sure
                        else:
                            # It's moving back, clear the memory
                            self.honeywell_was_triggered[side] = False

        except Exception:
            raise ValueError('Unable to parse reply from switches: {}'.format(switch_dict))

    def _read_status(self):
        """Check the dome status reported by both the dome plc and the extra switches."""
        # check plc
        self._read_plc()

        # check switches
        self._read_switches()

        if self.log and self.log_debug:
            self.log.debug('status: plc:{} switches:{}'.format(self.plc_status, self.switch_status))

        status = {}

        # dome logic
        for side in ['north', 'south']:
            plc_status = self.plc_status[side]
            switch_status = self.switch_status[side]

            if switch_status != 'unknown':
                # Chose which dome status to report
                if plc_status == switch_status:
                    # arbitrary
                    status[side] = plc_status
                elif plc_status == 'ERROR' and switch_status != 'ERROR':
                    # go with the one that is still working
                    status[side] = switch_status
                elif switch_status == 'ERROR' and plc_status != 'ERROR':
                    # go with the one that is still working
                    status[side] = plc_status
                elif plc_status[-3:] == 'ing':
                    if switch_status == 'part_open':
                        # the switches can't tell if it's moving
                        status[side] = plc_status
                    else:  # closed or full_open
                        # switch says it's reached the limit,
                        # but it hasn't stopped!!
                        status[side] = switch_status
                elif plc_status == 'part_open':
                    # switch says closed or full_open
                    status[side] = switch_status
                elif switch_status == 'part_open':
                    # plc says closed or full_open
                    status[side] = plc_status
                else:
                    # if one says closed and the other says full_open
                    # or something totally unexpected
                    status[side] = 'ERROR'
            else:
                # we don't have any switches for extra infomation
                status[side] = plc_status

        # Get the hatch status from the switch
        status['hatch'] = self.switch_status['hatch']

        return status

    def _status_thread(self):
        if self.status_thread_running:
            if self.log:
                self.log.debug('status thread tried to start when already running')
            return

        if self.log:
            self.log.debug('status thread started')
        self.status_thread_running = True

        while self.status_thread_running:
            try:
                self.status = self._read_status()
                self.status_update_time = time.time()
                # Check status more often if we are moving
                if self.output_thread_running:
                    time.sleep(0.5)
                else:
                    time.sleep(2)
            except Exception:
                if self.log:
                    self.log.error('Error in status thread')
                    self.log.debug('', exc_info=True)
                self.status_thread_running = False

        if self.log:
            self.log.debug('status thread finished')

    def _output_thread(self, side, command, frac):
        if self.output_thread_running:
            if self.log:
                self.log.debug('output thread tried to start when already running')
            return

        start_time = time.time()
        if self.log:
            self.log.debug('output thread started')
        self.output_thread_running = True

        while self.output_thread_running:
            # store running time for timeout
            running_time = time.time() - start_time

            # get the starting position
            start_position = self.status[side]

            # check reasons to break out and stop the thread
            if command == 'open' and self.status[side] == 'full_open':
                if self.log:
                    self.log.info('Dome at limit')
                self.output_thread_running = False
                break
            elif command == 'close' and self.status[side] == 'closed':
                if self.log:
                    self.log.info('Dome at limit')
                self.output_thread_running = False
                break
            elif (frac != 1 and running_time > self.move_time[side][command] * frac):
                if self.log:
                    self.log.info('Dome moved requested fraction')
                self.output_thread_running = False
                break
            elif running_time > params.DOME_MOVE_TIMEOUT:
                if self.log:
                    self.log.info('Dome moving timed out')
                self.output_thread_running = False
                break
            elif self.status[side] == 'ERROR':
                if self.log:
                    self.log.warning('All sensors failed, stopping movement')
                self.output_thread_running = False
                break

            # if we're still going, send the command to the serial port
            self.dome_serial.write(self.move_code[side][command])
            if self.log and self.log_debug:
                self.log.debug('plc SEND:"{}" ({} {} {})'.format(
                    self.move_code[side][command].decode(), side, frac, command))

            if (side == 'south' and start_position == 'closed' and command == 'open' and
                    running_time < params.DOME_STUTTER_TIME):
                # Used to "stutter step" the south side when opening,
                # so that the top shutter doesn't jerk on the belts when it tips over.
                # NEW: add start_position, so it doesn't stutter when already partially open
                time.sleep(params.DOME_STUTTER_TIMESTEP)
            else:
                time.sleep(params.DOME_MOVE_TIMESTEP)

        if self.log:
            self.log.debug('output thread finished')

    def _move_dome(self, side, command, frac):
        """Move the dome until it reaches its limit."""
        # Don't interupt!
        if self.status[side] in ['opening', 'closing']:
            return

        # start output thread
        if not self.output_thread_running:
            if self.log:
                self.log.info('starting to move: {} {} {}'.format(side, command, frac))
            ot = threading.Thread(target=self._output_thread,
                                  args=[side, command, frac])
            ot.daemon = True
            ot.start()
            return

    def open_side(self, side, frac=1):
        """Open one side of the dome."""
        self._move_dome(side, 'open', frac)
        return

    def close_side(self, side, frac=1):
        """Close one side of the dome."""
        self._move_dome(side, 'close', frac)
        return

    def halt(self):
        """Stop the output thread."""
        self.output_thread_running = False

    def reset_bumperguard(self):
        """Reset the bumper guard sensor."""
        self.dome_serial.write(self.reset_code)
        if self.log and self.log_debug:
            self.log.debug('plc SEND:"{}" ({})'.format(self.reset_code, 'reset'))


class FakeHeartbeat:
    """Fake dome heartbeat class."""

    def __init__(self):
        self.status = 'enabled'
        self.connection_error = False

    def disconnect(self):
        """Shutdown the connection."""
        return

    def sound_alarm(self):
        """Sound the dome alarm using the heartbeat."""
        # Note this is always blocking
        bell = 'play -qn --channels 1 synth 5 sine 440 vol 0.1'
        subprocess.getoutput(bell)

    def enable(self):
        """Enable the heartbeat."""
        self.status = 'enabled'
        return 'Heartbeat enabled'

    def disable(self):
        """Disable the heartbeat."""
        self.status = 'disabled'
        return 'Heartbeat disabled'


class DomeHeartbeat:
    """Dome heartbeat monitoring and control class.

    Parameters
    ----------
    port : str
        Device location for the heartbeat (e.g. '/dev/ttyUSB0')

    timeout : int, optional
        Timeout period for signals to the heartbeat.
        If this time is exceeded without receiving a signal the heartbeat box will close the dome.
        Default is 10 seconds

    log : logger, optional
        logger to log to
        Default is None, meaning a new logger will be created
    log_debug : bool, optional
        log debug strings?
        Default is False

    """

    def __init__(self, port, timeout=10, log=None, log_debug=False):
        self.serial_port = port
        self.serial_baudrate = 9600
        self.serial_timeout = 1

        # Create a logger if one isn't given
        if log is None:
            logging.basicConfig(level=logging.INFO)
            log = logging.getLogger('dome')
            log.setLevel(level=logging.DEBUG)
        self.log = log
        self.log_debug = log_debug

        self.enabled = True
        self.timeout = timeout
        self.status = 'ERROR'
        self.old_status = None
        self.connection_error = False

        self.thread_running = False

        # connect to serial port
        try:
            self.serial = serial.Serial(self.serial_port,
                                        baudrate=self.serial_baudrate,
                                        timeout=self.serial_timeout)
        except Exception:
            if self.log:
                self.log.error('Error connecting to heartbeat monitor')
                self.log.debug('', exc_info=True)
            self.status = 'ERROR'

        # start heartbeat thread
        ht = threading.Thread(target=self._heartbeat_thread)
        ht.daemon = True
        ht.start()

    def __del__(self):
        self.disconnect()

    def disconnect(self):
        """Shutdown the connection."""
        # Stop thread
        self.thread_running = False

        # Close serial port
        try:
            self.serial.close()
        except AttributeError:
            pass

    def _heartbeat_thread(self):
        if self.log:
            self.log.debug('heartbeat thread started')
        self.thread_running = True

        while self.thread_running:
            # check heartbeat status
            self._read_heartbeat()

            if not self.enabled:
                # send a 0 to make sure the system is disabled
                # if it's in the closed state it's already disabled, so leave it
                if self.status not in ['disabled', 'closed']:
                    if self.log:
                        self.log.debug('disabling heartbeat (status={})'.format(self.status))
                    v = 0
            else:
                if self.status == 'closed':
                    # the heartbeat has triggered, send a 0 to reset it
                    if self.log:
                        self.log.debug('resetting heartbeat (status={})'.format(self.status))
                    v = 0
                else:
                    # send the heartbeat time to the serial port
                    # NB the timeout param is in s, but the board takes .5 second intervals
                    v = int(self.timeout * 2)

            self.serial.write(bytes([v]))
            if self.log and self.log_debug:
                self.log.debug('heartbeat SEND:"{}" (status={})'.format(v, self.status))

            # Sleep for half of the timeout period
            time.sleep(self.timeout / 2)

        if self.log:
            self.log.debug('heartbeat thread finished')

    def _read_heartbeat(self, attempts=3):
        attempts_remaining = attempts
        while attempts_remaining:
            try:
                if self.serial.in_waiting:
                    out = self.serial.read(self.serial.in_waiting)
                    x = out[-1]
                    self._parse_status(x)
                    if self.log and self.log_debug:
                        self.log.debug('heartbeat RECV:"{}" (status={})'.format(x, self.status))
                return
            except Exception:
                attempts_remaining -= 1
                if self.log:
                    self.log.warning('Error communicating with the heartbeat monitor')
                    self.log.debug('', exc_info=True)
                    self.log.debug('Previous status: {}'.format(self.old_status))
                if attempts_remaining > 0:
                    self.log.warning('Remaining tries: {}'.format(attempts_remaining))
                    time.sleep(0.5)
                else:
                    if self.log:
                        self.log.error('Could not communicate with the heartbeat monitor')
                    self.status = 'ERROR'
                    self.connection_error = True

    def _parse_status(self, status_character):
        # save previous status
        self.old_status = self.status
        # parse value from heartbeat box
        if status_character == 254:
            self.status = 'closing'
        elif status_character == 255:
            self.status = 'closed'
        elif status_character == 0:
            self.status = 'disabled'
        elif 0 < status_character < 254:
            self.status = 'enabled'
        else:
            self.status = 'ERROR'
            raise ValueError('Unable to parse reply from the heartbeat monitor: {}'.format(
                status_character))
        return

    def sound_alarm(self):
        """Sound the dome alarm using the heartbeat (always sounds for 5s)."""
        if self.log:
            self.log.warning('Sounding alarm (status={})'.format(self.status))
        v = 255
        self.serial.write(bytes([v]))
        if self.log and self.log_debug:
            self.log.debug('heartbeat SEND:"{}" (status={})'.format(v, self.status))

    def enable(self):
        """Enable the heartbeat."""
        if self.enabled:
            return 'Heartbeat already enabled'
        else:
            self.enabled = True
            return 'Heartbeat enabled'

    def disable(self):
        """Disable the heartbeat."""
        if not self.enabled:
            return 'Heartbeat already disabled'
        else:
            self.enabled = False
            return 'Heartbeat disabled'


class FakeDehumidifier:
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


class Dehumidifier:
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
