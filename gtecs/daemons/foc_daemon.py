#!/usr/bin/env python
"""Daemon to control FLI focusers via fli_interface."""

import threading
import time

from astropy.time import Time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon, daemon_proxy


class FocDaemon(BaseDaemon):
    """Focuser hardware daemon class."""

    def __init__(self):
        super().__init__('foc')

        # foc is dependent on all the FLI interfaces
        for daemon_id in params.FLI_INTERFACES:
            self.dependencies.add(daemon_id)

        # command flags
        self.move_focuser_flag = 0
        self.home_focuser_flag = 0

        # focuser variables
        self.active_tel = []
        self.move_steps = {tel: 0 for tel in params.TEL_DICT}

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

        while(self.running):
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time
                self.force_check_flag = False

                # Check the dependencies
                self._check_dependencies()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's successful
                if self.dependency_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

            # control functions
            # move the focuser
            if self.move_focuser_flag:
                try:
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]
                        move_steps = self.move_steps[tel]
                        new_pos = self.info[tel]['current_pos'] + move_steps

                        self.log.info('Moving focuser %i (%s-%i) by %i to %i',
                                      tel, intf, hw, move_steps, new_pos)

                        try:
                            with daemon_proxy(intf) as fli:
                                c = fli.step_focuser_motor(move_steps, hw)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('move_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_tel = []
                self.move_focuser_flag = 0
                self.force_check_flag = True

            # home the focuser
            if self.home_focuser_flag:
                try:
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]

                        self.log.info('Homing focuser %i (%s-%i)',
                                      tel, intf, hw)

                        try:
                            with daemon_proxy(intf) as fli:
                                c = fli.home_focuser(hw)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)
                        fli._pyroRelease()
                        self.move_steps[tel] = 0  # to mark that it's homing
                except Exception:
                    self.log.error('home_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_tel = []
                self.home_focuser_flag = 0
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the heardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        for tel in params.TEL_DICT:
            # Get info from each interface
            try:
                intf, hw = params.TEL_DICT[tel]
                tel_info = {}
                tel_info['intf'] = intf
                tel_info['hw'] = hw

                with daemon_proxy(intf) as fli:
                    tel_info['remaining'] = fli.get_focuser_steps_remaining(hw)
                    tel_info['current_pos'] = fli.get_focuser_position(hw)
                    tel_info['limit'] = fli.get_focuser_limit(hw)
                    tel_info['int_temp'] = fli.get_focuser_temp('internal', hw)
                    tel_info['ext_temp'] = fli.get_focuser_temp('external', hw)
                    tel_info['serial_number'] = fli.get_focuser_serial_number(hw)

                if tel_info['remaining'] > 0:
                    tel_info['status'] = 'Moving'
                    if self.move_steps[tel] == 0:
                        # Homing, needed due to bug in remaining
                        tel_info['remaining'] = tel_info['current_pos']
                else:
                    tel_info['status'] = 'Ready'

                temp_info[tel] = tel_info
            except Exception:
                self.log.error('Failed to get filter wheel {} info'.format(tel))
                self.log.debug('', exc_info=True)
                temp_info[tel] = None

        # Update the master info dict
        self.info = temp_info

    # Control functions
    def set_focuser(self, new_pos, tel_list):
        """Move focuser to given position."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if int(new_pos) < 0 or (int(new_pos) - new_pos) != 0:
            raise ValueError('Position must be a positive integer')
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.wait_for_info()
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            if self.info[tel]['remaining'] == 0 and new_pos <= self.info[tel]['limit']:
                self.active_tel += [tel]
                self.move_steps[tel] = new_pos - self.info[tel]['current_pos']

        # Set flag
        self.move_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            s += '\n  '
            if self.info[tel]['remaining'] > 0:
                s += misc.errortxt('"HardwareStatusError: Focuser %i motor is still moving"' % tel)
            elif new_pos > self.info[tel]['limit']:
                s += misc.errortxt('"ValueError: Focuser %i position past limit"' % tel)
            else:
                s += 'Moving focuser %i' % tel
        return s

    def move_focuser(self, move_steps, tel_list):
        """Move focuser by given number of steps."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if (int(move_steps) - move_steps) != 0:
            raise ValueError('Steps must be an integer')
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.wait_for_info()
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            new_pos = self.info[tel]['current_pos'] + move_steps
            if self.info[tel]['remaining'] == 0 and new_pos <= self.info[tel]['limit']:
                self.active_tel += [tel]
                self.move_steps[tel] = move_steps

        # Set flag
        self.move_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            new_pos = self.info[tel]['current_pos'] + move_steps
            s += '\n  '
            if self.info[tel]['remaining'] > 0:
                s += misc.errortxt('"HardwareStatusError: Focuser %i motor is still moving"' % tel)
            elif new_pos > self.info[tel]['limit']:
                s += misc.errortxt('"ValueError: Position past limit"')
            else:
                s += 'Moving focuser %i' % tel
        return s

    def home_focuser(self, tel_list):
        """Move focuser to the home position."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))

        # Set values
        self.wait_for_info()
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            if self.info[tel]['remaining'] == 0:
                self.active_tel += [tel]

        # Set flag
        self.home_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            s += '\n  '
            if self.info[tel]['remaining'] > 0:
                s += misc.errortxt('"HardwareStatusError: Focuser %i motor is still moving"' % tel)
            else:
                s += 'Homing focuser %i' % tel
        return s


if __name__ == "__main__":
    daemon_id = 'foc'
    with misc.make_pid_file(daemon_id):
        FocDaemon()._run()
