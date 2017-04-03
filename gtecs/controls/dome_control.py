#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                           dome_control.py                            #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#     G-TeCS module containing classes to control telescope domes      #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import os, sys, subprocess
import six
if six.PY2:
    from commands import getoutput
else:
    from subprocess import getoutput
import time
import serial
import json
import threading
from six.moves import map
from six.moves import range
# TeCS modules
from gtecs.tecs_modules import params

########################################################################
# Fake AstroHaven dome class
class FakeDome:
    def __init__(self):
        self.fake = True
        # fake stuff
        self.temp_file = '/tmp/dome'
        self._read_temp()

    def _read_temp(self):
        if not os.path.exists(self.temp_file):
            self.dome_status = [0, 0, 0]
            self._write_temp()
        else:
            f = open(self.temp_file, 'r')
            self.dome_status = list(map(int,list(f.read().strip())))
            f.close()

    def _write_temp(self):
        f = open(self.temp_file,'w')
        f.write(''.join(str(i) for i in self.dome_status))
        f.close()

    def _move_dome(self, command, timeout=40.):
        self._read_temp()
        if command[:4] == 'west':
            side = 0
        elif command[:4] == 'east':
            side = 1
        if command[5:] == 'open':
            steps = 9 - self.dome_status[side]
            time.sleep(3 * steps) # intentionally blocking, like real dome
            self.dome_status[side] = 9
        elif command[5:] == 'close':
            steps = self.dome_status[side]
            time.sleep(3 * steps) # intentionally blocking, like real dome
            self.dome_status[side] = 0
        self._write_temp()

    def _move_dome_steps(self,command,steps):
        self._read_temp()
        if command[:4] == 'west':
            side = 0
        elif command[:4] == 'east':
            side = 1
        if command[5:] == 'open':
            finish = self.dome_status[side] + steps
            if finish > 9:
                finish = 9
                steps = 9 - self.dome_status[side]
            time.sleep(3 * steps) # intentionally blocking, like real dome
            self.dome_status[side] = finish
        elif command[5:] == 'close':
            finish = self.dome_status[side] - steps
            if finish < 0:
                finish = 0
                steps = self.dome_status[side]
            time.sleep(3 * steps) # intentionally blocking, like real dome
            self.dome_status[side] = finish
        self._write_temp()

    def status(self):
        status = {'dome':'ERROR', 'hatch':'ERROR'}
        self._read_temp()
        if self.dome_status[0] == 0 and self.dome_status[1] == 0:
            status['dome'] = 'closed'
        else:
            status['dome'] = 'open'
        if self.dome_status[2] == 0:
            status['hatch'] = 'closed'
        else:
            status['hatch'] = 'open'
        return status

    def open_full(self):
        self._move_dome('west_open')
        time.sleep(2)
        self._move_dome('east_open')

    def close_full(self):
        self._move_dome('west_close')
        time.sleep(2)
        self._move_dome('east_close')

    def open_side(self, side, steps):
        if side == 'west':
            self._move_dome_steps('west_open', steps)
        elif side == 'east':
            self._move_dome_steps('east_open', steps)

    def close_side(self, side, steps):
        if side == 'west':
            self._move_dome_steps('west_close', steps)
        elif side == 'east':
            self._move_dome_steps('east_close', steps)


########################################################################
# New AstroHaven dome class (based on Warwick 1m control)
class AstroHavenDome:
    def __init__(self, serial_port='/dev/ttyS0', stop_length=3):
        self.serial_port = serial_port
        self.stop_length = stop_length
        self.port_props = {'baudrate': 9600, 'parity': 'N',
                           'bytesize': 8, 'stopbits': 1,
                           'rtscts': 0, 'xonxoff': 0,
                           'timeout': 1}
        self.move_code = {'south':{'open':b'a','close':b'A'},
                          'north':{'open':b'b','close':b'B'}}
        self.move_time = {'south':{'open':25.,'close':26.},
                          'north':{'open':24.,'close':24.}}

        self.fake = False

        self.status_H = {'north':'ERROR', 'south':'ERROR'}
        self.status_A = {'north':'ERROR', 'south':'ERROR', 'hatch':'ERROR'}
        self.honeywell_was_triggered = {'north':0, 'south':0}
        self.status = None

        self.heartbeat_error = 0
        self.arduino_error = 0

        self.side = ''
        self.frac = 1
        self.command = ''
        self.timeout = 40.

        self.output_thread_running = 0
        self.status_thread_running = 0

    def _read_heartbeat(self):
        try:
            with serial.Serial(self.serial_port, **self.port_props) as dome_port:
                x = dome_port.read(1).decode('ascii')
            self._parse_heartbeat_status(x)
            return 0
        except:
            self.heartbeat_error = 1
            self.status_H['north'] = 'ERROR'
            self.status_H['south'] = 'ERROR'
            return 1

    def _parse_heartbeat_status(self, status_character):
        # save previous status
        self.old_status_H = self.status_H.copy()
        ## Non-moving statuses
        # returned when we're NOT sending command bytes
        if status_character == '0':
            self.status_H['north'] = 'closed'
            self.status_H['south'] = 'closed'
        elif status_character == '1':
            self.status_H['north'] = 'part_open'
            self.status_H['south'] = 'closed'
        elif status_character == '2':
            self.status_H['north'] = 'closed'
            self.status_H['south'] = 'part_open'
        elif status_character == '3':
            self.status_H['north'] = 'part_open'
            self.status_H['south'] = 'part_open'
        ## Moving statuses
        # returned when we ARE sending command bytes
        elif status_character == 'a':
            self.status_H['south'] = 'opening'
        elif status_character == 'A':
            self.status_H['south'] = 'closing'
        elif status_character == 'b':
            self.status_H['north'] = 'opening'
        elif status_character == 'B':
            self.status_H['north'] = 'closing'
        elif status_character == 'x':
            self.status_H['south'] = 'full_open'
        elif status_character == 'X':
            self.status_H['south'] = 'closed'
        elif status_character == 'y':
            self.status_H['north'] = 'full_open'
        elif status_character == 'Y':
            self.status_H['north'] = 'closed'
        else:
            self.heartbeat_error = 1
            self.status_H['north'] = 'ERROR'
            self.status_H['south'] = 'ERROR'
        return

    def _read_arduino(self):
        loc = params.ARDUINO_LOCATION
        try:
            arduino = getoutput('curl -s %s' %loc)
            data = json.loads(arduino)
            self._parse_arduino_status(data)
            return 0
        except:
            self.arduino_error = 1
            self.status_A['north'] = 'ERROR'
            self.status_A['south'] = 'ERROR'
            self.status_A['hatch'] = 'ERROR'
            return 1

    def _parse_arduino_status(self, status_dict):
            # save previous status
            self.old_status_A = self.status_A.copy()
            try:
                assert status_dict['switch_a'] in [0,1]
                assert status_dict['switch_b'] in [0,1]
                assert status_dict['switch_c'] in [0,1]
                assert status_dict['switch_d'] in [0,1]

                all_closed   = status_dict['switch_a']
                north_open   = status_dict['switch_b']
                south_open   = status_dict['switch_c']
                hatch_closed = status_dict['switch_d']

                if all_closed:
                    if not north_open:
                        self.status_A['north'] = 'closed'
                    else:
                        self.status_A['north'] = 'ERROR'
                    if not south_open:
                        self.status_A['south'] = 'closed'
                    else:
                        self.status_A['south'] = 'ERROR'
                else:
                    if north_open:
                        self.status_A['north'] = 'full_open'
                    else:
                        self.status_A['north'] = 'part_open'
                    if south_open:
                        self.status_A['south'] = 'full_open'
                    else:
                        self.status_A['south'] = 'part_open'

                if hatch_closed:
                    self.status_A['hatch'] = 'closed'
                else:
                    self.status_A['hatch'] = 'open'

                # the Honeywells need memory, in case the built-in
                # sensors fail again

                ## NOTE
                ## THIS LOGIC HASN'T BEEN TESTED
                ## YOU'D HAVE TO DISENGAGE THE DOME LIMIT SWITCHES
                ## OR WAIT FOR IT TO HAPPEN
                ## FINGERS CROSSED

                for side in ['north','south']:
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
                        if (self.honeywell_was_triggered[side] and
                            self.status_H[side] == 'opening'):
                            # Oh dear, it's flicked past the Honeywells
                            # and it's still going!!
                            print('agggg')
                            self.status_A[side] == 'full_open'
                            self.output_thread_running = 0 # to be sure
                        elif (self.honeywell_was_triggered[side] and
                            self.status_H[side] == 'closing'):
                            # It's moving back, clear the memory
                            self.honeywell_was_triggered[side] = 0

            except:
                self.arduino_error = 1
                self.status_A['north'] = 'ERROR'
                self.status_A['south'] = 'ERROR'
                self.status_A['hatch'] = 'ERROR'
            return

    def _read_status(self):
        '''Check the dome status
        reported by both the dome heartbeat and the arduino'''

        # check heartbeat
        self._read_heartbeat()

        #check arduino
        self._read_arduino()

        #print(self.status_H['north'], '\t', self.status_A['north'])
        #print(self.status_H['south'], '\t', self.status_A['south'])
        #print(self.status_A['hatch'])

        status = {}

        # Only the arduino reports the hatch
        status['hatch'] = self.status_A['hatch']

        # dome logic
        for side in ['north', 'south']:
                status_H = self.status_H[side]
                status_A = self.status_A[side]

                # Chose which dome status to report
                if status_H == status_A:
                    # arbitrary
                    status[side] = status_H
                elif status_H == 'ERROR' and status_A != 'ERROR':
                    # go with the one that is still working
                    status[side] = status_A
                elif status_A == 'ERROR' and status_H != 'ERROR':
                    # go with the one that is still working
                    status[side] = status_H
                elif status_H[-3:] == 'ing':
                    if status_A == 'part_open':
                        # arduino can't tell if it's moving
                        status[side] = status_H
                    else: # closed or full_open
                        # arduino says it's reached the limit,
                        # but it hasn't stopped!!
                        status[side] = status_A
                elif status_H == 'part_open':
                    # arduino says closed or full_open
                    status[side] = status_A
                elif status_A == 'part_open':
                    # heartbeat says closed or full_open
                    status[side] = status_H
                else:
                    # if one says closed and the other says full_open
                    # or something totally unexpected
                    status[side] = 'ERROR'
        return status

    def _check_status(self):
        ### start status check thread
        self.status_thread_running = 1
        st = threading.Thread(target=self._status_thread)
        st.daemon = True
        st.start()

    def _status_thread(self):
        start_time = time.time()
        while self.status_thread_running:
            self.status = self._read_status()
            time.sleep(0.5)

    def _output_thread(self):
        side = self.side
        frac = self.frac
        command = self.command
        timeout = self.timeout
        start_time = time.time()
        while self.output_thread_running:
            running_time = time.time() - start_time
            if command == 'open' and self.status[side] == 'full_open':
                print('Dome at limit')
                self.output_thread_running = 0
                break
            elif command == 'close' and self.status[side] == 'closed':
                print('Dome at limit')
                self.output_thread_running = 0
                break
            elif (frac != 1 and
                running_time > self.move_time[side][command]*frac):
                print('Dome moved requested fraction')
                self.output_thread_running = 0
                break
            elif running_time > timeout:
                print('Dome moving timed out')
                self.output_thread_running = 0
                break
            elif self.status[side] == 'ERROR'::
                print('All sensors failed, stopping movement')
                self.output_thread_running = 0
                break

            #print(side, frac, 'o:', self.move_code[side][command])

            with serial.Serial(self.serial_port, **self.port_props) as p:
                l = p.write(self.move_code[side][command])

            time.sleep(0.5)

        self.side = ''
        self.command = ''
        self.timeout = 40.

    def halt(self):
        '''To stop the output thread'''
        self.output_thread_running = 0

    def _move_dome(self, side, command, frac, timeout=40.):
        #'''Internal (blocking) function to keep moving dome until it reaches its limit'''
        self.side = side
        self.frac = frac
        self.command = command
        self.timeout = timeout

        # Don't interupt!
        if self.status[side] in ['opening','closing']:
            return

        ### start output thread
        if not self.output_thread_running:
            print('starting to move:', side, command, frac)
            self.output_thread_running = 1
            ot = threading.Thread(target=self._output_thread)
            ot.daemon = True
            ot.start()
            return

    def open_full(self, side, frac=1):
        self.sound_alarm(7)
        self._move_dome(side, 'open', frac)
        return

    def close_full(self, side, frac=1):
        self.sound_alarm(7)
        self._move_dome(side, 'close', frac)
        return

    def sound_alarm(self,duration=3,sleep=True):
        '''Sound the dome alarm using the Arduino

        duration : int [0-9]
            The time to sound the alarm for (seconds)
            default = 3

        sleep : bool
            Whether to sleep for the duration of the alarm
            or return immediately
            default = True
        '''
        loc = params.ARDUINO_LOCATION
        curl = getoutput('curl -s {}?s{}'.format(loc, duration))
        if sleep:
            time.sleep(duration)
        return


########################################################################
# AstroHaven dome class (based on KNU SLODAR dome control)
class OldAstroHavenDome:
    def __init__(self,serial_port='/dev/ttyS1',stop_length=3):
        self.serial_port = serial_port
        self.stop_length = stop_length
        self.port_props = {'baudrate': 9600, 'parity': 'N',
                           'bytesize': 8, 'stopbits': 1,
                           'rtscts': 0, 'xonxoff': 0,
                           'timeout': 1}
        self.move_code = {'west_open':'a', 'west_close':'A', 'east_open':'b', 'east_close':'B'}
        self.limit_code = {'west_open':'x', 'west_close':'X', 'east_open':'y', 'east_close':'Y'}
        self.fake = False

    def _move_dome(self,command,timeout=40.):
        '''Internal (blocking) function to keep moving dome until it reaches its limit'''
        received = ''
        stop_signal = self.stop_length * self.limit_code[command]
        print('Expecting stop on',stop_signal)
        start_time = time.time()
        while True:
            self.dome_port.write(self.move_code[command])
            x = self.dome_port.read(1)
            print(x, end=' ')
            received += x
            if received[-self.stop_length:] == stop_signal:
                print(received)
                return received
            elif time.time() - start_time > timeout:
                print('Dome moving timed out')
                print(received)
                return received
            time.sleep(0.1)

    def _move_dome_steps(self,command,steps):
        '''Internal (blocking) function to move dome a fixed number of (stop-start) steps'''
        received = ''
        for i in range(steps):
            self.dome_port.write(move_code[command])
            time.sleep(3)
            x = self.dome_port.read(1)
            print(x, end=' ')
            received += x
        print(received)
        return received

    def status(self):
        '''Check the status as reported by the arduino'''
        status = {'dome':'ERROR','hatch':'ERROR'}
        pin_dict = {'pin2':-1,'pin3':-1,'pin5':-1,'pin6':-1,'pin7':-1}
        try:
            curl = getoutput('curl -s dome')
            ard = remove_html_tags(curl).split()
            for i in range(len(ard)):
                if ard[i] == 'pin':
                    n = int(ard[i+1])
                    if ard[i+2] == 'HIGH':
                        pin_dict['pin%i' %n] = 1
                    if ard[i+2] == 'LOW':
                        pin_dict['pin%i' %n] = 0

            pins = []
            for n in [2,3,6,7]:
                pins.append(pin_dict['pin%n' %n])

            if pins.count(1) == len(pins):
                statdict['dome'] = 'open'
            elif pins.count(0) == len(pins):
                statdict['dome'] = 'close'
            else:
                statdict['dome'] = 'unknown'
            statdict['hatch'] = 'unknown'
        except:
            pass
        return status

    def open_full(self):
        #self.sound_alarm()
        # by using the serial port as a context manager it will still close if
        # an exception is raised inside _move_dome
        with serial.Serial(self.serial_port, **self.port_props) as self.dome_port:
            openW = self._move_dome('west_open')
            time.sleep(2)
            openE = self._move_dome('east_open')
        print(openW, openE)
        return openW.strip() + openE.strip()

    def close_full(self):
        #self.sound_alarm()
        with serial.Serial(self.serial_port, **self.port_props) as self.dome_port:
            closeW = self._move_dome('west_close')
            time.sleep(2)
            closeE = self._move_dome('east_close')
        print(closeW, closeE)
        return closeW.strip() + closeE.strip()

    def open_side(self,side,steps):
        #self.sound_alarm()
        with serial.Serial(self.serial_port, **self.port_props) as self.dome_port:
            if side == 'west':
                openS = self._move_dome_steps('west_open',steps)
            elif side == 'east':
                openS = self._move_dome_steps('east_open',steps)
        return openS.strip()

    def close_side(self,side,steps):
        #self.sound_alarm()
        with serial.Serial(self.serial_port, **self.port_props) as self.dome_port:
            if side == 'west':
                closeS = self._move_dome_steps('west_close',steps)
            elif side == 'east':
                closeS = self._move_dome_steps('east_close',steps)
        return closeS.strip()

    def sound_alarm(self,sleep=True):
        '''Sound the dome alarm using the arduino'''
        curl = getoutput('curl -s dome?s')
        if sleep:
            time.sleep(5)

########################################################################
# Direct control
if __name__ == '__main__':
    dome = AstroHavenDome(params.DOME_LOCATION)
    try:
        if sys.argv[1] == 'open':
            dome.open_full()
        elif sys.argv[1] == 'close':
            dome.close_full()
        elif sys.argv[1] == 'status':
            print(dome.status())
        elif sys.argv[1] == 'alarm':
            dome.sound_alarm()
        else:
            print('Usage: python dome_control.py status/open/close/alarm')
    except:
        print('Usage: python dome_control.py status/open/close/alarm')
