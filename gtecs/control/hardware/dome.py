"""Classes to control telescope domes and dehumidifiers."""

import json
import logging
import os
import subprocess
import threading
import time
import urllib

import Pyro4

import serial  # noqa: I900

from .power import ETHPDU
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
        status = {'a_side': 'ERROR', 'b_side': 'ERROR', 'hatch': 'ERROR'}
        self._read_temp()

        # "a" side
        if self._status_arr[0] == 0:
            status['a_side'] = 'closed'
        elif self._status_arr[0] == 9:
            status['a_side'] = 'full_open'
        elif self.output_thread_running and self._moving_side == 'a_side':
            if self._moving_command == 'open':
                status['a_side'] = 'opening'
            elif self._moving_command == 'close':
                status['a_side'] = 'closing'
        else:
            status['a_side'] = 'part_open'

        # "b" side
        if self._status_arr[1] == 0:
            status['b_side'] = 'closed'
        elif self._status_arr[1] == 9:
            status['b_side'] = 'full_open'
        elif self.output_thread_running and self._moving_side == 'b_side':
            if self._moving_command == 'open':
                status['b_side'] = 'opening'
            elif self._moving_command == 'close':
                status['b_side'] = 'closing'
        else:
            status['b_side'] = 'part_open'

        # hatch (never actually opens...)
        if self._status_arr[2] == 0:
            status['hatch'] = 'closed'
        else:
            status['hatch'] = 'open'

        return status

    def _output_thread(self, side, command, frac):
        if side == 'a_side':
            i_side = 0
        elif side == 'b_side':
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
                time.sleep(0.5)
            elif command == 'close':
                self._status_arr[i_side] -= 1
                self._write_temp()
                time.sleep(0.5)

            time.sleep(0.5)

        self._moving_side = None
        self._moving_command = None
        if self.log:
            self.log.debug('output thread finished')

    def _move_dome(self, side, command, frac):
        # Don't interrupt!
        if self.status[side] in ['opening', 'closing']:
            return

        # limit move fraction
        if frac > 1:
            frac = 1

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
    domealert_uri : str, optional
        Pyro URI for the DomeAlert with additional switches

    log : logger, optional
        logger to log to
        default = None
    log_debug : bool, optional
        log debug strings?
        default = False

    """

    def __init__(self, port, arduino_ip=None, roomalert_ip=None, domealert_uri=None,
                 log=None, log_debug=False):
        self.serial_port = port
        self.serial_baudrate = 9600
        self.serial_timeout = 1

        if arduino_ip and not arduino_ip.startswith('http'):
            arduino_ip = 'http://' + arduino_ip
        self.arduino_ip = arduino_ip

        if roomalert_ip and not roomalert_ip.startswith('http'):
            roomalert_ip = 'http://' + roomalert_ip
        self.roomalert_ip = roomalert_ip

        if domealert_uri and not domealert_uri.startswith('PYRO'):
            domealert_uri = 'PYRO:' + domealert_uri
        self.domealert_uri = domealert_uri

        if ((arduino_ip and roomalert_ip) or
                (arduino_ip and domealert_uri) or
                (roomalert_ip and domealert_uri)):
            raise ValueError('Only one source of switches should be given.')

        # Create a logger if one isn't given
        if log is None:
            logging.basicConfig(level=logging.INFO)
            log = logging.getLogger('dome')
            log.setLevel(level=logging.DEBUG)
        self.log = log
        self.log_debug = log_debug

        self.status = None

        self.plc_status = {'a_side': 'ERROR', 'b_side': 'ERROR'}
        self.old_plc_status = None
        self.plc_error = False

        self.switch_status = {'a_side': 'ERROR', 'b_side': 'ERROR', 'hatch': 'ERROR'}
        self.old_switch_status = None
        self.switch_error = False

        self.full_open = {'a_side': False, 'b_side': False}
        self.honeywell_was_triggered = {'a_side': False, 'b_side': False}

        self.move_code = {'a_side': {'open': b'a', 'close': b'A'},
                          'b_side': {'open': b'b', 'close': b'B'}}
        self.reset_code = b'R'

        self.move_time = {'a_side': {'open': params.DOME_OPEN_ASIDE_TIME,
                                     'close': params.DOME_CLOSE_ASIDE_TIME},
                          'b_side': {'open': params.DOME_OPEN_BSIDE_TIME,
                                     'close': params.DOME_CLOSE_BSIDE_TIME}}

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
                    self.plc_status['a_side'] = 'ERROR'
                    self.plc_status['b_side'] = 'ERROR'

    def _parse_plc_status(self, status_character):
        # save previous status
        self.old_plc_status = self.plc_status.copy()
        # Non-moving statuses
        # returned when we're NOT sending command bytes
        # note the open status depends on the full_open flags
        if status_character == '0':
            self.plc_status['a_side'] = 'closed'
            self.plc_status['b_side'] = 'closed'
        elif status_character == '1':
            self.plc_status['a_side'] = 'closed'
            if self.full_open['b_side']:
                self.plc_status['b_side'] = 'full_open'
            else:
                self.plc_status['b_side'] = 'part_open'
        elif status_character == '2':
            if self.full_open['a_side']:
                self.plc_status['a_side'] = 'full_open'
            else:
                self.plc_status['a_side'] = 'part_open'
            self.plc_status['b_side'] = 'closed'
        elif status_character == '3':
            if self.full_open['a_side']:
                self.plc_status['a_side'] = 'full_open'
            else:
                self.plc_status['a_side'] = 'part_open'
            if self.full_open['b_side']:
                self.plc_status['b_side'] = 'full_open'
            else:
                self.plc_status['b_side'] = 'part_open'
        elif status_character == '4':  # Only in newer AstroHaven domes
            self.plc_status['a_side'] = 'full_open'
            self.plc_status['b_side'] = 'full_open'
        # Moving statuses
        # returned when we ARE sending command bytes
        # note here we set the full_open flag, since we only get that info when a move has finished
        elif status_character == 'a':
            self.plc_status['a_side'] = 'opening'
            self.full_open['a_side'] = False
        elif status_character == 'A':
            self.plc_status['a_side'] = 'closing'
            self.full_open['a_side'] = False
        elif status_character == 'b':
            self.plc_status['b_side'] = 'opening'
            self.full_open['b_side'] = False
        elif status_character == 'B':
            self.plc_status['b_side'] = 'closing'
            self.full_open['b_side'] = False
        elif status_character == 'x':
            self.plc_status['a_side'] = 'full_open'
            self.full_open['a_side'] = True
        elif status_character == 'X':
            self.plc_status['a_side'] = 'closed'
            self.full_open['a_side'] = False
        elif status_character == 'y':
            self.plc_status['b_side'] = 'full_open'
            self.full_open['b_side'] = True
        elif status_character == 'Y':
            self.plc_status['b_side'] = 'closed'
            self.full_open['b_side'] = False
        # Other return commands
        elif status_character == 'R':
            # We just sent an 'R' to reset the bumper guards
            # I don't have anything to do here, but it's good to know (and we already logged it)
            self.log.info('Bumper guard reset')
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
                       'a_side_open': bool(data['switch_b']),
                       'b_side_open': bool(data['switch_c']),
                       'hatch_closed': bool(data['switch_d']),
                       }
        return switch_dict

    def _read_roomalert(self):
        with urllib.request.urlopen(self.roomalert_ip + '/getData.json') as r:
            data = json.loads(r.read())
        data = {d['lab']: d['stat'] for d in data['s_sen'] if 'Switch Sen' not in d['lab']}
        if self.log and self.log_debug:
            self.log.debug('roomalert RECV:"{}"'.format(data))

        if 'Hatch' not in data or data['Hatch'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'A Limit' not in data or data['A Limit'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'B Limit' not in data or data['B Limit'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'Full Close' not in data or data['Full Close'] not in [0, 1]:
            raise ValueError('Unexpected switch status: {}'.format(data))

        switch_dict = {'all_closed': bool(data['Full Close']),
                       'a_side_open': bool(data['A Limit']),
                       'b_side_open': bool(data['B Limit']),
                       'hatch_closed': bool(data['Hatch']),
                       }
        return switch_dict

    def _read_domealert(self):
        with Pyro4.Proxy(self.domealert_uri) as pyro_daemon:
            pyro_daemon._pyroSerializer = 'serpent'
            data = pyro_daemon.last_measurement()

        if self.log and self.log_debug:
            self.log.debug('domealert RECV:"{}"'.format(data))

        if 'hatch_closed' not in data or data['hatch_closed_valid'] is False:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'north_shutter_open' not in data or data['north_shutter_open_valid'] is False:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'south_shutter_open' not in data or data['south_shutter_open_valid'] is False:
            raise ValueError('Unexpected switch status: {}'.format(data))
        if 'shutters_closed' not in data or data['shutters_closed_valid'] is False:
            raise ValueError('Unexpected switch status: {}'.format(data))

        # Account for different sides
        if params.DOME_ASIDE_NAME.lower() == 'north':
            a_side_open = data['north_shutter_open']
            b_side_open = data['south_shutter_open']
        else:
            a_side_open = data['south_shutter_open']
            b_side_open = data['north_shutter_open']

        switch_dict = {'all_closed': bool(data['shutters_closed']),
                       'a_side_open': bool(a_side_open),
                       'b_side_open': bool(b_side_open),
                       'hatch_closed': bool(data['hatch_closed']),
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
                elif self.domealert_uri is not None:
                    switch_dict = self._read_domealert()
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
                    self._parse_switch_status(None)
                    self.switch_error = True

    def _parse_switch_status(self, switch_dict):
        # save previous status
        self.old_switch_status = self.switch_status.copy()

        # no source of switches
        if switch_dict is None:
            self.switch_status = {'a_side': 'unknown', 'b_side': 'unknown', 'hatch': 'unknown'}
            return

        # we should have switch info
        try:
            if switch_dict['all_closed']:
                if not switch_dict['a_side_open']:
                    self.switch_status['a_side'] = 'closed'
                else:
                    self.switch_status['a_side'] = 'ERROR'
                if not switch_dict['b_side_open']:
                    self.switch_status['b_side'] = 'closed'
                else:
                    self.switch_status['b_side'] = 'ERROR'
            else:
                if switch_dict['a_side_open']:
                    self.switch_status['a_side'] = 'full_open'
                else:
                    self.switch_status['a_side'] = 'part_open'
                if switch_dict['b_side_open']:
                    self.switch_status['b_side'] = 'full_open'
                else:
                    self.switch_status['b_side'] = 'part_open'

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

            for side in ['a_side', 'b_side']:
                # find the current status
                if side == 'a_side':
                    honeywell_triggered = switch_dict['a_side_open']
                else:
                    honeywell_triggered = switch_dict['b_side_open']

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
        for side in ['a_side', 'b_side']:
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

        # get the starting position
        # (used for stepping so it doesn't stutter when already partially open)
        start_position = self.status[side]

        while self.output_thread_running:
            # store running time for timeout
            running_time = time.time() - start_time

            # check reasons to break out and stop the thread
            if command == 'open' and self.status[side] == 'full_open':
                if self.log:
                    self.log.info('Dome at limit (full_open)')
                self.output_thread_running = False
                break
            elif command == 'close' and self.status[side] == 'closed':
                if self.log:
                    self.log.info('Dome at limit (closed)')
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

            if (side == 'a_side' and start_position == 'closed' and command == 'open' and
                    running_time < params.DOME_STUTTER_TIME):
                # Used to "stutter step" the a side (3 shutters) when opening,
                # so that the top shutter doesn't jerk on the belts when it tips over.
                time.sleep(params.DOME_STUTTER_TIMESTEP)
            else:
                time.sleep(params.DOME_MOVE_TIMESTEP)

        if self.log:
            self.log.debug('output thread finished')

    def _move_dome(self, side, command, frac):
        """Move the dome until it reaches its limit."""
        # Don't interrupt!
        if self.status[side] in ['opening', 'closing']:
            return

        # limit move fraction
        if frac > 1:
            frac = 1

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
        self.status = None
        self.old_status = None

        self.thread_running = False

        # connect to serial port
        self.connect()

        # start heartbeat thread
        ht = threading.Thread(target=self._heartbeat_thread)
        ht.daemon = True
        ht.start()

    def __del__(self):
        self.disconnect()

    def connect(self):
        """Connect to the heartbeat via serial port."""
        self.serial = serial.Serial(
            self.serial_port,
            baudrate=self.serial_baudrate,
            timeout=self.serial_timeout,
        )

    def disconnect(self):
        """Shutdown the connection."""
        # Stop thread
        self.thread_running = False

        # Close serial port
        try:
            self.serial.close()
        except AttributeError:
            pass
        self.serial = None
        self.status = 'ERROR'

    def _heartbeat_thread(self):
        if self.log:
            self.log.debug('heartbeat thread started')
        self.thread_running = True

        while self.thread_running:
            # check heartbeat status
            self._read_heartbeat()

            if self.status == 'ERROR':
                # if we can't communicate with the heartbeat monitor then exit
                if self.log:
                    self.log.error('Connection error, exiting heartbeat thread')
                self.thread_running = False
                break

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
                if self.serial is None:
                    self.connect()
                self.old_status = self.status  # save previous status
                if self.serial.in_waiting:
                    out = self.serial.read(self.serial.in_waiting)
                    x = out[-1]
                    self.status = self._parse_status(x)
                    if self.log and self.log_debug:
                        self.log.debug('heartbeat RECV:"{}" (status={})'.format(x, self.status))
            except Exception:
                attempts_remaining -= 1
                if self.log:
                    self.log.warning('Error communicating with the heartbeat monitor')
                    self.log.debug('', exc_info=True)
                    if self.old_status is not None:
                        self.log.debug('Previous status: {}'.format(self.old_status))
                if attempts_remaining > 0:
                    # If we have the connection then it's worth retrying
                    self.log.warning('Remaining tries: {}'.format(attempts_remaining))
                    time.sleep(0.5)
                else:
                    if self.log:
                        self.log.error('Could not communicate with the heartbeat monitor')
                    self.status = 'ERROR'
                    break

    def _parse_status(self, status_character):
        """Parse the return value from heartbeat."""
        if status_character == 254:
            return 'closing'
        elif status_character == 255:
            return 'closed'
        elif status_character == 0:
            return 'disabled'
        elif 0 < status_character < 254:
            return 'enabled'
        else:
            raise ValueError('Unable to parse reply from the heartbeat monitor: {}'.format(
                status_character))

    def sound_alarm(self):
        """Sound the dome alarm using the heartbeat (always sounds for 5s)."""
        if self.log:
            self.log.warning('Sounding alarm (status={})'.format(self.status))
        if self.serial is None or self.status == 'ERROR':
            raise ValueError('Cannot connect to Heartbeat')  # TODO: should be HardwareError?
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
        self._on = False

    def on(self):
        """Turn on the dehumidifier."""
        self._on = True

    def off(self):
        """Turn off the dehumidifier."""
        self._on = False

    @property
    def status(self):
        """Get the dehumidifier status (True = on, False = off)."""
        return self._on


class ETH002Dehumidifier:
    """Dehumidifier class (using a ETH002 relay)."""

    def __init__(self, address, port):
        self.address = address
        self.port = port
        self.power = ETHPDU(self.address, self.port, outlets=2, normally_closed=False)

    def on(self):
        """Turn on the dehumidifier."""
        self.power.on(1)

    def off(self):
        """Turn off the dehumidifier."""
        self.power.off(1)

    @property
    def status(self):
        """Get the dehumidifier status (True = on, False = off)."""
        return self.power.status()[0] == '1'


class Dehumidifier:
    """Dehumidifier class (using Paul's DomeAlert)."""

    def __init__(self, uri):
        self.uri = uri

    def _proxy(self):
        proxy = Pyro4.Proxy(self.uri)
        proxy._pyroSerializer = 'serpent'
        return proxy

    def on(self):
        """Turn on the dehumidifier."""
        with self._proxy() as proxy:
            proxy.set_relay(True)

    def off(self):
        """Turn off the dehumidifier."""
        with self._proxy() as proxy:
            proxy.set_relay(False)

    @property
    def status(self):
        """Get the dehumidifier status (True = on, False = off)."""
        with self._proxy() as proxy:
            status = proxy.get_relay()
        return status
