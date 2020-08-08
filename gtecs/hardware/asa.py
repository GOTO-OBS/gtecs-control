"""Classes to control ASA hardware."""

import math
import threading
import time

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

    def __init__(self, port, serial_number):
        self._stored_info = None
        self._info_delay = 1

        # Serial connection to the gateway
        self.port = port
        self.serial_baudrate = 38400
        self.serial_timeout = 5
        self.serial = serial.Serial(self.port,
                                    baudrate=self.serial_baudrate,
                                    timeout=self.serial_timeout)

        # Set serial number
        # (ASAs don't actually have a way to get the serial number from the hub,
        #  so we just define it here)
        self.serial_number = serial_number

        # Get initial info
        info_dict = self._get_info()

        # We need to save the target position internally, but on init that's not defined.
        # So take the current position as the target instead.
        self.target_position = info_dict['focuser']['position']

    def __del__(self):
        try:
            self.serial.close()
        except AttributeError:
            pass

    @classmethod
    def locate_device(cls, port, serial_number):
        """Locate the focuser by port."""
        try:
            return cls(port, serial_number)
        except serial.serialutil.SerialException:
            return None

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
            if not reply.startswith('#') or not reply.endswith('$'):
                raise ValueError('Invalid ASA reply string: "{}"'.format(reply))
            reply = reply[1:-1]  # strip leading # and trailing $
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
    def stepper_position(self):
        """Get the number of steps remaining."""
        # TODO Add more properties
        info_dict = self._get_info()
        return info_dict['focuser']['position']

    def get_steps_remaining(self):
        """Get the number of steps remaining."""
        return abs(self.target_position - self.stepper_position)

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

    def get_cover_position(self):
        """Get the current position of the mirror cover."""
        # TODO: This needs checking, in paticular what the three statuses actually mean.
        # TODO: Also we don't know when the cover is moving, depending on what the
        #       0="intermediate" status actually means
        # TODO: For that matter can we interupt commands, i.e. close while opening?
        info_dict = self._get_info()
        positions = list(info_dict['cover']['position'].values())
        if any(p == 'ERROR' for p in positions):
            return 'ERROR'
        elif all(p == 'closed' for p in positions):
            return 'closed'
        elif all(p == 'open' for p in positions):
            return 'full_open'
        else:
            return 'part_open'  # Best I can say for now

    def _move_cover(self, command, blocking=False):
        """Open or close the mirror cover."""
        if command not in ['open', 'close']:
            raise ValueError(f"{command} is not a valid command (should be 'open' or 'close')")

        reply = self._serial_command('cover', command)
        if int(reply[0]) != 5000:
            raise Exception('Command error: {}'.format(reply))

        if blocking:
            while True:
                if command == 'open' and self.get_cover_position() == 'open':
                    break
                if command == 'close' and self.get_cover_position() == 'closed':
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


class FakeH400(object):
    """Fake ASA H400 gateway controller class, for testing."""

    def __init__(self, port, serial_number):
        self.fake = True

        self.port = port
        self.connected = True
        self.serial_number = serial_number

        self.stepper_position = 0
        self.target_position = 0
        self._focuser_move_speed = 100  # steps/sec
        self._focuser_moving = False
        self.max_extent = 5000

        self._cover_position = 0  # 0=closed, 1-4=part_open, 5=full_open
        self._cover_target_position = 0
        self._cover_move_speed = 1  # steps/sec
        self._cover_moving = False

    def get_steps_remaining(self):
        """Get the number of steps remaining."""
        return abs(self.target_position - self.stepper_position)

    def _move_fake_focuser(self):
        """Fake thread to simulate moving the focuser stepper motor."""
        self._focuser_moving = True
        while (self._focuser_moving and
               self.get_steps_remaining() > int(self._focuser_move_speed * 0.1)):
            time.sleep(0.1)
            if self.stepper_position < self.target_position:
                self.stepper_position += int(self._focuser_move_speed * 0.1)
            else:
                self.stepper_position -= int(self._focuser_move_speed * 0.1)
        if self._focuser_moving:
            self._focuser_moving = False
            self.stepper_position = self.target_position
        else:
            # Stopped early
            self.target_position = self.stepper_position

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

        self.target_position = target_position

        if blocking:
            self._move_fake_focuser()
        else:
            ot = threading.Thread(target=self._move_fake_focuser)
            ot.daemon = True
            ot.start()

    def home_focuser(self, blocking=False):
        """Move the focuser to the home position."""
        steps = int(self.max_extent / 2) - self.stepper_position
        self.step_motor(steps, blocking=blocking)

    def stop_focuser(self):
        """Stop the focuser from moving."""
        self._focuser_moving = False

    def get_cover_position(self):
        """Get the current position of the mirror cover."""
        if self._cover_position == 0:
            return 'closed'
        elif self._cover_position == 5:
            return 'full_open'
        else:
            return 'part_open'

    def _get_cover_steps_remaining(self):
        """Get the number of steps remaining."""
        return abs(self._cover_target_position - self._cover_position)

    def _move_fake_cover(self):
        """Fake thread to simulate moving the mirror cover."""
        self._cover_moving = True
        while (self._cover_moving and
               self._get_cover_steps_remaining() > self._cover_move_speed * 0.1):
            time.sleep(0.1)
            if self._cover_position < self._cover_target_position:
                self._cover_position += self._cover_move_speed * 0.1
            else:
                self._cover_position -= self._cover_move_speed * 0.1
        if self._cover_moving:
            self._cover_moving = False
            self._cover_position = self._cover_target_position
        else:
            # Stopped early
            self._cover_target_position = self._cover_position

    def _move_cover(self, command, blocking=False):
        """Open or close the mirror cover."""
        if command not in ['open', 'close']:
            raise ValueError(f"{command} is not a valid command (should be 'open' or 'close')")

        if command == 'open':
            self._cover_target_position = 5
        else:
            self._cover_target_position = 0

        if blocking:
            self._move_fake_cover()
        else:
            ot = threading.Thread(target=self._move_fake_cover)
            ot.daemon = True
            ot.start()

    def open_cover(self, blocking=False):
        """Open the mirror cover."""
        self._move_cover('open', blocking=blocking)

    def close_cover(self, blocking=False):
        """Close the mirror cover."""
        self._cover_target_position = 0
        self._move_cover('close', blocking=blocking)

    def stop_cover(self):
        """Stop the mirror cover from moving."""
        self._cover_moving = False
