"""Classes to control power switches and UPSs."""

import os
import shutil
import socket
import subprocess
import threading
import time

from six import byte2int, indexbytes, int2byte


class FakePDU(object):
    """Fake PDU power class (8 ports)."""

    def __init__(self, address):
        self.unit_type = 'PDU'
        self.address = address
        self.count = 8
        self.outlets = list(range(1, self.count + 1))
        self.off_value = 0
        self.on_value = 1
        # fake stuff
        self._temp_file = '/tmp/pdu_' + self.address
        self._outlet_status = [self.off_value] * self.count
        self._read_temp()

    def _read_temp(self):
        if not os.path.exists(self._temp_file):
            self._outlet_status = [self.off_value] * self.count
            self._write_temp()
        else:
            f = open(self._temp_file, 'r')
            self._outlet_status = list(f.read().strip())
            f.close()

    def _write_temp(self):
        f = open(self._temp_file, 'w')
        f.write(''.join(str(i) for i in self._outlet_status))
        f.close()

    def status(self):
        """Return the current status of the outlets."""
        self._read_temp()
        return ''.join(self._outlet_status)

    def on(self, outlet):
        """Turn on the given outlet."""
        if outlet == 0:  # all
            self._outlet_status = [self.on_value] * self.count
        else:
            self._outlet_status[outlet - 1] = self.on_value
        self._write_temp()

    def off(self, outlet):
        """Turn off the given outlet."""
        if outlet == 0:  # all
            self._outlet_status = [self.off_value] * self.count
        else:
            self._outlet_status[outlet - 1] = self.off_value
        self._write_temp()

    def reboot(self, outlet):
        """Reboot the given outlet."""
        self.off(outlet)
        t = threading.Thread(target=self._turn_on_after_reboot, args=[outlet])
        t.start()

    def _turn_on_after_reboot(self, outlet):
        time.sleep(3)
        self.on(outlet)


class FakeUPS(object):
    """Fake UPS power class."""

    def __init__(self, address):
        self.unit_type = 'UPS'
        self.address = address
        self.statuses = {'1': 'UNKNOWN', '2': 'Normal', '3': 'LOW'}
        self.count = 3
        self.outlets = list(range(1, self.count + 1))
        self.on_value = 1
        self.off_value = 0
        # fake stuff
        self._temp_file = '/tmp/ups_' + self.address
        self._outlet_status = [self.off_value] * self.count
        self._read_temp()

    def _read_temp(self):
        if not os.path.exists(self._temp_file):
            self._outlet_status = [self.off_value] * self.count
            self._write_temp()
        else:
            f = open(self._temp_file, 'r')
            self._outlet_status = list(f.read().strip())
            f.close()

    def _write_temp(self):
        f = open(self._temp_file, 'w')
        f.write(''.join(str(i) for i in self._outlet_status))
        f.close()

    def status(self):
        """Return the current status of the UPS."""
        status = self.statuses['2']
        return status

    def percent_remaining(self):
        """Return the current power percentage remaining in the UPS."""
        percent = 100.0
        return percent

    def time_remaining(self):
        """Return the current power time remaining in the UPS."""
        seconds = 65535.0
        return seconds

    def load(self):
        """Return the current load on the UPS."""
        percent = 50.0
        return percent

    def outlet_status(self):
        """Return the current status of the outlets."""
        self._read_temp()
        return ''.join(self._outlet_status)

    def on(self, outlet):
        """Turn on the given outlet."""
        if outlet == 0:  # all
            self._outlet_status = [self.on_value] * self.count
        else:
            self._outlet_status[outlet - 1] = self.on_value
        self._write_temp()

    def off(self, outlet):
        """Turn off the given outlet."""
        if outlet == 0:  # all
            self._outlet_status = [self.off_value] * self.count
        else:
            self._outlet_status[outlet - 1] = self.off_value
        self._write_temp()

    def reboot(self, outlet):
        """Reboot the given outlet."""
        self.off(outlet)
        t = threading.Thread(target=self._turn_on_after_reboot, args=[outlet])
        t.start()

    def _turn_on_after_reboot(self, outlet):
        time.sleep(3)
        self.on(outlet)


class APCPDU(object):
    """APC PDU power class (for AP7921, 8 ports)."""

    def __init__(self, address):
        self.unit_type = 'PDU'
        self.address = address
        self.commands = {'ON': '1', 'OFF': '2', 'REBOOT': '3'}
        self.count = 8
        self.outlets = list(range(1, self.count + 1))
        self.on_value = 1
        self.off_value = 2

    def _initialise_oid_array(self, outlet):
        """Set up the oid array to use with snmpget and snmpset."""
        base = '.1.3.6.1.4.1.318.1.1.12.3.3.1.1.4'
        if outlet in self.outlets:
            oid_arr = [base + '.' + str(outlet)]
        elif outlet == 0:  # all
            oid_arr = [base + '.' + str(outlet) for outlet in self.outlets]
        else:
            raise ValueError('Invalid outlet')
        return oid_arr

    def _snmpget(self, oid_arr):
        """Get a value using snmpget."""
        snmpget = shutil.which('snmpget')
        if snmpget is None:
            raise OSError('SNMP tools not installed')
        address = self.address
        command = [snmpget, '-v', '1', '-c', 'public', address] + oid_arr
        output = subprocess.check_output(command).decode('ascii').split('\n')
        status = ''
        for i in range(len(output) - 1):
            status += output[i][-1]
        return status

    def _snmpset(self, oid_arr, value):
        """Set a value using snmpset."""
        snmpset = shutil.which('snmpset')
        if snmpset is None:
            raise OSError('SNMP tools not installed')
        address = self.address
        command_oid_arr = []
        for oid in oid_arr:
            command_oid_arr += [oid, 'i', value]
        command = [snmpset, '-v', '1', '-c', 'private', address] + command_oid_arr
        output = subprocess.check_output(command).decode('ascii').split('\n')
        status = ''
        for i in range(len(output) - 1):
            status += output[i][-1]
        return status

    def status(self):
        """Return the current status of the outlets."""
        outlet = 0  # all
        oid_arr = self._initialise_oid_array(outlet)
        out = self._snmpget(oid_arr)
        return out

    def on(self, outlet):
        """Turn on the given outlet."""
        oid_arr = self._initialise_oid_array(outlet)
        out = self._snmpset(oid_arr, self.commands['ON'])
        return out

    def off(self, outlet):
        """Turn off the given outlet."""
        oid_arr = self._initialise_oid_array(outlet)
        out = self._snmpset(oid_arr, self.commands['OFF'])
        return out

    def reboot(self, outlet):
        """Reboot the given outlet."""
        oid_arr = self._initialise_oid_array(outlet)
        out = self._snmpset(oid_arr, self.commands['REBOOT'])
        return out


class APCUPS(object):
    """APC UPS power class (for Smart-UPS X 3000)."""

    def __init__(self, address):
        self.unit_type = 'UPS'
        self.address = address
        self.command_oids = {'STATUS': '4.1.1.0',
                             'PERCENT': '2.3.1.0',
                             'TIME': '2.2.3.0',
                             'LOAD': '4.3.3.0',
                             'OUTLET': '12.3.2.1.3'}
        self.commands = {'ON': '1', 'OFF': '2', 'REBOOT': '3'}
        self.statuses = {'1': 'UNKNOWN',
                         '2': 'Normal',
                         '3': 'ON BATTERY',
                         '4': 'onSmartBoost',
                         '5': 'timedSleeping',
                         '6': 'softwareBypass',
                         '7': 'off',
                         '8': 'rebooting',
                         '9': 'switchedBypass',
                         '10': 'hardwareFailureBypass',
                         '11': 'sleepingUntilPowerReturn',
                         '12': 'onSmartTrim',
                         '13': 'ecoMode',
                         '14': 'hotStandby',
                         '15': 'onBatteryTest'}
        self.count = 3
        self.outlets = list(range(1, self.count + 1))
        self.on_value = 1
        self.off_value = 2

    def _initialise_oid_array(self, command_oid, outlet=None):
        """Set up the oid array to use with snmpget and snmpset."""
        base = '.1.3.6.1.4.1.318.1.1.1'
        if outlet in self.outlets:
            oid_arr = [base + '.' + str(command_oid) + '.' + str(outlet)]
        elif outlet == 0:  # all
            oid_arr = [base + '.' + str(command_oid) + '.' + str(outlet) for outlet in self.outlets]
        elif outlet:
            raise ValueError('Invalid outlet')
        else:
            oid_arr = [base + '.' + str(command_oid)]
        return oid_arr

    def _snmpget(self, oid_arr):
        """Get a value using snmpget."""
        snmpget = shutil.which('snmpget')
        if snmpget is None:
            raise OSError('SNMP tools not installed')
        address = self.address
        command = [snmpget, '-v', '1', '-c', 'public', address] + oid_arr
        output = subprocess.check_output(command).decode('ascii').split('\n')
        status = ''
        for i in range(len(output) - 1):
            status += output[i].split(' ')[-1]
        return status

    def _snmpset(self, oid_arr, value):
        """Set a value using snmpset."""
        snmpset = shutil.which('snmpset')
        if snmpset is None:
            raise OSError('SNMP tools not installed')
        address = self.address
        command_oid_arr = []
        for oid in oid_arr:
            command_oid_arr += [oid, 'i', value]
        command = [snmpset, '-v', '1', '-c', 'private', address] + command_oid_arr
        output = subprocess.check_output(command).decode('ascii').split('\n')
        status = ''
        for i in range(len(output) - 1):
            status += output[i][-1]
        return status

    def status(self):
        """Return the current status of the UPS."""
        oid_arr = self._initialise_oid_array(self.command_oids['STATUS'])
        out = self._snmpget(oid_arr)
        status = self.statuses[out]
        return status

    def percent_remaining(self):
        """Return the current power percentage remaining in the UPS."""
        oid_arr = self._initialise_oid_array(self.command_oids['PERCENT'])
        out = self._snmpget(oid_arr)
        percent = float(out) / 10.
        return percent

    def time_remaining(self):
        """Return the current power time remaining in the UPS."""
        oid_arr = self._initialise_oid_array(self.command_oids['TIME'])
        out = self._snmpget(oid_arr)
        hms = out.split(':')
        seconds = int(hms[0]) * 3600 + int(hms[1]) * 60 + float(hms[2])
        return seconds

    def load(self):
        """Return the current load on the UPS."""
        oid_arr = self._initialise_oid_array(self.command_oids['LOAD'])
        out = self._snmpget(oid_arr)
        percent = float(out) / 10.
        return percent

    def outlet_status(self):
        """Return the current status of the outlets."""
        outlet = 0  # all
        oid_arr = self._initialise_oid_array(self.command_oids['OUTLET'], outlet)
        out = self._snmpget(oid_arr)
        return out

    def on(self, outlet):
        """Turn on the given outlet."""
        oid_arr = self._initialise_oid_array(self.command_oids['OUTLET'], outlet)
        out = self._snmpset(oid_arr, self.commands['ON'])
        return out

    def off(self, outlet):
        """Turn off the given outlet."""
        oid_arr = self._initialise_oid_array(self.command_oids['OUTLET'], outlet)
        out = self._snmpset(oid_arr, self.commands['OFF'])
        return out

    def reboot(self, outlet):
        """Reboot the given outlet."""
        oid_arr = self._initialise_oid_array(self.command_oids['OUTLET'], outlet)
        out = self._snmpset(oid_arr, self.commands['REBOOT'])
        return out


class ETH8020(object):
    """Ethernet relay power class (for ETH8020, 20 ports)."""

    def __init__(self, address, port, normally_closed=False):
        self.unit_type = 'PDU'
        self.address = address
        self.port = port
        if not normally_closed:
            self.commands = {'ON': b'\x20', 'OFF': b'\x21', 'ALL': b'\x23', 'STATUS': b'\x24'}
            self.on_value = 1
            self.off_value = 0
        else:
            self.commands = {'ON': b'\x21', 'OFF': b'\x20', 'ALL': b'\x23', 'STATUS': b'\x24'}
            self.on_value = 0
            self.off_value = 1
        self.count = 20
        self.outlets = list(range(1, self.count + 1))
        self.reboot_time = 5  # seconds
        self.buffer_size = 1024

        # Create one persistent socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.connect((self.address, self.port))

    def __del__(self):
        self.socket.shutdown(socket.SHUT_RDWR)
        self.socket.close()

    def _tcp_command(self, command_bytes):
        """Send command bytes to the device, then fetch the reply bytes and return them."""
        try:
            self.socket.send(command_bytes)
            reply = self.socket.recv(self.buffer_size)
            return reply
        except Exception as error:
            return 'Socket error: {}'.format(error)

    def status(self):
        """Return the current status of the outlets."""
        out = self._tcp_command(self.commands['STATUS'])
        status_ints = [indexbytes(out, x) for x in range(len(out))]
        status_strings = [str(bin(i))[2::] for i in status_ints]
        status_strings[0] = status_strings[0].zfill(8)[::-1]
        status_strings[1] = status_strings[1].zfill(8)[::-1]
        status_strings[2] = status_strings[2].zfill(4)[::-1]
        status_string = ''.join(status_strings)
        return status_string

    def on(self, outlet):
        """Turn on the given outlet."""
        if outlet == 0:
            command = self.commands['ALL'] + b'\xff' + b'\xff' + b'\xff'
        else:
            command = self.commands['ON'] + int2byte(outlet) + b'\x00'
        out = byte2int(self._tcp_command(command))
        return out

    def off(self, outlet):
        """Turn off the given outlet."""
        if outlet == 0:
            command = self.commands['ALL'] + b'\x00' + b'\x00' + b'\x00'
        else:
            command = self.commands['OFF'] + int2byte(outlet) + b'\x00'
        out = byte2int(self._tcp_command(command))
        return out

    def reboot(self, outlet):
        """Reboot the given outlet."""
        reboot_time = int(self.reboot_time * 10)  # relay takes 0.1s intervals
        if outlet == 0:
            cmd_arr = [self.commands['OFF'] + int2byte(n) + int2byte(reboot_time)
                       for n in self.outlets]
            command = b''.join(cmd_arr)
        else:
            command = self.commands['OFF'] + int2byte(outlet) + int2byte(reboot_time)
        out = byte2int(self._tcp_command(command))
        return out


class ETH002(object):
    """Ethernet relay power class (for ETH002, 2 ports)."""

    def __init__(self, address, port, normally_closed=False):
        self.address = address
        self.port = port
        if not normally_closed:
            self.commands = {'ON': b'\x20', 'OFF': b'\x21', 'ALL': b'\x23', 'STATUS': b'\x24'}
            self.on_value = 1
            self.off_value = 0
        else:
            self.commands = {'ON': b'\x21', 'OFF': b'\x20', 'ALL': b'\x23', 'STATUS': b'\x24'}
            self.on_value = 0
            self.off_value = 1
        self.count = 2
        self.outlets = list(range(1, self.count + 1))
        self.reboot_time = 5  # seconds
        self.buffer_size = 1024

        # Create one persistent socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.connect((self.address, self.port))

    def __del__(self):
        self.socket.shutdown(socket.SHUT_RDWR)
        self.socket.close()

    def _tcp_command(self, command_bytes):
        """Send command bytes to the device, then fetch the reply bytes and return them."""
        try:
            self.socket.send(command_bytes)
            reply = self.socket.recv(self.buffer_size)
            return reply
        except Exception as error:
            return 'Socket error: {}'.format(error)

    def status(self):
        """Return the current status of the outlets."""
        out = self._tcp_command(self.commands['STATUS'])
        i = byte2int(out)
        status_string = str(bin(i))[2::]
        status_string = status_string.zfill(2)[::-1]
        return status_string

    def on(self, outlet):
        """Turn on the given outlet."""
        if outlet == 0:
            command = self.commands['ALL'] + b'\xff' + b'\xff' + b'\xff'
        else:
            command = self.commands['ON'] + int2byte(outlet) + b'\x00'
        out = byte2int(self._tcp_command(command))
        return out

    def off(self, outlet):
        """Turn off the given outlet."""
        if outlet == 0:
            command = self.commands['ALL'] + b'\x00' + b'\x00' + b'\x00'
        else:
            command = self.commands['OFF'] + int2byte(outlet) + b'\x00'
        out = byte2int(self._tcp_command(command))
        return out

    def reboot(self, outlet):
        """Reboot the given outlet."""
        reboot_time = int(self.reboot_time * 10)  # relay takes 0.1s intervals
        if outlet == 0:
            cmd_arr = [self.commands['OFF'] + int2byte(n) + int2byte(reboot_time)
                       for n in self.outlets]
            command = b''.join(cmd_arr)
        else:
            command = self.commands['OFF'] + int2byte(outlet) + int2byte(reboot_time)
        out = byte2int(self._tcp_command(command))
        return out
