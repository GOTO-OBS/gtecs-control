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
from six.moves import map
from six.moves import range

########################################################################
# Fake AstroHaven dome class
class FakeDome:
    def __init__(self,serial_port='/dev/ttyS1',stop_length=3):
        self.serial_port = serial_port
        self.stop_length = stop_length
        self.move_code = {'west_open':'a', 'west_close':'A', 'east_open':'b', 'east_close':'B'}
        self.limit_code = {'west_open':'x', 'west_close':'X', 'east_open':'y', 'east_close':'Y'}
        # fake stuff
        self.temp_file = '/tmp/dome'
        self._read_temp()

    def _new_temp(self):
        self.domestatus = [0,0,0] # start closed
        self._write_temp()

    def _read_temp(self):
        if not os.path.exists(self.temp_file):
            self._new_temp()
        else:
            f = open(self.temp_file,'r')
            self.domestatus = list(map(int,list(f.read().strip())))
            f.close()

    def _write_temp(self):
        f = open(self.temp_file,'w')
        f.write(''.join(str(i) for i in self.domestatus))
        f.close()

    def _move_dome(self,command,timeout=40.):
        self._read_temp()
        if command[:4] == 'west':
            side = 0
        elif command[:4] == 'east':
            side = 1
        if command[5:] == 'open':
            steps = 9 - self.domestatus[side]
            time.sleep(3*steps)
            self.domestatus[side] = 9
        elif command[5:] == 'close':
            steps = self.domestatus[side]
            time.sleep(3*steps)
            self.domestatus[side] = 0
        self._write_temp()

    def _move_dome_steps(self,command,steps):
        self._read_temp()
        if command[:4] == 'west':
            side = 0
        elif command[:4] == 'east':
            side = 1
        if command[5:] == 'open':
            finish = self.domestatus[side] + steps
            if finish > 9:
                finish = 9
                steps = 9 - self.domestatus[side]
            time.sleep(3*steps)
            self.domestatus[side] = finish
        elif command[5:] == 'close':
            finish = self.domestatus[side] - steps
            if finish < 0:
                finish = 0
                steps = self.domestatus[side]
            time.sleep(3*steps)
            self.domestatus[side] = finish
        self._write_temp()

    def status(self):
        status = {'dome':'ERROR','hatch':'ERROR'}
        self._read_temp()
        if self.domestatus[0] == 0 and self.domestatus[1] == 0:
            status['dome'] = 'closed'
        else:
            status['dome'] = 'open'
        if self.domestatus[2] == 0:
            status['hatch'] = 'closed'
        else:
            status['hatch'] = 'open'
        return status

    def open_full(self):
        #self.sound_alarm()
        #self.dome_port = serial.Serial(self.serial_port,9600,parity='N',bytesize=8,stopbits=1,rtscts=0,xonxoff=0,timeout=1)
        openW = self._move_dome('west_open')
        time.sleep(2)
        openE = self._move_dome('east_open')
        #self.dome_port.close()
        print(openW, openE)
        #return openW.strip() + openE.strip()

    def close_full(self):
        #self.sound_alarm()
        #self.dome_port = serial.Serial(self.serial_port,9600,parity='N',bytesize=8,stopbits=1,rtscts=0,xonxoff=0,timeout=1)
        closeW = self._move_dome('west_close')
        time.sleep(2)
        closeE = self._move_dome('east_close')
        #self.dome_port.close()
        print(closeW, closeE)
        #return closeW.strip() + closeE.strip()

    def open_side(self,side,steps):
        #self.sound_alarm()
        #self.dome_port = serial.Serial(self.serial_port,9600,parity='N',bytesize=8,stopbits=1,rtscts=0,xonxoff=0,timeout=1)
        if side == 'west':
            openS = self._move_dome_steps('west_open',steps)
        elif side == 'east':
            openS = self._move_dome_steps('east_open',steps)
        #self.dome_port.close()
        #return openS.strip()

    def close_side(self,side,steps):
        #self.sound_alarm()
        #self.dome_port = serial.Serial(self.serial_port,9600,parity='N',bytesize=8,stopbits=1,rtscts=0,xonxoff=0,timeout=1)
        if side == 'west':
            closeS = self._move_dome_steps('west_close',steps)
        elif side == 'east':
            closeS = self._move_dome_steps('east_close',steps)
        #self.dome_port.close()
        #return closeS.strip()

    def sound_alarm(self,sleep=True):
        '''Sound the dome alarm using the arduino'''
        curl = getoutput('curl -s dome?s')
        if sleep:
            time.sleep(5)

########################################################################
# AstroHaven dome class (based on KNU SLODAR dome control)
class AstroHavenDome:
    def __init__(self,serial_port='/dev/ttyS1',stop_length=3):
        self.serial_port = serial_port
        self.stop_length = stop_length
        self.move_code = {'west_open':'a', 'west_close':'A', 'east_open':'b', 'east_close':'B'}
        self.limit_code = {'west_open':'x', 'west_close':'X', 'east_open':'y', 'east_close':'Y'}

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
        self.dome_port = serial.Serial(self.serial_port,9600,parity='N',bytesize=8,stopbits=1,rtscts=0,xonxoff=0,timeout=1)
        openW = self._move_dome('west_open')
        time.sleep(2)
        openE = self._move_dome('east_open')
        self.dome_port.close()
        print(openW, openE)
        return openW.strip() + openE.strip()

    def close_full(self):
        #self.sound_alarm()
        self.dome_port = serial.Serial(self.serial_port,9600,parity='N',bytesize=8,stopbits=1,rtscts=0,xonxoff=0,timeout=1)
        closeW = self._move_dome('west_close')
        time.sleep(2)
        closeE = self._move_dome('east_close')
        self.dome_port.close()
        print(closeW, closeE)
        return closeW.strip() + closeE.strip()

    def open_side(self,side,steps):
        #self.sound_alarm()
        self.dome_port = serial.Serial(self.serial_port,9600,parity='N',bytesize=8,stopbits=1,rtscts=0,xonxoff=0,timeout=1)
        if side == 'west':
            openS = self._move_dome_steps('west_open',steps)
        elif side == 'east':
            openS = self._move_dome_steps('east_open',steps)
        self.dome_port.close()
        return openS.strip()

    def close_side(self,side,steps):
        #self.sound_alarm()
        self.dome_port = serial.Serial(self.serial_port,9600,parity='N',bytesize=8,stopbits=1,rtscts=0,xonxoff=0,timeout=1)
        if side == 'west':
            closeS = self._move_dome_steps('west_close',steps)
        elif side == 'east':
            closeS = self._move_dome_steps('east_close',steps)
        self.dome_port.close()
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
