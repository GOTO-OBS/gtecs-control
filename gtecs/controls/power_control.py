#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                           power_control.py                           #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#       G-TeCS module containing class to control power switches       #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
import os, sys
import socket
import subprocess
import threading
import time
import shutil
from six.moves import range
from six import int2byte, byte2int, indexbytes

########################################################################
# Fake classes
class FakePDU:
    def __init__(self, IP_address):
        self.unit_type = 'PDU'
        self.IP_address = IP_address
        self.count = 8
        self.outlets = list(range(1, self.count+1))
        self.off_value = 0
        self.on_value = 1
        # fake stuff
        self.temp_file = '/tmp/power_'+self.IP_address
        self.outlet_status = [self.off_value]*self.count
        self._read_temp()

    def _read_temp(self):
        if not os.path.exists(self.temp_file):
            self.outlet_status = [self.off_value]*self.count
            self._write_temp()
        else:
            f = open(self.temp_file, 'r')
            self.outlet_status = list(f.read().strip())
            f.close()

    def _write_temp(self):
        f = open(self.temp_file,'w')
        f.write(''.join(str(i) for i in self.outlet_status))
        f.close()

    def status(self):
        self._read_temp()
        return ''.join(self.outlet_status)

    def on(self,outlet):
        if outlet == 0: # all
            self.outlet_status = [self.on_value]*self.count
        else:
            self.outlet_status[outlet-1] = self.on_value
        self._write_temp()

    def off(self,outlet):
        if outlet == 0: # all
            self.outlet_status = [self.off_value]*self.count
        else:
            self.outlet_status[outlet-1] = self.off_value
        self._write_temp()

    def reboot(self,outlet):
        self.off(outlet)
        t = threading.Thread(target=self._turn_on_after_reboot, args = [outlet])
        t.start()

    def _turn_on_after_reboot(self,outlet):
        time.sleep(3)
        self.on(outlet)


class FakeUPS:
    def __init__(self, IP_address):
        self.unit_type = 'UPS'
        self.IP_address = IP_address
        self.statuses = {'1':'UNKNOWN', '2':'NORMAL', '3':'LOW'}

    def status(self):
        status = self.statuses['2']
        return status

    def percent_remaining(self):
        percent = 100.0
        return percent

    def time_remaining(self):
        seconds = 65535.0
        return seconds

########################################################################
# APC PDU power class (for AP7921, 8 ports)

class APCPDU:
    def __init__(self,IP_address):
        self.unit_type = 'PDU'
        self.IP_address = IP_address
        self.commands = {'ON':'1', 'OFF':'2', 'REBOOT':'3'}
        self.count = 8
        self.outlets = list(range(1, self.count+1))
        self.on_value = 1
        self.off_value = 2

    def _initialise_oid_array(self, outlet):
        """ Setup the oid array to use with snmpget and snmpset """
        base = '.1.3.6.1.4.1.318.1.1.12.3.3.1.1.4'
        if outlet in self.outlets:
            oid_arr = [base + '.' + str(outlet)]
        elif outlet == 0: # all
            oid_arr = [base + '.' + str(outlet) for outlet in self.outlets]
        else:
            raise ValueError('Invalid outlet')
        return oid_arr

    def _snmpget(self, oid_arr):
        """ Get a value using snmpget """
        snmpget = shutil.which('snmpget')
        if snmpget is None:
            raise OSError('SNMP tools not installed')
        IP = self.IP_address
        command = [snmpget, '-v', '1', '-c', 'public', IP] + oid_arr
        output = subprocess.check_output(command).decode('ascii').split('\n')
        status = ''
        for i in range(len(output)-1):
            status += output[i][-1]
        return status

    def _snmpset(self, oid_arr, value):
        """ Set a value using snmpset """
        snmpset = shutil.which('snmpset')
        if snmpset is None:
            raise OSError('SNMP tools not installed')
        IP = self.IP_address
        command_oid_arr = []
        for oid in oid_arr:
            command_oid_arr += [oid, 'i', value]
        command = [snmpset, '-v', '1', '-c', 'public', IP] + command_oid_arr
        output = subprocess.check_output(command).decode('ascii').split('\n')
        status = ''
        for i in range(len(output)-1):
            status += output[i][-1]
        return status

    def status(self):
        outlet = 0 # all
        oid_arr = self._initialise_oid_array(outlet)
        out = self._snmpget(oid_arr)
        return out

    def on(self, outlet):
        oid_arr = self._initialise_oid_array(outlet)
        out = self._snmpset(oid_arr, self.commands['ON'])
        return out

    def off(self, outlet):
        oid_arr = self._initialise_oid_array(outlet)
        out = self._snmpset(oid_arr, self.commands['OFF'])
        return out

    def reboot(self, outlet):
        oid_arr = self._initialise_oid_array(outlet)
        out = self._snmpset(oid_arr, self.commands['REBOOT'])
        return out

########################################################################
# APC UPS power class (for Smart-UPS X 3000)

class APCUPS:
    def __init__(self,IP_address):
        self.unit_type = 'UPS'
        self.IP_address = IP_address
        self.command_oids = {'STATUS':'2.1.1.0',
                             'PERCENT':'2.2.1.0',
                             'TIME':'2.2.3.0'}
        self.statuses = {'1':'UNKNOWN', '2':'NORMAL', '3':'LOW'}

    def _initialise_oid_array(self, command_oid):
        """ Setup the oid array to use with snmpget and snmpset """
        base = '.1.3.6.1.4.1.318.1.1.1'
        oid_arr = [base + '.' + str(command_oid)]
        return oid_arr

    def _snmpget(self, oid_arr):
        """ Get a value using snmpget """
        snmpget = shutil.which('snmpget')
        if snmpget is None:
            raise OSError('SNMP tools not installed')
        IP = self.IP_address
        command = [snmpget, '-v', '1', '-c', 'public', IP] + oid_arr
        output = subprocess.check_output(command).decode('ascii').split('\n')
        status = output[0].split(' ')[-1]
        return status

    def status(self):
        oid_arr = self._initialise_oid_array(self.command_oids['STATUS'])
        out = self._snmpget(oid_arr)
        status = self.statuses[out]
        return status

    def percent_remaining(self):
        oid_arr = self._initialise_oid_array(self.command_oids['PERCENT'])
        out = self._snmpget(oid_arr)
        percent = float(out)
        return percent

    def time_remaining(self):
        oid_arr = self._initialise_oid_array(self.command_oids['TIME'])
        out = self._snmpget(oid_arr)
        hms = out.split(':')
        seconds = int(hms[0])*3600 + int(hms[1])*60 + float(hms[2])
        return seconds

########################################################################
# Ethernet relay power class (for ETH8020, 20 ports)

class EthPower:
    def __init__(self, IP_address, port):
        self.IP_address = IP_address
        self.port = port
        self.commands = {'ON':b'\x20', 'OFF':b'\x21', 'ALL':b'\x23', 'STATUS':b'\x24'}
        self.count = 20
        self.off_value = 0
        self.reboot_time = 5  # seconds
        self.buffer_size = 1024

    def tcp_command(self, command):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        IP = self.IP_address
        port = self.port
        s.connect((IP,port))
        s.send(command)
        reply = s.recv(self.buffer_size)
        s.close()
        return reply

    def on(self, outlet):
        num = int(outlet)
        if num == 0:
            command = self.commands['ALL'] + b'\xff' + b'\xff' + b'\xff'
        else:
            command = self.commands['ON'] + int2byte(num) + b'\x00'
        return byte2int(self.tcp_command(command))

    def off(self, outlet):
        num = int(outlet)
        if num == 0:
            command = self.commands['ALL'] + b'\x00' + b'\x00' + b'\x00'
        else:
            command = self.commands['OFF'] + int2byte(num) + b'\x00'
        return byte2int(self.tcp_command(command))

    def reboot(self, outlet):
        num = int(outlet)
        time = int(self.reboot_time*10)  # relay takes 0.1s intervals
        if num == 0:
            cmd_arr = [self.commands['OFF'] + int2byte(n) + int2byte(time)
                       for n in range(1, self.count + 1)]
            command = b''.join(cmd_arr)
        else:
            command = self.commands['OFF'] + int2byte(num) + int2byte(time)
        output = self.tcp_command(command)
        if len(output) == 1:
            return int2byte(output)
        elif b'\x01' in output:
            return 1
        else:
            return 0

    def status(self, outlet):
        num = int(outlet)
        output = self.tcp_command(self.commands['STATUS'])
        status_ints = [indexbytes(output, x) for x in range(len(output))]
        status_strings = [str(bin(i))[2::] for i in status_ints]
        status_strings[0] = status_strings[0].zfill(8)[::-1]
        status_strings[1] = status_strings[1].zfill(8)[::-1]
        status_strings[2] = status_strings[2].zfill(4)[::-1]
        status_string = ''.join(status_strings)
        if num == 0:
            return status_string
        else:
            return status_string[num - 1]
