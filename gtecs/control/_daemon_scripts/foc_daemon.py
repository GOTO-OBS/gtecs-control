#!/usr/bin/env python3
"""Daemon to control focusers  via the UT interface daemons."""

import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import errors
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, daemon_proxy
from gtecs.control.observing import get_conditions
from gtecs.control.style import errortxt


class FocDaemon(BaseDaemon):
    """Focuser hardware daemon class."""

    def __init__(self):
        super().__init__('foc')

        # foc is dependent on all the interfaces
        for interface_id in params.INTERFACES:
            self.dependencies.add(interface_id)

        # command flags
        self.move_focuser_flag = 0
        self.set_focuser_flag = 0
        self.home_focuser_flag = 0
        self.stop_focuser_flag = 0
        self.sync_focuser_flag = 0

        # focuser variables
        self.uts = params.UTS_WITH_FOCUSERS.copy()
        self.active_uts = []
        self.move_steps = {ut: 0 for ut in self.uts}
        self.set_position = {ut: 0 for ut in self.uts}
        self.sync_position = {ut: 0 for ut in self.uts}

        self.last_move_time = {ut: None for ut in self.uts}
        self.last_move_temp = {ut: None for ut in self.uts}

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

        while self.running:
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
                        current_pos = self.info[ut]['current_pos']
                        new_pos = current_pos + move_steps

                        s = 'Moving focuser {} ({}) ' .format(ut, interface_id)
                        s += '{:+d} steps from {} to {} (moving)'.format(
                            move_steps, current_pos, new_pos)
                        self.log.info(s)

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.move_focuser(move_steps, ut)
                                if c:
                                    self.log.info(c)
                            self.last_move_time[ut] = self.loop_time
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

            # set the focuser
            if self.set_focuser_flag:
                try:
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        new_pos = self.set_position[ut]
                        current_pos = self.info[ut]['current_pos']
                        move_steps = new_pos - current_pos

                        s = 'Moving focuser {} ({}) ' .format(ut, interface_id)
                        s += '{:+d} steps from {} to {} (setting)'.format(
                            move_steps, current_pos, new_pos)
                        self.log.info(s)

                        try:
                            with daemon_proxy(interface_id) as interface:
                                # Only the ASA focusers have explicit set commands,
                                # the others we just do moves
                                if self.info[ut]['can_set']:
                                    c = interface.set_focuser(new_pos, ut)
                                else:
                                    c = interface.move_focuser(move_steps, ut)
                                if c:
                                    self.log.info(c)
                            self.last_move_time[ut] = self.loop_time
                            self.last_move_temp[ut] = self.info[ut]['current_temp']

                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('set_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.set_focuser_flag = 0
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
                            self.last_move_time[ut] = self.loop_time

                            # mark that it's homing
                            self.move_steps[ut] = 0

                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                except Exception:
                    self.log.error('home_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.home_focuser_flag = 0
                self.force_check_flag = True

            # stop the focuser
            if self.stop_focuser_flag:
                try:
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']

                        self.log.info('Stopping focuser {} ({})'.format(
                                      ut, interface_id))

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.stop_focuser(ut)
                                if c:
                                    self.log.info(c)
                            self.last_move_time[ut] = self.loop_time

                            # mark that it's stopped
                            self.move_steps[ut] = 0

                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)

                except Exception:
                    self.log.error('stop_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.stop_focuser_flag = 0
                self.force_check_flag = True

            # sync the focuser
            if self.sync_focuser_flag:
                try:
                    for ut in self.active_uts:
                        interface_id = params.UT_DICT[ut]['INTERFACE']
                        position = self.sync_position[ut]

                        self.log.info('Syncing focuser position to {}'.format(position))

                        try:
                            with daemon_proxy(interface_id) as interface:
                                c = interface.sync_focuser(position, ut)
                                if c:
                                    self.log.info(c)

                        except Exception:
                            self.log.error('No response from interface {}'.format(interface_id))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('sync_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.sync_focuser_flag = 0
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the hardware."""
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
            int_conditions = get_conditions()['internal']
            temp_info['dome_temp'] = int_conditions['temperature']
        except Exception:
            self.log.error('Failed to get dome internal temperature')
            self.log.debug('', exc_info=True)
            temp_info['dome_temp'] = None

        # Get info from each UT
        temp_info['uts'] = self.uts.copy()
        for ut in self.uts:
            try:
                ut_info = {}
                interface_id = params.UT_DICT[ut]['INTERFACE']
                ut_info['interface_id'] = interface_id

                with daemon_proxy(interface_id) as interface:
                    ut_info['serial_number'] = interface.get_focuser_serial_number(ut)
                    ut_info['hw_class'] = interface.get_focuser_class(ut)
                    ut_info['current_pos'] = interface.get_focuser_position(ut)
                    ut_info['limit'] = interface.get_focuser_limit(ut)
                    ut_info['can_set'] = interface.focuser_can_set(ut)
                    ut_info['can_stop'] = interface.focuser_can_stop(ut)
                    ut_info['can_sync'] = interface.focuser_can_sync(ut)
                    try:
                        ut_info['remaining'] = interface.get_focuser_steps_remaining(ut)
                    except NotImplementedError:
                        # The ASA H400s don't store steps remaining
                        ut_info['remaining'] = 0
                    try:
                        ut_info['int_temp'] = interface.get_focuser_temp('internal', ut)
                        ut_info['ext_temp'] = interface.get_focuser_temp('external', ut)
                    except NotImplementedError:
                        # The ASA H400s don't have temperature sensors
                        ut_info['int_temp'] = None
                        ut_info['ext_temp'] = None
                    try:
                        ut_info['status'] = interface.get_focuser_status(ut)
                    except NotImplementedError:
                        # The FLI focusers don't have a status
                        if ut_info['remaining'] > 0:
                            ut_info['status'] = 'Moving'
                            if self.move_steps[ut] == 0:
                                # Homing, needed due to bug in remaining
                                ut_info['remaining'] = ut_info['current_pos']
                        else:
                            ut_info['status'] = 'Ready'

                ut_info['last_move_time'] = self.last_move_time[ut]
                ut_info['current_temp'] = temp_info['dome_temp']
                ut_info['last_move_temp'] = self.last_move_temp[ut]

                temp_info[ut] = ut_info
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
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check the new position is a valid input
            try:
                new_pos = int(new_position[ut])
                assert new_pos == new_position[ut]
            except Exception:
                s = '"{}" is not a valid integer'.format(new_position[ut])
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check the new position is within the focuser limit
            if new_pos < 0 or new_pos > self.info[ut]['limit']:
                s = 'New position {} is outside focuser limits (0-{})'.format(
                    new_pos, self.info[ut]['limit'])
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # # Check the new position is different from the current position
            # if new_pos == self.info[ut]['current_pos']:
            #     s = 'Focuser is already at position {}'.format(new_pos)
            #     retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
            #     continue

            # Check the focuser is not already moving
            if self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving':
                s = 'Focuser is already moving'
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            self.set_position[ut] = new_pos
            s = 'Focuser {}: Moving from {} to {} ({:+d} steps)'.format(
                ut, self.info[ut]['current_pos'], self.set_position[ut],
                self.set_position[ut] - self.info[ut]['current_pos'])
            retstrs.append(s)

        # Set flag
        self.set_focuser_flag = 1

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
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check the new position is a valid input
            try:
                steps = int(move_steps[ut])
                assert steps == move_steps[ut]
            except Exception:
                s = '"{}" is not a valid integer'.format(move_steps[ut])
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check the new position is within the focuser limit
            new_pos = self.info[ut]['current_pos'] + steps
            if new_pos < 0 or new_pos > self.info[ut]['limit']:
                s = 'New position {} is outside focuser limits (0-{})'.format(
                    new_pos, self.info[ut]['limit'])
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # # Check the new position is different from the current position
            # if new_pos == self.info[ut]['current_pos']:
            #     s = 'Focuser is already at position {}'.format(new_pos)
            #     retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
            #     continue

            # Check the focuser is not already moving
            if self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving':
                s = 'Focuser is already moving'
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            self.move_steps[ut] = new_pos - self.info[ut]['current_pos']
            s = 'Focuser {}: Moving {:+d} steps (from {} to {})'.format(
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
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check the focuser is not already moving
            if self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving':
                s = 'Focuser is already moving'
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            s = 'Focuser {}: Moving to home position'.format(ut)
            retstrs.append(s)

        # Set flag
        self.home_focuser_flag = 1

        # Format return string
        return '\n'.join(retstrs)

    def stop_focusers(self, ut_list=None):
        """Stop focuser(s) moving."""
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
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check if the focuser has a stop command
            if not self.info[ut]['can_stop']:
                s = 'Focuser does not a stop command'
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            s = 'Focuser {}: Stopping movement'.format(ut)
            retstrs.append(s)

        # Set flag
        self.stop_focuser_flag = 1

        # Format return string
        return '\n'.join(retstrs)

    def sync_focusers(self, position):
        """Sync focuser(s) position to the given value."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Format input
        if not isinstance(position, dict):
            position = {ut: position for ut in self.uts}

        self.wait_for_info()
        retstrs = []
        for ut in sorted(position):
            # Check the UT ID is valid
            if ut not in self.uts:
                s = 'Unit telescope ID "{}" not in list {}'.format(ut, self.uts)
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check the new position is a valid input
            try:
                sync_pos = int(position[ut])
                assert sync_pos == position[ut]
            except Exception:
                s = '"{}" is not a valid integer'.format(position[ut])
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check if the focuser has a sync command
            if not self.info[ut]['can_sync']:
                s = 'Focuser does not a sync command'
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check the new position is within the focuser limit
            if sync_pos < 0 or sync_pos > self.info[ut]['limit']:
                s = 'New position {} is outside focuser limits (0-{})'.format(
                    sync_pos, self.info[ut]['limit'])
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Check the focuser is not moving
            if self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving':
                s = 'Focuser is moving'
                retstrs.append('Focuser {}: '.format(ut) + errortxt(s))
                continue

            # Set values
            self.active_uts += [ut]
            self.sync_position[ut] = sync_pos
            s = 'Focuser {}: Setting current position {} to {}'.format(
                ut, self.info[ut]['current_pos'], sync_pos)
            retstrs.append(s)

        # Set flag
        self.sync_focuser_flag = 1

        # Format return string
        return '\n'.join(retstrs)


if __name__ == '__main__':
    with make_pid_file('foc'):
        FocDaemon()._run()
