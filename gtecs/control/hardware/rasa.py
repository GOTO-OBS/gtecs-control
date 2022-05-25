"""Classes to control RASAs."""

import os
import time

import serial  # noqa: I900


class FocusLynx(object):
    """FocusLynx focuser controller class.

    Some of the methods and properties are named oddly, but that's because they're based on
    the USBFocuser class from FLI-API.
    """

    def __init__(self, port, number=1):
        self.number = int(number)

        self._stored_info = None
        self._info_delay = 1

        # serial connection to the focuser
        self.port = port
        self.serial_baudrate = 115200
        self.serial_timeout = 5
        self.serial = serial.Serial(self.port,
                                    baudrate=self.serial_baudrate,
                                    timeout=self.serial_timeout)
        self.__lockfile = os.path.join('/tmp', os.path.split(self.port)[-1] + '.lock')
        self._serial_lock = False

        # get initial info, fill properties like serial number
        self._get_info()

    def __del__(self):
        try:
            self.serial.close()
        except AttributeError:
            pass

    @property
    def _serial_lock(self):
        return os.path.exists(self.__lockfile)

    @_serial_lock.setter
    def _serial_lock(self, value):
        if value:
            with open(self.__lockfile, 'w') as f:
                f.write(str(os.getpid()))
        else:
            if os.path.exists(self.__lockfile):
                os.remove(self.__lockfile)

    @classmethod
    def locate_device(cls, port, serial_number):
        """Locate the focuser by name."""
        channel_dict = {}
        for i in [1, 2]:
            focuser = cls(port, i)
            name = focuser.serial_number
            channel_dict[name] = focuser
        try:
            return channel_dict[serial_number]
        except KeyError:
            raise ValueError('Serial {} not recognised (not in {})'.format(
                             serial_number, sorted(channel_dict.keys())))

    def _serial_command(self, command_str):
        """Send command to the device, then fetch the reply and return it."""
        command_str = '<F{}{}>'.format(self.number, command_str)

        while self._serial_lock:
            time.sleep(0.1)
        self._serial_lock = True
        try:
            self.serial.flushInput()
            self.serial.flushOutput()
            self.serial.write(command_str.encode('ascii'))
            time.sleep(0.1)
            out_bytes = self.serial.read(self.serial.in_waiting)
        except Exception:
            raise ConnectionError('No reply from serial connection')
        finally:
            self._serial_lock = False

        if len(out_bytes) == 0:
            raise ConnectionError('No reply from serial connection')

        reply = out_bytes.decode('ascii').strip()
        reply_list = reply.split('\n')[1:]
        if len(reply_list) == 1:
            return reply_list[0]
        else:
            return reply_list

    def _get_info(self):
        """Get the focuser status infomation."""
        # Info queries take time, so we don't want to do it too often
        if self._stored_info is None or time.time() - self._stored_info['ts'] > self._info_delay:
            info_dict = {}
            # Use both info commands
            for command in ['GETSTATUS', 'GETCONFIG']:
                reply = self._serial_command(command)
                for s in reply[1:-1]:
                    key, value = s.split('=')
                    info_dict[key.strip()] = value.strip()

            # fill properties that shouldn't change
            self.max_extent = int(info_dict['Max Pos'])
            self.serial_number = info_dict['Nickname']

            # save timestamp and store
            info_dict['ts'] = time.time()
            self._stored_info = info_dict
        return self._stored_info

    @property
    def connected(self):
        """Check if this device is still available."""
        try:
            ret = self._serial_command('HELLO')
            if ret != self.serial_number:
                raise ConnectionError('Incorrect reply from serial connection')
            return True
        except ConnectionError:
            return False

    @property
    def stepper_position(self):
        """Get the number of steps remaining."""
        info_dict = self._get_info()
        return int(info_dict['Curr Pos'])

    def get_status(self):
        """Get the focuser status."""
        info_dict = self._get_info()
        if not bool(int(info_dict['IsHomed'])):
            return 'UNHOMED'
        elif bool(int(info_dict['IsMoving'])) or bool(int(info_dict['IsHoming'])):
            return 'Moving'
        else:
            return 'Ready'

    def get_steps_remaining(self):
        """Get the number of steps remaining."""
        info_dict = self._get_info()
        return abs(int(info_dict['Targ Pos']) - int(info_dict['Curr Pos']))

    def move_focuser(self, steps, blocking=False):
        """Move the focuser a given number of steps.

        If blocking is True this function returns when the move is complete.
        If not this function returns immediately, use 'get_steps_remaining'
        to see when move is complete.
        """
        info_dict = self._get_info()
        current_position = int(info_dict['Curr Pos'])
        new_position = int(current_position + steps)
        if new_position > self.max_extent:
            raise Exception('New position past limit ({})'.format(self.max_extent))

        command = 'MA' + '{:06d}'.format(new_position)
        reply = self._serial_command(command)
        if reply != 'M':
            raise Exception('Failed to move: {}'.format(reply))
        if blocking:
            while True:
                if self.get_steps_remaining() == 0:
                    break
                time.sleep(0.1)

    def home_focuser(self, blocking=False):
        """Move the focuser to the home position."""
        reply = self._serial_command('CENTER')
        if reply != 'H':
            raise Exception('Failed to move: {}'.format(reply))
        if blocking:
            while True:
                if self.get_steps_remaining() == 0:
                    break
                time.sleep(0.1)

    def stop_focuser(self):
        """Stop the focuser from moving."""
        reply = self._serial_command('HALT')
        if reply != 'HALTED':
            raise Exception('Failed to stop: {}'.format(reply))

    def sync_focuser(self, position):
        """Set the current motor position to the given value."""
        command = 'SCCP' + '{:06d}'.format(position)
        reply = self._serial_command(command)
        if reply != 'SET':
            raise Exception('Failed to sync: {}'.format(reply))

    def read_temperature(self, temp_type='internal'):
        """Get the focuser temperature, in Celcius."""
        info_dict = self._get_info()
        return float(info_dict['Temp(C)'])


class FocusLynxHub(object):
    """FocusLynx focuser hub controller class.

    Each hub can have two focusers attached, and commands to both are sent through the same port.

    Some of the methods and properties are named oddly, but that's because they're based on
    the USBFocuser class from FLI-API.
    """

    def __init__(self, port):
        self.dev_numbers = [1, 2]

        self._stored_info = None
        self._info_delay = 1

        # serial connection to the focuser
        self.port = port
        self.serial_baudrate = 115200
        self.serial_timeout = 5
        self.serial = serial.Serial(self.port,
                                    baudrate=self.serial_baudrate,
                                    timeout=self.serial_timeout)
        self._serial_lock = False

        # get initial info, fill properties like serial number
        self._get_info()

    def __del__(self):
        try:
            self.serial.close()
        except AttributeError:
            pass

    @classmethod
    def locate_device(cls, port):
        """Locate the focuser hub by port."""
        try:
            return cls(port)
        except serial.serialutil.SerialException:
            return None

    def _serial_command(self, dev_number, command_str):
        """Send command to the device, then fetch the reply and return it."""
        if dev_number not in self.dev_numbers and dev_number != 'H':
            raise ValueError('Invalid device number "{}"'.format(dev_number))

        command_str = '<F{}{}>'.format(dev_number, command_str)

        while self._serial_lock:
            time.sleep(0.1)
        self._serial_lock = True
        try:
            self.serial.flushInput()
            self.serial.flushOutput()
            self.serial.write(command_str.encode('ascii'))
            time.sleep(0.1)
            out_bytes = self.serial.read(self.serial.in_waiting)
        except Exception:
            raise ConnectionError('No reply from serial connection')
        finally:
            self._serial_lock = False

        if len(out_bytes) == 0:
            raise ConnectionError('No reply from serial connection')

        reply = out_bytes.decode('ascii').strip()
        reply_list = reply.split('\n')[1:]
        if len(reply_list) == 1:
            return reply_list[0]
        else:
            return reply_list

    def _get_info(self):
        """Get the focuser status infomation."""
        # Info queries take time, so we don't want to do it too often
        if self._stored_info is None or time.time() - self._stored_info['ts'] > self._info_delay:
            info_dict = {}
            max_extent_dict = {}
            serial_number_dict = {}
            for dev_number in self.dev_numbers:
                temp_dict = {}
                # Use both info commands
                for command in ['GETSTATUS', 'GETCONFIG']:
                    reply = self._serial_command(dev_number, command)
                    for s in reply[1:-1]:
                        key, value = s.split('=')
                        temp_dict[key.strip()] = value.strip()
                info_dict[dev_number] = temp_dict

                # fill properties that shouldn't change
                max_extent_dict[dev_number] = int(temp_dict['Max Pos'])
                serial_number_dict[dev_number] = temp_dict['Nickname']

            # save timestamp and store
            info_dict['ts'] = time.time()
            self._stored_info = info_dict
            self.max_extent_dict = max_extent_dict
            self.serial_number_dict = serial_number_dict
            self.serial_number = '+'.join(serial_number_dict.values())
        return self._stored_info

    @property
    def connected(self):
        """Check if this device is still available."""
        try:
            ret = self._serial_command('H', 'GETHUBINFO')
            if ret[0] != 'HUB INFO':
                raise ConnectionError('Incorrect reply from serial connection')
            return True
        except ConnectionError:
            return False

    def get_dev_number(self, serial):
        """Get the focuser device number."""
        for dev_number, serial_number in self.serial_number_dict.items():
            if serial_number == serial:
                return dev_number
        raise ValueError('Serial number "{}" not found'.format(serial))

    def get_serial_number(self, dev_number):
        """Get the focuser nickname."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        return self.serial_number_dict[dev_number]

    def get_stepper_position(self, dev_number):
        """Get the number of steps remaining."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        info_dict = self._get_info()[dev_number]
        return int(info_dict['Curr Pos'])

    def get_max_extent(self, dev_number):
        """Get the maximum extent of the focuser."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        return self.max_extent_dict[dev_number]

    def get_status(self, dev_number):
        """Get the focuser status."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        info_dict = self._get_info()[dev_number]
        if not bool(int(info_dict['IsHomed'])):
            return 'UNHOMED'
        elif bool(int(info_dict['IsMoving'])) or bool(int(info_dict['IsHoming'])):
            return 'Moving'
        else:
            return 'Ready'

    def get_steps_remaining(self, dev_number):
        """Get the number of steps remaining."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        info_dict = self._get_info()[dev_number]
        return abs(int(info_dict['Targ Pos']) - int(info_dict['Curr Pos']))

    def move_focuser(self, dev_number, steps, blocking=False):
        """Move the focuser a given number of steps.

        If blocking is True this function returns when the move is complete.
        If not this function returns immediately, use 'get_steps_remaining'
        to see when move is complete.
        """
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        info_dict = self._get_info()[dev_number]
        current_position = int(info_dict['Curr Pos'])
        new_position = int(current_position + steps)
        max_extent = self.get_max_extent(dev_number)
        if new_position > max_extent:
            raise Exception('New position past limit ({})'.format(max_extent))

        command = 'MA' + '{:06d}'.format(new_position)
        reply = self._serial_command(dev_number, command)
        if reply != 'M':
            raise Exception('Failed to move: {}'.format(reply))
        if blocking:
            while True:
                if self.get_steps_remaining(dev_number) == 0:
                    break
                time.sleep(0.1)

    def home_focuser(self, dev_number, blocking=False):
        """Move the focuser to the home position."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        reply = self._serial_command(dev_number, 'CENTER')
        if reply != 'H':
            raise Exception('Failed to move: {}'.format(reply))
        if blocking:
            while True:
                if self.get_steps_remaining(dev_number) == 0:
                    break
                time.sleep(0.1)

    def stop_focuser(self, dev_number):
        """Stop the focuser from moving."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        reply = self._serial_command(dev_number, 'HALT')
        if reply != 'HALTED':
            raise Exception('Failed to stop: {}'.format(reply))

    def sync_focuser(self, dev_number, position):
        """Set the current motor position to the given value."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        command = 'SCCP' + '{:06d}'.format(position)
        reply = self._serial_command(dev_number, command)
        if reply != 'SET':
            raise Exception('Failed to sync: {}'.format(reply))

    def read_temperature(self, dev_number):
        """Get the focuser temperature, in Celcius."""
        if dev_number not in self.dev_numbers:
            raise ValueError('Invalid device number "{}"'.format(dev_number))
        info_dict = self._get_info()[dev_number]
        return float(info_dict['Temp(C)'])
