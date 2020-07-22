#!/usr/bin/env python3
"""Daemon to control focusers  via the UT interface daemons."""

import threading
import time

from astropy.time import Time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.conditions import get_roomalert
from gtecs.daemons import BaseDaemon, daemon_proxy


class FocDaemon(BaseDaemon):
    """Focuser hardware daemon class."""

    def __init__(self):
        super().__init__('foc')

        # foc is dependent on all the interfaces
        for interface_id in params.INTERFACES:
            self.dependencies.add(interface_id)

        # command flags
        self.move_focuser_flag = 0
        self.home_focuser_flag = 0

        # focuser variables
        self.uts = params.UTS_WITH_FOCUSERS.copy()
        self.active_uts = []
        self.move_steps = {ut: 0 for ut in self.uts}
        self.last_move_temp = {ut: None for ut in self.uts}

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
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        move_steps = self.move_steps[ut]
                        new_pos = self.info[ut]['current_pos'] + move_steps

                        self.log.info('Moving focuser {} ({}) by {} to {}'.format(
                                      ut, interface_id, move_steps, new_pos))

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.step_focuser_motor(move_steps, ut)
                                if c:
                                    self.log.info(c)

                                # store the temperature at the time it moved
                                self.last_move_temp[ut] = self.info[ut]['current_temp']

                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('move_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.move_focuser_flag = 0
                self.force_check_flag = True

            # home the focuser
            if self.home_focuser_flag:
                try:
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']

                        self.log.info('Homing focuser {} ({})'.format(
                                      ut, interface_id))

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.home_focuser(ut)
                                if c:
                                    self.log.info(c)
                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                        interface._pyroRelease()
                        self.move_steps[ut] = 0  # to mark that it's homing
                except Exception:
                    self.log.error('home_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
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

        # Get the dome internal temperature
        # Note the focusers each have inbuilt temperature sensors, but they always differ by a lot.
        # Best to just use the single measurement for the temperature inside the dome,
        # but store it on each UT's info so if we want to change it in the future it's easier.
        # UPDATE: The H400s don't have temperature sensors, so that simplifies things even further.
        #         We still have to get the dome temp here so we can store it each time we move.
        try:
            dome_temp = get_roomalert('dome')['int_temperature']
            temp_info['dome_temp'] = dome_temp
        except Exception:
            self.log.error('Failed to get dome internal temperature')
            self.log.debug('', exc_info=True)
            temp_info['dome_temp'] = None

        for ut in self.uts:
            # Get info from each interface
            try:
                interface_id = params.UT_DICT[ut]['INTERFACE']
                interface_info = {}
                interface_info['interface_id'] = interface_id

                with daemon_proxy(interface_id) as interface:
                    interface_info['remaining'] = interface.get_focuser_steps_remaining(ut)
                    interface_info['current_pos'] = interface.get_focuser_position(ut)
                    interface_info['limit'] = interface.get_focuser_limit(ut)
                    interface_info['serial_number'] = interface.get_focuser_serial_number(ut)
                    try:
                        interface_info['int_temp'] = interface.get_focuser_temp('internal', ut)
                        interface_info['ext_temp'] = interface.get_focuser_temp('external', ut)
                    except NotImplementedError:
                        # The ASA H400s don't have temperature sensors
                        interface_info['int_temp'] = None
                        interface_info['ext_temp'] = None

                if interface_info['remaining'] > 0:
                    interface_info['status'] = 'Moving'
                    if self.move_steps[ut] == 0:
                        # Homing, needed due to bug in remaining
                        interface_info['remaining'] = interface_info['current_pos']
                else:
                    interface_info['status'] = 'Ready'

                interface_info['current_temp'] = temp_info['dome_temp']
                interface_info['last_move_temp'] = self.last_move_temp[ut]

                temp_info[ut] = interface_info
            except Exception:
                self.log.error('Failed to get focuser {} info'.format(ut))
                self.log.debug('', exc_info=True)
                temp_info[ut] = None

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(ut, temp_info[ut]['status'])
                        for ut in self.uts]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Focusers are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(ut, self.info[ut]['status'])
                            for ut in self.uts]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Focusers are {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    # Control functions
    def set_focusers(self, new_position):
        """Move focuser(s) to the given position(s)."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if not isinstance(new_position, dict):
            new_position = {ut: new_position for ut in self.uts}

        self.wait_for_info()
        retstrs = []
        for ut in sorted(new_position):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the new position is a valid input
            try:
                new_pos = int(new_position[ut])
                assert new_pos == new_position[ut]
            except Exception:
                s = '"{}" is not a valid integer'.format(new_position[ut])
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the new position is within the focuser limit
            if new_pos < 0 or new_pos > self.info[ut]['limit']:
                s = 'New position {} is outside focuser limits (0-{})'.format(
                    new_pos, self.info[ut]['limit'])
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the new position is different from the current position
            if new_pos == self.info[ut]['current_pos']:
                s = 'Focuser is already at position {}'.format(new_pos)
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the focuser is not already moving
            if self.info[ut]['remaining'] > 0:
                s = 'Focuser is already moving'
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            self.move_steps[ut] = new_pos - self.info[ut]['current_pos']
            s = 'Focuser {}: Moving {} steps from {} to {}'.format(
                ut, self.move_steps[ut], self.info[ut]['current_pos'], new_pos)
            retstrs.append(s)

        # Set flag
        self.move_focuser_flag = 1

        # Format return string
        return '\n'.join(retstrs)

    def move_focusers(self, move_steps):
        """Move focuser(s) by the given number of steps."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if not isinstance(move_steps, dict):
            move_steps = {ut: move_steps for ut in self.uts}

        self.wait_for_info()
        retstrs = []
        for ut in sorted(move_steps):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the new position is a valid input
            try:
                steps = int(move_steps[ut])
                assert steps == move_steps[ut]
            except Exception:
                s = '"{}" is not a valid integer'.format(move_steps[ut])
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the new position is within the focuser limit
            new_pos = self.info[ut]['current_pos'] + steps
            if new_pos < 0 or new_pos > self.info[ut]['limit']:
                s = 'New position {} is outside focuser limits (0-{})'.format(
                    new_pos, self.info[ut]['limit'])
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the new position is different from the current position
            if new_pos == self.info[ut]['current_pos']:
                s = 'Focuser is already at position {}'.format(new_pos)
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the focuser is not already moving
            if self.info[ut]['remaining'] > 0:
                s = 'Focuser is already moving'
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            self.move_steps[ut] = new_pos - self.info[ut]['current_pos']
            s = 'Focuser {}: Moving {} steps from {} to {}'.format(
                ut, self.move_steps[ut], self.info[ut]['current_pos'], new_pos)
            retstrs.append(s)

        # Set flag
        self.move_focuser_flag = 1

        # Format return string
        return '\n'.join(retstrs)

    def home_focusers(self, ut_list=None):
        """Move focuser(s) to the home position."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if ut_list is None:
            ut_list = self.uts.copy()

        self.wait_for_info()
        retstrs = []
        for ut in sorted(ut_list):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Check the focuser is not already moving
            if self.info[ut]['remaining'] > 0:
                s = 'Focuser is already moving'
                retstrs.append('Focuser {}: '.format(ut) + misc.errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            s = 'Focuser {}: Moving to home position'.format(ut)
            retstrs.append(s)

        # Set flag
        self.home_focuser_flag = 1

        # Format return string
        return '\n'.join(retstrs)


if __name__ == '__main__':
    daemon_id = 'foc'
    with misc.make_pid_file(daemon_id):
        FocDaemon()._run()
