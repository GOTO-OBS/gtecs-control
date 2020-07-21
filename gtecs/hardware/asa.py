"""Classes to control ASA hardware."""

import math
import time

import pyudev

import serial


class H400(object):
    """ASA H400 gateway controller class, for focusers and mirror covers.

    Some of the methods and properties are named oddly, but that's because they're based on
    the USBFocuser class from FLI-API (see also `gtecs.hardware.rasa.FocusLynx`).
    """

    # Serial command codes and addresses
    _ADDRESS_CODE = {'focuser': 107,
                     'cover': 1,
                     'gateway': 254,
                     }
    _FOCUSER_COMMAND_CODE = {'goto': 205,
                             'move': 206,
                             'stop': 204,
                             }
    _COVER_COMMAND_CODE = {'open': 304,
                           'close': 305,
                           'stop': 204,
                           }
    _STATUS_COMMAND_CODE = {'focuser': 1001,
                            'cover': 1002,
                            'gateway': 1010,
                            }

    # Reply codes
    _FOCUSER_POSITION_STATUS_CODE = {0: 'invalid',
                                     1: 'ok',
                                     2: 'max',
                                     3: 'min',
                                     7: 'ERROR',
                                     }
    _FOCUSER_CONTROL_STATUS_CODE = {0: 'invalid',
                                    1: 'moving',
                                    2: 'timeout',
                                    3: 'at_pos',
                                    }
    _FOCUSER_MOTOR_STATUS_CODE = {0: 'invalid',
                                  1: 'moving',
                                  2: 'stop_hard',
                                  3: 'stop_hiz',
                                  4: 'stop_soft',
                                  5: 'active',
                                  6: 'single',
                                  }
    _COVER_STATUS_CODE = {0: 'intermediate',  # TODO: no way to tell if moving?
                          1: 'open',
                          2: 'closed',
                          3: 'ERROR',
                          }
    _GATEWAY_STATUS_CODE = {0: 'off',
                            1: 'on',
                            3: 'pwm_control',
                            4: 'ERROR',
                            }

    def __init__(self, port):
        self._stored_info = None
        self._info_delay = 1

        # Serial connection to the gateway
        self.port = port
        self.serial_baudrate = 38400
        self.serial_timeout = 5
        self.serial = serial.Serial(self.port,
                                    baudrate=self.serial_baudrate,
                                    timeout=self.serial_timeout)

        # Get initial info, to fill properties like serial number
        info_dict = self._get_info()

        # We need to save the target position internally, but on init that's not defined.
        # So take the current position as the target instead.
        self.target_position = info_dict['focuser']['position']

        # Use udev to find device properties directly from the dev file
        # We need to do this to construct a unique serial number
        # TODO: Need to check the output of all of these
        context = pyudev.Context()
        udev = pyudev.Devices.from_device_file(context, port).parent.parent  # TODO: needs checking
        self.sys_name = str(udev.sys_name)
        self.usb = (str(udev['BUSNUM']), str(udev['DEVNUM']))
        self.type = str(udev['ID_MODEL_FROM_DATABASE'])
        self.typeID = str(udev['ID_MODEL_ID'])

    def __del__(self):
        try:
            self.serial.close()
        except AttributeError:
            pass

    def _serial_command(self, device, command, value=0):
        """Send command to the device, then fetch the reply and return it."""
        if command == 'status':
            device_address = self._ADDRESS_CODE['gateway']
            command_code = self._STATUS_COMMAND_CODE[device]
        else:
            device_address = self._ADDRESS_CODE[device]
            if device == 'focuser':
                command_code = self._FOCUSER_COMMAND_CODE[command]
            elif device == 'cover':
                command_code = self._COVER_COMMAND_CODE[command]
            else:
                raise ValueError('Unknown device: {}'.format(device))
        command_str = '#{:d} {:d} {:d}$'.format(command_code, device_address, value)

        self.serial.flushInput()
        self.serial.flushOutput()
        self.serial.write(command_str.encode('ascii'))

        time.sleep(0.1)
        if self.serial.in_waiting:
            out_bytes = self.serial.read(self.serial.in_waiting)
            reply = out_bytes.decode('ascii').strip()
            reply_list = reply.split(' ')
            if len(reply_list) == 1:
                return reply_list[0]
            else:
                return reply_list
        else:
            raise ConnectionError('No reply from serial connection')

    def _get_info(self):
        """Get the focuser status infomation."""
        # Limit how often we update
        if self._stored_info is None or time.time() - self._stored_info['ts'] > self._info_delay:
            info_dict = {}

            # Get focuser status
            foc_info = {}
            reply = self._serial_command('focuser', 'status')
            foc_info['position'] = int(reply[1])  # position in "LSB" steps (1 LSB=0.156um)
            foc_info['limit'] = math.floor(int(reply[3]) / 10 / 0.156)  # limit is in 0.01mm units
            foc_info['position_status'] = self._FOCUSER_POSITION_STATUS_CODE[int(reply(2))]
            foc_info['control_status'] = self._FOCUSER_CONTROL_STATUS_CODE[int(reply(4))]
            foc_info['motor_status'] = self._FOCUSER_MOTOR_STATUS_CODE[int(reply(5))]
            info_dict['focuser'] = foc_info

            # Get cover status
            # TODO: What exactly do the three different values mean here?
            cov_info = {}
            reply = self._serial_command('cover', 'status')
            cov_info['position'] = {1: int(reply[1]),
                                    2: int(reply[2]),
                                    3: int(reply[3]),
                                    }
            cov_info['status'] = {1: self._COVER_STATUS_CODE[int(reply[4])],
                                  2: self._COVER_STATUS_CODE[int(reply[5])],
                                  3: self._COVER_STATUS_CODE[int(reply[6])],
                                  }
            info_dict['cover'] = cov_info

            # Get gateway status
            gw_info = {}
            reply = self._serial_command('gateway', 'status')
            gw_info['swv'] = reply(1)
            gw_info['status'] = self._GATEWAY_STATUS_CODE[int(reply(2))]
            info_dict['gateway'] = gw_info

            # Store properties that shouldn't change
            self.max_extent = int(info_dict['focuser']['limit'])

            # Save timestamp and store
            info_dict['ts'] = time.time()
            self._stored_info = info_dict
        return self._stored_info

    @property
    def connected(self):
        """Check if this device is still available."""
        try:
            self._get_info()
            return True
        except ConnectionError:
            return False

    @property
    def serial_number(self):
        """Return a constructed serial number.

        Since the ASA devices don't have serial numbers (they don't really need them to be fair,
        as long as you know the port) we construct a unique string from the model name and
        USB bus it is connected to.
        """
        return 'H400:' + self.sys_name

    @property
    def stepper_position(self):
        """Get the number of steps remaining."""
        # TODO Add more properties
        info_dict = self._get_info()
        return info_dict['focuser']['position']

    def get_steps_remaining(self):
        """Get the number of steps remaining."""
        return self.target_position - self.stepper_position

    def step_motor(self, steps, blocking=False):
        """Step motor a given number of steps.

        If blocking is True this function returns when the move is complete.
        If not this function returns immediately, use 'get_steps_remaining'
        to see when move is complete.
        """
        target_position = int(self.stepper_position + steps)
        if target_position > self.max_extent:
            raise ValueError('Target position ({}) past limit ({})'.format(
                             target_position, self.max_extent))
        elif target_position < 0:
            raise ValueError('Target position ({}) is negative'.format(target_position))

        # Store the target position, so we can tell when it's reached with get_steps_remaining()
        # (because these focusers don't seem to store it themselves)
        self.target_position = target_position

        reply = self._serial_command('focuser', 'goto', int(target_position))
        if int(reply[0]) != 5000:
            raise Exception('Command error: {}'.format(reply))

        if blocking:
            while True:
                if self.get_steps_remaining() == 0:
                    break
                time.sleep(0.5)

    def home_focuser(self, blocking=False):
        """Move the focuser to the home position.

        The ASA focusers don't actually have a home command, this just moves to
        the centre of the range.
        """
        info_dict = self._get_info()
        current_position = int(info_dict['focuser']['position'])
        halfway_position = int(self.max_extent / 2)
        steps = halfway_position - current_position
        self.step_motor(steps, blocking=blocking)

    def stop_focuser(self):
        """Stop the focuser from moving."""
        reply = self._serial_command('focuser', 'stop')
        if int(reply[0]) != 5000:
            raise Exception('Command error: {}'.format(reply))

    def _move_cover(self, command, blocking=False):
        """Open or close the mirror cover."""
        if command not in ['open', 'close']:
            raise ValueError(f"{command} is not a valid command (should be 'open' or 'close')")

        reply = self._serial_command('cover', command)
        if int(reply[0]) != 5000:
            raise Exception('Command error: {}'.format(reply))

        if blocking:
            while True:
                info_dict = self._get_info()
                # TODO: we should have a single status for the cover, not three
                # TODO: also we don't know when the cover is moving, depending on what the
                #       0="intermediate" status actually means
                positions = list(info_dict['cover']['position'].values())
                if command == 'open' and all(p == 'open' for p in positions):
                    break
                if command == 'close' and all(p == 'closed' for p in positions):
                    break
                time.sleep(0.5)

    def open_cover(self, blocking=False):
        """Open the mirror cover."""
        self._move_cover('open', blocking=blocking)

    def close_cover(self, blocking=False):
        """Close the mirror cover."""
        self._move_cover('close', blocking=blocking)

    def stop_cover(self):
        """Stop the mirror cover from moving."""
        reply = self._serial_command('cover', 'stop')
        if int(reply[0]) != 5000:
            raise Exception('Command error: {}'.format(reply))
