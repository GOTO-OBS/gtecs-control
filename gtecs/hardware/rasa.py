"""Classes to control RASAs."""

import time

import serial


class FocusLynx(object):
    """FocusLynx focuser controller class.

    Some of the methods and properties are named oddly, but that's because they're based on
    the USBFocuser class from FLI-API.
    """

    def __init__(self, port, number=1):
        self.number = int(number)

        self.port = port
        self.serial_baudrate = 115200
        self.serial_timeout = 5

        # serial connection to the focuser
        self.serial = serial.Serial(self.port,
                                    baudrate=self.serial_baudrate,
                                    timeout=self.serial_timeout)

        # get initial info, fill properties like serial number
        self._get_info()

    def __del__(self):
        try:
            self.serial.close()
        except AttributeError:
            pass

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
        self.serial.flushInput()
        self.serial.flushOutput()
        command = '<F{}{}>'.format(self.number, command_str)
        self.serial.write(command.encode('ascii'))
        time.sleep(0.1)
        if self.serial.in_waiting:
            out_bytes = self.serial.read(self.serial.in_waiting)
            reply = out_bytes.decode('ascii').strip()
            reply_list = reply.split('\n')[1:]
            if len(reply_list) == 1:
                return reply_list[0]
            else:
                return reply_list
        else:
            raise ConnectionError('No reply from serial connection')

    def _get_info(self):
        """Get the focuser status infomation."""
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

        return info_dict

    @property
    def connected(self):
        """Check if this device is still available."""
        try:
            self._get_info()
            return True
        except ConnectionError:
            return False

    @property
    def stepper_position(self):
        """Get the number of steps remaining."""
        info_dict = self._get_info()
        return int(info_dict['Curr Pos'])

    def get_steps_remaining(self):
        """Get the number of steps remaining."""
        info_dict = self._get_info()
        return abs(int(info_dict['Targ Pos']) - int(info_dict['Curr Pos']))

    def step_motor(self, steps, blocking=False):
        """Step motor a given number of steps.

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

    def read_temperature(self, temp_type='internal'):
        """Get the focuser temperature, in Celcius."""
        info_dict = self._get_info()
        return float(info_dict['Temp(C)'])
