#!/usr/bin/env python3
"""Daemon to control focusers."""

import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.daemons import (BaseDaemon, DaemonDependencyError, HardwareError,
                                   daemon_proxy, get_daemon_host)

import numpy as np


class FocDaemon(BaseDaemon):
    """Focuser hardware daemon class."""

    def __init__(self):
        super().__init__('foc')

        # command flags
        self.move_focuser_flag = 0
        self.set_focuser_flag = 0
        self.home_focuser_flag = 0
        self.stop_focuser_flag = 0
        self.sync_focuser_flag = 0

        # focuser variables
        self.uts = params.UTS_WITH_FOCUSERS.copy()
        self.active_uts = []
        self.interfaces = {f'foc{ut}' for ut in self.uts}

        self.move_steps = {ut: 0 for ut in self.uts}
        self.set_position = {ut: 0 for ut in self.uts}
        self.sync_position = {ut: 0 for ut in self.uts}

        self.last_move_time = {ut: None for ut in self.uts}
        self.last_move_temp = {ut: None for ut in self.uts}

        # dependencies
        for interface_id in self.interfaces:
            self.dependencies.add(interface_id)

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')
        self.check_period = params.DAEMON_CHECK_PERIOD
        self.check_time = 0
        self.force_check_flag = True

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
                        move_steps = int(self.move_steps[ut])
                        current_pos = self.info[ut]['current_pos']
                        new_pos = current_pos + move_steps
                        msg = 'Moving focuser {} {:+d} steps from {} to {} (moving)'.format(
                            ut, move_steps, current_pos, new_pos)
                        self.log.info(msg)

                        try:
                            with daemon_proxy(f'foc{ut}') as interface:
                                reply = interface.move_focuser(move_steps)
                                if reply:
                                    self.log.info(reply)
                            self.last_move_time[ut] = self.loop_time
                            self.last_move_temp[ut] = self.info[ut]['current_temp']

                        except Exception:
                            self.log.error('No response from interface foc{}'.format(ut))
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
                        new_pos = int(self.set_position[ut])
                        current_pos = self.info[ut]['current_pos']
                        move_steps = new_pos - current_pos
                        msg = 'Moving focuser {} {:+d} steps from {} to {} (setting)'.format(
                            ut, move_steps, current_pos, new_pos)
                        self.log.info(msg)

                        try:
                            with daemon_proxy(f'foc{ut}') as interface:
                                # Only the ASA focusers have explicit set commands,
                                # the others we just do moves
                                if self.info[ut]['can_set']:
                                    reply = interface.set_focuser(new_pos)
                                else:
                                    reply = interface.move_focuser(move_steps)
                                if reply:
                                    self.log.info(reply)
                            self.last_move_time[ut] = self.loop_time
                            self.last_move_temp[ut] = self.info[ut]['current_temp']

                        except Exception:
                            self.log.error('No response from interface foc{}'.format(ut))
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
                        self.log.info('Homing focuser {}'.format(ut))

                        try:
                            with daemon_proxy(f'foc{ut}') as interface:
                                reply = interface.home_focuser()
                                if reply:
                                    self.log.info(reply)
                            self.last_move_time[ut] = self.loop_time

                            # mark that it's homing
                            self.move_steps[ut] = 0

                        except Exception:
                            self.log.error('No response from interface foc{}'.format(ut))
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
                        self.log.info('Stopping focuser {}'.format(ut))

                        try:
                            with daemon_proxy(f'foc{ut}') as interface:
                                reply = interface.stop_focuser()
                                if reply:
                                    self.log.info(reply)
                            self.last_move_time[ut] = self.loop_time

                            # mark that it's stopped
                            self.move_steps[ut] = 0

                        except Exception:
                            self.log.error('No response from interface foc{}'.format(ut))
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
                        position = int(self.sync_position[ut])
                        self.log.info('Syncing focuser {} position to {}'.format(ut, position))

                        try:
                            with daemon_proxy(f'foc{ut}') as interface:
                                reply = interface.sync_focuser(position)
                                if reply:
                                    self.log.info(reply)

                        except Exception:
                            self.log.error('No response from interface foc{}'.format(ut))
                            self.log.debug('', exc_info=True)
                except Exception:
                    self.log.error('sync_focuser command failed')
                    self.log.debug('', exc_info=True)
                self.active_uts = []
                self.sync_focuser_flag = 0
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')

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
            with daemon_proxy('conditions', timeout=30) as daemon:
                conditions_info = daemon.get_info(force_update=False)
            int_temperature = np.max([conditions_info['internal']['temperature'][source]
                                      for source in conditions_info['internal']['temperature']])
            temp_info['dome_temp'] = int_temperature
        except Exception:
            self.log.error('Failed to get dome internal temperature')
            self.log.debug('', exc_info=True)
            temp_info['dome_temp'] = None

        # Get info from each UT
        temp_info['uts'] = self.uts.copy()
        for ut in self.uts:
            try:
                ut_info = {}
                ut_info['interface_id'] = f'foc{ut}'

                with daemon_proxy(f'foc{ut}') as interface:
                    ut_info['serial_number'] = interface.get_serial_number()
                    ut_info['hw_class'] = interface.get_class()
                    ut_info['current_pos'] = interface.get_position()
                    ut_info['limit'] = interface.get_limit()
                    ut_info['can_set'] = interface.can_set()
                    ut_info['can_stop'] = interface.can_stop()
                    ut_info['can_sync'] = interface.can_sync()
                    try:
                        ut_info['remaining'] = interface.get_steps_remaining()
                    except NotImplementedError:
                        # The ASA H400s don't store steps remaining
                        ut_info['remaining'] = 0
                    try:
                        ut_info['int_temp'] = interface.get_temp('internal')
                        ut_info['ext_temp'] = interface.get_temp('external')
                    except NotImplementedError:
                        # The ASA H400s don't have temperature sensors
                        ut_info['int_temp'] = None
                        ut_info['ext_temp'] = None
                    try:
                        ut_info['status'] = interface.get_focuser_status()
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
            now_strs = ['{}:{}'.format(ut,
                                       temp_info[ut]['status']
                                       if temp_info[ut] is not None else 'ERROR')
                        for ut in self.uts]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Focusers are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(ut,
                                           self.info[ut]['status']
                                           if self.info[ut] is not None else 'ERROR')
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
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if not isinstance(new_position, dict):
            new_position = {ut: new_position for ut in self.uts}
        if any(ut not in self.uts for ut in new_position):
            raise ValueError(f'Invalid UTs: {[ut for ut in new_position if ut not in self.uts]}')

        self.wait_for_info()
        if any(new_position[ut] < 0 for ut in new_position):
            bad_positions = {ut: new_position[ut] for ut in new_position if new_position[ut] < 0}
            raise HardwareError(f'Invalid focuser positions: {bad_positions}')
        if any(new_position[ut] > self.info[ut]['limit'] for ut in new_position):
            bad_positions = {ut: new_position[ut] for ut in new_position
                             if new_position[ut] > self.info[ut]['limit']}
            raise HardwareError(f'Invalid focuser positions: {bad_positions}')
        if any(self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving'
               for ut in new_position):
            bad_uts = [ut for ut in new_position
                       if self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving']
            raise HardwareError(f'Focusers are already moving: {bad_uts}')

        # Need to be integers
        new_position = {ut: int(new_position[ut]) for ut in new_position}

        self.active_uts = sorted(new_position)
        self.set_position.update(new_position)
        self.set_focuser_flag = 1

    def move_focusers(self, move_steps):
        """Move focuser(s) by the given number of steps."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if not isinstance(move_steps, dict):
            move_steps = {ut: move_steps for ut in self.uts}
        if any(ut not in self.uts for ut in move_steps):
            raise ValueError(f'Invalid UTs: {[ut for ut in move_steps if ut not in self.uts]}')

        self.wait_for_info()
        new_position = {ut: self.info[ut]['current_pos'] + move_steps[ut] for ut in move_steps}
        if any(new_position[ut] < 0 for ut in new_position):
            bad_positions = {ut: new_position[ut] for ut in new_position if new_position[ut] < 0}
            raise HardwareError(f'Invalid focuser positions: {bad_positions}')
        if any(new_position[ut] > self.info[ut]['limit'] for ut in new_position):
            bad_positions = {ut: new_position[ut] for ut in new_position
                             if new_position[ut] > self.info[ut]['limit']}
            raise HardwareError(f'Invalid focuser positions: {bad_positions}')
        if any(self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving'
               for ut in move_steps):
            bad_uts = [ut for ut in move_steps
                       if self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving']
            raise HardwareError(f'Focusers are already moving: {bad_uts}')

        # Need to be integers
        move_steps = {ut: int(move_steps[ut]) for ut in move_steps}

        self.active_uts = sorted(move_steps)
        self.move_steps.update(move_steps)
        self.move_focuser_flag = 1

    def home_focusers(self, uts=None):
        """Move focuser(s) to the home position."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if uts is None:
            uts = self.uts.copy()
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')

        self.wait_for_info()
        if any(self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving'
               for ut in uts):
            bad_uts = [ut for ut in uts
                       if self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving']
            raise HardwareError(f'Focusers are already moving: {bad_uts}')

        self.active_uts = sorted(uts)
        self.home_focuser_flag = 1

    def stop_focusers(self, uts=None):
        """Stop focuser(s) moving."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if uts is None:
            uts = self.uts.copy()
        if any(ut not in self.uts for ut in uts):
            raise ValueError(f'Invalid UTs: {[ut for ut in uts if ut not in self.uts]}')

        self.wait_for_info()
        if any(not self.info[ut]['can_stop'] for ut in uts):
            bad_uts = [ut for ut in uts if not self.info[ut]['can_stop']]
            raise HardwareError(f'Focusers do not a stop command: {bad_uts}')

        self.active_uts = sorted(uts)
        self.stop_focuser_flag = 1

    def sync_focusers(self, position):
        """Sync focuser(s) position to the given value."""
        if self.dependency_error:
            raise DaemonDependencyError(f'Dependencies are not responding: {self.bad_dependencies}')
        if not isinstance(position, dict):
            position = {ut: position for ut in self.uts}
        if any(ut not in self.uts for ut in position):
            raise ValueError(f'Invalid UTs: {[ut for ut in position if ut not in self.uts]}')

        self.wait_for_info()
        if any(not self.info[ut]['can_sync'] for ut in position):
            bad_uts = [ut for ut in position if not self.info[ut]['can_sync']]
            raise HardwareError(f'Focusers do not a sync command: {bad_uts}')
        if any(position[ut] < 0 for ut in position):
            bad_positions = {ut: position[ut] for ut in position if position[ut] < 0}
            raise HardwareError(f'Invalid focuser positions: {bad_positions}')
        if any(position[ut] > self.info[ut]['limit'] for ut in position):
            bad_positions = {ut: position[ut] for ut in position
                             if position[ut] > self.info[ut]['limit']}
            raise HardwareError(f'Invalid focuser positions: {bad_positions}')
        if any(self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving'
               for ut in position):
            bad_uts = [ut for ut in position
                       if self.info[ut]['remaining'] > 0 or self.info[ut]['status'] == 'Moving']
            raise HardwareError(f'Focusers are already moving: {bad_uts}')

        # Need to be integers
        position = {ut: int(position[ut]) for ut in position}

        self.active_uts = sorted(position)
        self.sync_position.update(position)
        self.sync_focuser_flag = 1

    # Info function
    def get_info_string(self, verbose=False, force_update=False):
        """Get a string for printing status info."""
        info = self.get_info(force_update)
        if not verbose:
            msg = ''
            lim = max(len(str(info[ut]['limit'])) for ut in info['uts'])
            for ut in info['uts']:
                host, port = get_daemon_host(info[ut]['interface_id'])
                msg += 'FOCUSER {} ({}:{}) '.format(ut, host, port)
                if info[ut]['status'] != 'Moving':
                    cur_pos = '{:>{}d}'.format(info[ut]['current_pos'], lim)
                    limit = '{:<{}d}'.format(info[ut]['limit'], lim)
                    msg += '  Current position: {}/{} '.format(cur_pos, limit)
                    msg += '  [{}]\n'.format(info[ut]['status'])
                else:
                    msg += '  Moving '
                    if info[ut]['remaining'] > 0:
                        msg += ' ({})\n'.format(info[ut]['remaining'])
                    else:
                        msg += '\n'
            msg = msg.rstrip()
        else:
            msg = '###### FOCUSER INFO #######\n'
            for ut in info['uts']:
                host, port = get_daemon_host(info[ut]['interface_id'])
                msg += 'FOCUSER {} ({}:{})\n'.format(ut, host, port)
                msg += 'Status: {} '.format(info[ut]['status'])
                if info[ut]['remaining'] > 0:
                    msg += (' ({})\n'.format(info[ut]['remaining']))
                else:
                    msg += ('\n')
                msg += 'Current motor pos:    {}\n'.format(info[ut]['current_pos'])
                msg += 'Maximum motor limit:  {}\n'.format(info[ut]['limit'])
                msg += 'Internal temperature: {}\n'.format(info[ut]['int_temp'])
                msg += 'External temperature: {}\n'.format(info[ut]['ext_temp'])
                msg += 'Serial number:        {}\n'.format(info[ut]['serial_number'])
                msg += 'Hardware class:       {}\n'.format(info[ut]['hw_class'])
                msg += '~~~~~~~\n'
            msg += 'Uptime: {:.1f}s\n'.format(info['uptime'])
            msg += 'Timestamp: {}\n'.format(info['timestamp'])
            msg += '###########################'
        return msg


if __name__ == '__main__':
    daemon = FocDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
