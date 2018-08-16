#!/usr/bin/env python
"""Daemon to control FLI focusers via fli_interface."""

import datetime
import threading
import time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.daemons import HardwareDaemon, daemon_proxy


class FocDaemon(HardwareDaemon):
    """Focuser hardware daemon class."""

    def __init__(self):
        super().__init__('foc')

        # foc is dependent on all the FLI interfaces
        for daemon_id in params.FLI_INTERFACES:
            self.dependencies.add(daemon_id)

        # command flags
        self.get_info_flag = 1
        self.set_focuser_flag = 0
        self.move_focuser_flag = 0
        self.home_focuser_flag = 0

        # focuser variables
        self.limit = {}
        self.current_pos = {}
        self.remaining = {}
        self.int_temp = {}
        self.ext_temp = {}
        self.move_steps = {}
        self.serial_number = {}

        for intf in params.FLI_INTERFACES:
            nhw = len(params.FLI_INTERFACES[intf]['TELS'])
            self.limit[intf] = [0] * nhw
            self.current_pos[intf] = [0] * nhw
            self.remaining[intf] = [0] * nhw
            self.int_temp[intf] = [0] * nhw
            self.ext_temp[intf] = [0] * nhw
            self.move_steps[intf] = [0] * nhw
            self.serial_number[intf] = [0] * nhw

        self.active_tel = []

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        self.log.info('Daemon control thread started')

        while(self.running):
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time

                # Check the dependencies
                self._check_dependencies()

                # If there is an error then keep looping.
                if self.dependency_error:
                    time.sleep(1)
                    continue

            # control functions
            # request info
            if self.get_info_flag:
                try:
                    # update variables
                    for tel in params.TEL_DICT:
                        intf, hw = params.TEL_DICT[tel]
                        try:
                            with daemon_proxy(intf) as fli:
                                self.limit[intf][hw] = fli.get_focuser_limit(hw)
                                self.remaining[intf][hw] = fli.get_focuser_steps_remaining(hw)
                                self.current_pos[intf][hw] = fli.get_focuser_position(hw)
                                self.int_temp[intf][hw] = fli.get_focuser_temp('internal', hw)
                                self.ext_temp[intf][hw] = fli.get_focuser_temp('external', hw)
                                self.serial_number[intf][hw] = fli.get_focuser_serial_number(hw)
                        except Exception:
                            self.log.error('No response from fli interface on %s', intf)
                            self.log.debug('', exc_info=True)
                    # save info
                    info = {}
                    for tel in params.TEL_DICT:
                        intf, hw = params.TEL_DICT[tel]
                        tel = str(params.FLI_INTERFACES[intf]['TELS'][hw])
                        if self.remaining[intf][hw] > 0:
                            info['status' + tel] = 'Moving'
                            if self.move_steps[intf][hw] == 0:
                                # Homing, needed due to bug in remaining
                                info['remaining' + tel] = self.current_pos[intf][hw]
                            else:
                                info['remaining' + tel] = self.remaining[intf][hw]
                        else:
                            info['status' + tel] = 'Ready'
                        info['current_pos' + tel] = self.current_pos[intf][hw]
                        info['limit' + tel] = self.limit[intf][hw]
                        info['int_temp' + tel] = self.int_temp[intf][hw]
                        info['ext_temp' + tel] = self.ext_temp[intf][hw]
                        info['serial_number' + tel] = self.serial_number[intf][hw]

                    info['uptime'] = time.time() - self.start_time
                    info['ping'] = time.time() - self.loop_time
                    now = datetime.datetime.utcnow()
                    info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

                    self.info = info
                except Exception:
                    self.log.error('get_info command failed')
                    self.log.debug('', exc_info=True)
                self.get_info_flag = 0

            # move the focuser
            if self.move_focuser_flag:
                try:
                    for tel in self.active_tel:
                        intf, hw = params.TEL_DICT[tel]
                        move_steps = self.move_steps[intf][hw]
                        new_pos = self.current_pos[intf][hw] + move_steps

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
                        self.move_steps[intf][hw] = 0  # to mark that it's homing
                except Exception:
                    self.log.error('home_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_tel = []
                self.home_focuser_flag = 0

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Focuser control functions
    def get_info(self):
        """Return focuser status info."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Set flag
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.1)
        return self.info

    def get_info_simple(self):
        """Return plain status dict, or None."""
        try:
            info = self.get_info()
        except Exception:
            return None
        return info

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
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            if self.remaining[intf][hw] == 0 and new_pos <= self.limit[intf][hw]:
                self.active_tel += [tel]
                self.move_steps[intf][hw] = new_pos - self.current_pos[intf][hw]

        # Set flag
        self.move_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            s += '\n  '
            if self.remaining[intf][hw] > 0:
                s += misc.errortxt('"HardwareStatusError: Focuser %i motor is still moving"' % tel)
            elif new_pos > self.limit[intf][hw]:
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
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            new_pos = self.current_pos[intf][hw] + move_steps
            if self.remaining[intf][hw] == 0 and new_pos <= self.limit[intf][hw]:
                self.active_tel += [tel]
                self.move_steps[intf][hw] = move_steps

        # Set flag
        self.move_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            new_pos = self.current_pos[intf][hw] + move_steps
            s += '\n  '
            if self.remaining[intf][hw] > 0:
                s += misc.errortxt('"HardwareStatusError: Focuser %i motor is still moving"' % tel)
            elif new_pos > self.limit[intf][hw]:
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
        self.get_info_flag = 1
        time.sleep(0.1)
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            if self.remaining[intf][hw] == 0:
                self.active_tel += [tel]

        # Set flag
        self.home_focuser_flag = 1

        # Format return string
        s = 'Moving:'
        for tel in tel_list:
            intf, hw = params.TEL_DICT[tel]
            s += '\n  '
            if self.remaining[intf][hw] > 0:
                s += misc.errortxt('"HardwareStatusError: Focuser %i motor is still moving"' % tel)
            else:
                s += 'Homing focuser %i' % tel
        return s


if __name__ == "__main__":
    daemon_id = 'foc'
    with misc.make_pid_file(daemon_id):
        FocDaemon()._run()
