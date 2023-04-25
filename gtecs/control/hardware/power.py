"""Classes to control power switches and UPSs."""

import os
import shutil
import socket
import subprocess
import threading
import time

from six import byte2int, indexbytes, int2byte


class FakePDU:
    """Fake PDU power class."""

    def __init__(self, address, outlets=8):
        self.unit_type = 'PDU'
        self.address = address
        self.outlets = list(range(1, outlets + 1))
        self.off_value = 0
        self.on_value = 1
        # fake stuff
        self._temp_file = '/tmp/pdu_' + self.address
        self._outlet_status = [self.off_value] * len(self.outlets)
        self._read_temp()

    def _read_temp(self):
        if not os.path.exists(self._temp_file):
            self._outlet_status = [self.off_value] * len(self.outlets)
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
            self._outlet_status = [self.on_value] * len(self.outlets)
        else:
            self._outlet_status[outlet - 1] = self.on_value
        self._write_temp()

    def off(self, outlet):
        """Turn off the given outlet."""
        if outlet == 0:  # all
            self._outlet_status = [self.off_value] * len(self.outlets)
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


class FakeUPS:
    """Fake UPS power class."""

    def __init__(self, address, outlets=3):
        self.unit_type = 'UPS'
        self.address = address
        self.statuses = {'1': 'UNKNOWN', '2': 'Normal', '3': 'LOW'}
        self.outlets = list(range(1, outlets + 1))
        self.on_value = 1
        self.off_value = 0
        # fake stuff
        self._temp_file = '/tmp/ups_' + self.address
        self._outlet_status = [self.off_value] * len(self.outlets)
        self._read_temp()

    def _read_temp(self):
        if not os.path.exists(self._temp_file):
            self._outlet_status = [self.off_value] * len(self.outlets)
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
            self._outlet_status = [self.on_value] * len(self.outlets)
        else:
            self._outlet_status[outlet - 1] = self.on_value
        self._write_temp()

    def off(self, outlet):
        """Turn off the given outlet."""
        if outlet == 0:  # all
            self._outlet_status = [self.off_value] * len(self.outlets)
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


class APCPDU:
    """APC Power Distribution Unit class, communicating through SNMP."""

    def __init__(self, address, outlets=8):
        self.unit_type = 'PDU'
        self.address = address
        self.commands = {'ON': '1', 'OFF': '2', 'REBOOT': '3'}
        self.outlets = list(range(1, outlets + 1))
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
        try:
            output = subprocess.check_output(command).decode('ascii').split('\n')
        except subprocess.CalledProcessError as e:
            # TODO https://stackoverflow.com/questions/29824461
            raise

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


class APCUPS:
    """APC Uninterruptible Power Supply class, communicating through SNMP."""

    def __init__(self, address, outlets=3):
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
        self.outlets = list(range(1, outlets + 1))
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


class APCUPS_USB:
    """APC Uninterruptible Power Supply class, communicating through USB via `apcupsd`."""

    def __init__(self, address='localhost', port=3551):
        self.unit_type = 'UPS'
        self.address = address
        self.port = port
        self.buffer_size = 1024

    def _get_status(self):
        """Get the UPS status through the apcupsd daemon."""
        # connect
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.connect((self.address, self.port))

        status_command = '\x00\x06status'
        self.socket.send(status_command.encode())
        raw_out = ""
        while not raw_out.endswith('  \n\x00\x00'):
            out = self.socket.recv(self.buffer_size).decode()
            raw_out += out

        # close
        self.socket.shutdown(socket.SHUT_RDWR)
        self.socket.close()

        return self._parse_status(raw_out)

    def _parse_status(self, raw_data):
        data_list = [x[1:].split(':') for x in raw_data.split('\x00') if len(x) > 2]
        data_dict = {x[0].strip().lower(): x[1].strip() for x in data_list}
        return data_dict

    def status(self):
        """Return the current status of the UPS."""
        data_dict = self._get_status()
        status = data_dict['status']
        if status == 'ONLINE':
            status = 'Normal'  # same as SNMP
        return status

    def percent_remaining(self):
        """Return the current power percentage remaining in the UPS."""
        data_dict = self._get_status()
        percent = float(data_dict['bcharge'].split()[0])
        return percent

    def time_remaining(self):
        """Return the current power time remaining in the UPS."""
        data_dict = self._get_status()
        time, unit = data_dict['timeleft'].split()
        if unit == 'Seconds':
            seconds = float(time)
        elif unit == 'Minutes':
            seconds = float(time) * 60
        return seconds

    def load(self):
        """Return the current load on the UPS."""
        data_dict = self._get_status()
        percent = float(data_dict['loadpct'].split()[0])
        return percent

    def outlet_status(self):
        raise NotImplementedError('Cannot control UPS outlets through USB connection')

    def on(self, outlet):
        raise NotImplementedError('Cannot control UPS outlets through USB connection')

    def off(self, outlet):
        raise NotImplementedError('Cannot control UPS outlets through USB connection')

    def reboot(self, outlet):
        raise NotImplementedError('Cannot control UPS outlets through USB connection')


class APCATS:
    """APC Automatic Transfer Switch class, communicating through SNMP."""

    def __init__(self, address):
        self.unit_type = 'ATS'
        self.address = address
        self.command_oids = {'STATUS': '5.1.3.0',
                             'STATUS_A': '5.1.12.0',
                             'STATUS_B': '5.1.13.0',
                             'SOURCE': '5.1.2.0',
                             }
        self.statuses = {'1': 'ERROR',
                         '2': 'Normal',
                         }
        self.sources = {'1': 'A',
                        '2': 'B',
                        }

    def _initialise_oid_array(self, command_oid):
        """Set up the oid array to use with snmpget and snmpset."""
        base = '.1.3.6.1.4.1.318.1.1.8'
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

    def status(self):
        """Return the current status of the ATS."""
        oid_arr = self._initialise_oid_array(self.command_oids['STATUS'])
        out = self._snmpget(oid_arr)
        status = self.statuses[out]
        return status

    def source_status(self, source):
        """Return the current status of the given source."""
        if source == 'A':
            oid_arr = self._initialise_oid_array(self.command_oids['STATUS_A'])
        elif source == 'B':
            oid_arr = self._initialise_oid_array(self.command_oids['STATUS_B'])
        else:
            raise ValueError('Invalid source')
        out = self._snmpget(oid_arr)
        status = self.statuses[out]
        return status

    def active_source(self):
        """Return which source is currently active."""
        oid_arr = self._initialise_oid_array(self.command_oids['SOURCE'])
        out = self._snmpget(oid_arr)
        source = self.sources[out]
        return source


class EPCPDU:
    """Expert Power Control Power Distribution Unit class, communicating through SNMP."""

    def __init__(self, address, outlets=8):
        self.unit_type = 'PDU'
        self.address = address
        self.commands = {'ON': '1', 'OFF': '0'}
        self.outlets = list(range(1, outlets + 1))
        self.on_value = 1
        self.off_value = 0

    def _initialise_oid_array(self, outlet):
        """Set up the oid array to use with snmpget and snmpset."""
        base = '.1.3.6.1.4.1.28507.29.1.3.1.2.1.3'
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
        self.off(outlet)
        time.sleep(1)
        self.on(outlet)


class ETHPDU:
    """Robot Electronics ethernet relay class, communicating through TCP/IP."""

    def __init__(self, address, port, outlets=20, normally_closed=False):
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
        self.outlets = list(range(1, outlets + 1))
        self.reboot_time = 5  # seconds
        self.buffer_size = 1024

        # Create one persistent socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.connect((self.address, self.port))

    def __del__(self):
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
        except OSError:
            pass

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
        status_strings = [str(bin(i))[2::].zfill(8)[::-1] for i in status_ints]
        status_string = ''.join(status_strings)[:len(self.outlets)]
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
