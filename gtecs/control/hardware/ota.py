"""Classes to control ASA hardware."""

import threading
import time

import serial  # noqa: I900


class H400:
    """ASA H400 gateway controller class, for focusers and mirror covers.

    Some of the methods and properties are named oddly, but that's because they're based on
    the USBFocuser class from FLI-API (see also `gtecs.control.hardware.rasa.FocusLynx()`).

    Parameters
    ----------
    port : str
        Device location (e.g. '/dev/ttyUSB0')

    serial_number : str, optional
        serial string to associate with this telescope
        default is 'unknown'

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
    _COVER_STATUS_CODE = {0: 'part_open',
                          1: 'full_open',
                          2: 'closed',
                          3: 'ERROR',
                          }

    def __init__(self, port, serial_number='unknown', debug_file=None):
        self._stored_info = None
        self._info_delay = 1

        # Serial connection to the gateway
        self.port = port
        self.serial_baudrate = 38400
        self.serial_timeout = 5
        self.serial = serial.Serial(self.port,
                                    baudrate=self.serial_baudrate,
                                    timeout=self.serial_timeout)
        self._serial_lock = False

        # Set serial number
        # (ASAs don't actually have a way to get the serial number from the hub,
        #  so we just define it here)
        self.serial_number = serial_number

        # Store if the focuser has moved yet
        # It seems the auto adjusting doesn't happen until we've send a single move command
        self._initial_move = False

        # Get initial info
        self._get_info()

        # start debug thread
        if debug_file is not None:
            self.debug_file = debug_file
            self.debug_delay = 0.2
            t = threading.Thread(target=self._debug_thread)
            t.daemon = True
            t.start()

    def __del__(self):
        try:
            self.debug_thread_running = False
            self.serial.close()
        except AttributeError:
            pass

    @classmethod
    def locate_device(cls, port, serial_number='unknown', debug_file=None):
        """Locate the focuser by port."""
        try:
            return cls(port, serial_number, debug_file)
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

        reply = out_bytes.decode('ascii').strip()
        if not reply.startswith('#') or not reply.endswith('$'):
            raise ValueError('Invalid ASA reply string: "{}"'.format(reply))
        reply = reply[1:-1]  # strip leading # and trailing $
        reply_list = reply.split(' ')
        if len(reply_list) == 1:
            return reply_list[0]
        else:
            return reply_list

    def _get_info(self):
        """Get the focuser status information."""
        # Limit how often we update
        if self._stored_info is None or time.time() - self._stored_info['ts'] > self._info_delay:
            info_dict = {}

            # Get focuser status
            foc_info = {}
            reply = self._serial_command('focuser', 'status')
            # Position is in "LSB" steps (1 LSB=0.156μm), limit is in 0.01mm (10μm) units
            # To be consistent we convert both to μm, and treat them as "steps"
            foc_info['position'] = int(int(reply[1]) * 0.156)
            foc_info['limit'] = int(reply[3]) * 10
            foc_info['position_status'] = self._FOCUSER_POSITION_STATUS_CODE[int(reply[2])]
            foc_info['control_status'] = self._FOCUSER_CONTROL_STATUS_CODE[int(reply[4])]
            foc_info['motor_status'] = self._FOCUSER_MOTOR_STATUS_CODE[int(reply[5])]
            info_dict['focuser'] = foc_info

            # Get cover status
            # NB: The open position is ~2700, the closed position is ~0
            # Unfortunately there's no way to tell if the cover is moving or not
            # The 'part_open' status (0) is true if it's moving or if it's stopped
            # However it shouldn't matter, since new open/close commands can overwrite old ones
            cov_info = {}
            reply = self._serial_command('cover', 'status')
            cov_info['position'] = int(reply[1])  # in 10ths of a degree, I think
            cov_info['status'] = self._COVER_STATUS_CODE[int(reply[4])]
            info_dict['cover'] = cov_info

            # Store properties that shouldn't change
            self.max_extent = int(info_dict['focuser']['limit'])

            # Save timestamp and store
            info_dict['ts'] = time.time()
            self._stored_info = info_dict
        return self._stored_info

    def _debug_thread(self):
        import requests
        import json

        self.debug_thread_running = True
        self.count = 0

        def http_get(command_str):
            data = {}
            data['ClientID'] = 2323
            self.count += 1
            data['ClientTransactionID'] = self.count
            url = 'http://10.2.6.105:11111/api/v1/telescope/0/' + command_str
            r = requests.get(url, params=data)
            reply_str = r.content.decode(r.encoding)
            reply = json.loads(reply_str)
            return reply['Value']

        while self.debug_thread_running:
            try:
                line = f'{time.time():.7f};'
                foc_status = self._serial_command('focuser', 'status')
                line += ';'.join([foc_status[1], foc_status[2], foc_status[4], foc_status[5]])
                # ra = http_get('rightascension')
                # dec = http_get('declination')
                az = http_get('azimuth')
                alt = http_get('altitude')
                slewing = http_get('slewing')
                tracking = http_get('tracking')
                # line += f';{ra:.4f};{dec:.4f};{az:.4f};{alt:.4f};{slewing};{tracking}'
                line += f';{az:.4f};{alt:.4f};{slewing};{tracking}'
                with open(self.debug_file, 'a') as f:
                    f.write(line + '\n')
            except Exception:
                pass
            time.sleep(self.debug_delay)

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
        return info_dict['focuser']['position']

    def get_status(self):
        """Get the focuser status."""
        info_dict = self._get_info()
        foc_info = info_dict['focuser']
        if foc_info['control_status'] == 'invalid' or not self._initial_move:
            return 'UNSET'
        elif foc_info['control_status'] == 'moving':
            return 'Moving'
        elif foc_info['control_status'] == 'at_pos':
            return 'Ready'
        else:
            return 'ERROR'

    def move_focuser(self, steps, blocking=False):
        """Move the focuser a given number of steps.

        If blocking is True this function returns when the move is complete.
        If not this function returns immediately, use 'get_status()' to see when move is complete.
        """
        target_position = int(self.stepper_position + steps)
        if target_position > self.max_extent:
            raise ValueError('Target position ({}) past limit ({})'.format(
                             target_position, self.max_extent))
        elif target_position < 0:
            raise ValueError('Target position ({}) is negative'.format(target_position))

        # Convert from μm to LSB (see _get_info)
        steps_lsb = steps / 0.156
        reply = self._serial_command('focuser', 'move', int(steps_lsb))
        if int(reply[0]) != 5000:
            raise Exception('Command error: {}'.format(reply))
        self._initial_move = True

        if blocking:
            while True:
                time.sleep(0.5)
                if self.get_status() != 'Moving':
                    break

    def set_focuser(self, target_position, blocking=False):
        """Move the focuser to the given position.

        If blocking is True this function returns when the move is complete.
        If not this function returns immediately, use 'get_status()' to see when move is complete.
        """
        if target_position > self.max_extent:
            raise ValueError('Target position ({}) past limit ({})'.format(
                             target_position, self.max_extent))
        elif target_position < 0:
            raise ValueError('Target position ({}) is negative'.format(target_position))

        # Convert from μm to LSB (see _get_info)
        position_lsb = target_position / 0.156
        reply = self._serial_command('focuser', 'goto', int(position_lsb))
        if int(reply[0]) != 5000:
            raise Exception('Command error: {}'.format(reply))
        self._initial_move = True

        if blocking:
            while True:
                time.sleep(0.5)
                if self.get_status() != 'Moving':
                    break

    def home_focuser(self, blocking=False):
        """Move the focuser to the home position.

        The ASA focusers don't actually have a home command, this just moves to
        the centre of the range.
        """
        halfway_position = int(self.max_extent / 2)
        self.set_focuser(halfway_position, blocking=blocking)

    def stop_focuser(self):
        """Stop the focuser from moving."""
        reply = self._serial_command('focuser', 'stop')
        if int(reply[0]) != 5000:
            raise Exception('Command error: {}'.format(reply))

    def get_cover_position(self):
        """Get the current position of the mirror cover."""
        # NB: We can't tell if the cover is moving
        info_dict = self._get_info()
        return info_dict['cover']['status']

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


class FakeH400:
    """Fake ASA H400 gateway controller class, for testing.

    Parameters
    ----------
    port : str
        Device location (e.g. '/dev/ttyUSB0')

    serial_number : str, optional
        serial string to associate with this telescope
        default is 'unknown'

    """

    def __init__(self, port, serial_number='unknown'):
        self.fake = True

        self.port = port
        self.connected = True
        self.serial_number = serial_number

        self.stepper_position = 0
        self._target_position = 0
        self._focuser_move_speed = 100  # steps/sec
        self._focuser_moving = False
        self.max_extent = 5000
        self._initial_move = False

        self._cover_position = 0  # 0=closed, 1-4=part_open, 5=full_open
        self._cover_target_position = 0
        self._cover_move_speed = 1  # steps/sec
        self._cover_moving = False

    def get_status(self):
        """Get the focuser status."""
        if not self._initial_move:
            return 'UNSET'
        elif self._focuser_moving:
            return 'Moving'
        else:
            return 'Ready'

    def _move_fake_focuser(self):
        """Fake thread to simulate moving the focuser stepper motor."""
        self._initial_move = True
        self._focuser_moving = True

        while self._focuser_moving:
            time.sleep(0.1)
            step = int(self._focuser_move_speed * 0.1)
            if abs(self.stepper_position - self._target_position) < step:
                self.stepper_position = self._target_position
                break
            elif self.stepper_position < self._target_position:
                new_position = self.stepper_position + step
                if new_position > self.max_extent:
                    self.stepper_position = self.max_extent
                    break
                else:
                    self.stepper_position = new_position
            else:
                new_position = self.stepper_position - step
                if new_position < 0:
                    self.stepper_position = 0
                    break
                else:
                    self.stepper_position = new_position

        # stopped early?
        if self._focuser_moving:
            self._focuser_moving = False
        else:
            self._target_position = self.stepper_position

    def move_focuser(self, steps, blocking=False):
        """Move the focuser a given number of steps.

        If blocking is True this function returns when the move is complete.
        If not this function returns immediately.
        """
        target_position = int(self.stepper_position + steps)
        if target_position > self.max_extent:
            raise ValueError('Target position ({}) past limit ({})'.format(
                             target_position, self.max_extent))
        elif target_position < 0:
            raise ValueError('Target position ({}) is negative'.format(target_position))

        self._target_position = target_position

        if blocking:
            self._move_fake_focuser()
        else:
            ot = threading.Thread(target=self._move_fake_focuser)
            ot.daemon = True
            ot.start()

    def set_focuser(self, target_position, blocking=False):
        """Move the focuser to the given position.

        If blocking is True this function returns when the move is complete.
        If not this function returns immediately.
        """
        if target_position > self.max_extent:
            raise ValueError('Target position ({}) past limit ({})'.format(
                             target_position, self.max_extent))
        elif target_position < 0:
            raise ValueError('Target position ({}) is negative'.format(target_position))

        self._target_position = target_position

        if blocking:
            self._move_fake_focuser()
        else:
            ot = threading.Thread(target=self._move_fake_focuser)
            ot.daemon = True
            ot.start()

    def home_focuser(self, blocking=False):
        """Move the focuser to the home position."""
        halfway_position = int(self.max_extent / 2)
        self.set_focuser(halfway_position, blocking=blocking)

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
