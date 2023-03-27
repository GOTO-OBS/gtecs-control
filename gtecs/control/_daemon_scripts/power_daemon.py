#!/usr/bin/env python3
"""Daemon to control APC power devices."""

import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon
from gtecs.control.hardware.power import APCATS, APCPDU, APCUPS, APCUPS_USB, EPCPDU, ETHPDU
from gtecs.control.hardware.power import FakePDU, FakeUPS


class PowerDaemon(BaseDaemon):
    """Power hardware daemon class."""

    def __init__(self):
        super().__init__('power')

        # hardware
        self.power_units = {unit_name: None for unit_name in params.POWER_UNITS}

        # command flags
        self.on_flag = 0
        self.off_flag = 0
        self.reboot_flag = 0

        # power variables
        self.power_status = {}

        self.current_units = []
        self.current_outlets = []

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

                # Try to connect to the hardware
                self._connect()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's successful
                if self.hardware_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

                # If there is an error then getting info failed.
                # Restart the loop to try reconnecting above.
                if self.hardware_error:
                    continue

            # control functions
            # power on a specified outlet
            if self.on_flag:
                try:
                    for unit, outlet in zip(self.current_units, self.current_outlets):
                        name = params.POWER_UNITS[unit]['NAMES'][outlet - 1]
                        if outlet == 0:
                            name = 'ALL'
                        self.log.info('Powering on {} outlet {} ({})'.format(unit, outlet, name))
                        power = self.power_units[unit]
                        reply = power.on(outlet)
                        if reply:
                            self.log.info(reply)
                except Exception:
                    self.log.error('on command failed')
                    self.log.debug('', exc_info=True)
                self.current_units = []
                self.current_outlets = []
                self.on_flag = 0
                self.force_check_flag = True

            # power off a specified outlet
            if self.off_flag:
                try:
                    for unit, outlet in zip(self.current_units, self.current_outlets):
                        name = params.POWER_UNITS[unit]['NAMES'][outlet - 1]
                        if outlet == 0:
                            name = 'ALL'
                        self.log.info('Powering off {} outlet {} ({})'.format(unit, outlet, name))
                        power = self.power_units[unit]
                        reply = power.off(outlet)
                        if reply:
                            self.log.info(reply)
                except Exception:
                    self.log.error('off command failed')
                    self.log.debug('', exc_info=True)
                self.current_units = []
                self.current_outlets = []
                self.off_flag = 0
                self.force_check_flag = True

            # reboot a specified outlet
            if self.reboot_flag:
                try:
                    for unit, outlet in zip(self.current_units, self.current_outlets):
                        name = params.POWER_UNITS[unit]['NAMES'][outlet - 1]
                        if outlet == 0:
                            name = 'ALL'
                        self.log.info('Rebooting {} outlet {} ({})'.format(unit, outlet, name))
                        power = self.power_units[unit]
                        reply = power.reboot(outlet)
                        if reply:
                            self.log.info(reply)
                except Exception:
                    self.log.error('reboot command failed')
                    self.log.debug('', exc_info=True)
                self.current_units = []
                self.current_outlets = []
                self.reboot_flag = 0
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')

    # Internal functions
    def _connect(self):
        """Connect to hardware."""
        for unit_name in params.POWER_UNITS:
            # Connect to each unit
            if self.power_units[unit_name] is None:
                unit_params = params.POWER_UNITS[unit_name].copy()
                unit_class = unit_params['CLASS']
                unit_ip = unit_params['IP']
                if 'PORT' in unit_params:
                    unit_port = int(unit_params['PORT'])

                # create power object by class
                if unit_class == 'FakePDU':
                    self.power_units[unit_name] = FakePDU(unit_ip)
                    self.log.info('Connected to {}'.format(unit_name))

                elif unit_class == 'FakeUPS':
                    self.power_units[unit_name] = FakeUPS(unit_ip)
                    self.log.info('Connected to {}'.format(unit_name))

                elif unit_class == 'APCPDU':
                    try:
                        unit_outlets = len(unit_params['NAMES'])
                        self.power_units[unit_name] = APCPDU(unit_ip, unit_outlets)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        if unit_name not in self.bad_hardware:
                            self.log.error('Failed to connect to {}'.format(unit_name))
                            self.bad_hardware.add(unit_name)

                elif unit_class == 'APCUPS':
                    try:
                        self.power_units[unit_name] = APCUPS(unit_ip)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        if unit_name not in self.bad_hardware:
                            self.log.error('Failed to connect to {}'.format(unit_name))
                            self.bad_hardware.add(unit_name)

                elif unit_class == 'APCUPS_USB':
                    try:
                        self.power_units[unit_name] = APCUPS_USB(unit_ip, unit_port)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        if unit_name not in self.bad_hardware:
                            self.log.error('Failed to connect to {}'.format(unit_name))
                            self.bad_hardware.add(unit_name)

                elif unit_class == 'APCATS':
                    try:
                        self.power_units[unit_name] = APCATS(unit_ip)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        if unit_name not in self.bad_hardware:
                            self.log.error('Failed to connect to {}'.format(unit_name))
                            self.bad_hardware.add(unit_name)

                elif unit_class == 'EPCPDU':
                    try:
                        unit_outlets = len(unit_params['NAMES'])
                        self.power_units[unit_name] = EPCPDU(unit_ip, unit_outlets)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        if unit_name not in self.bad_hardware:
                            self.log.error('Failed to connect to {}'.format(unit_name))
                            self.bad_hardware.add(unit_name)

                elif unit_class == 'ETHPDU':
                    try:
                        unit_port = int(unit_params['PORT'])
                        unit_outlets = len(unit_params['NAMES'])
                        unit_nc = bool(unit_params['NC']) if 'NC' in unit_params else False
                        self.power_units[unit_name] = ETHPDU(unit_ip, unit_port,
                                                             unit_outlets, unit_nc)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        if unit_name not in self.bad_hardware:
                            self.log.error('Failed to connect to {}'.format(unit_name))
                            self.bad_hardware.add(unit_name)

        # Finally check if we need to report an error
        self._check_errors()

    def _get_info(self):
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        for unit_name in self.power_units:
            # Get info from each unit
            try:
                power = self.power_units[unit_name]
                temp_status = {}
                if hasattr(power, 'outlets'):
                    if power.unit_type == 'PDU':
                        outlet_statuses = power.status()
                    elif power.unit_type == 'UPS':
                        outlet_statuses = power.outlet_status()
                    temp_status['outlet_statuses'] = outlet_statuses
                    outlet_names = params.POWER_UNITS[unit_name]['NAMES']
                    for name, status in zip(outlet_names, outlet_statuses):
                        if status == str(power.on_value):
                            temp_status[name] = 'on'
                        elif status == str(power.off_value):
                            temp_status[name] = 'off'
                        else:
                            temp_status[name] = 'ERROR'
                if power.unit_type == 'UPS':
                    temp_status['status'] = power.status()
                    temp_status['percent'] = power.percent_remaining()
                    temp_status['time'] = power.time_remaining()
                    temp_status['load'] = power.load()
                if power.unit_type == 'ATS':
                    temp_status['status'] = power.status()
                    temp_status['status_A'] = power.source_status('A')
                    temp_status['status_B'] = power.source_status('B')
                    temp_status['source'] = power.active_source()
                temp_info['status_' + unit_name] = temp_status
            except Exception:
                self.log.error('Failed to get {} info'.format(unit_name))
                self.log.debug('', exc_info=True)
                temp_status[unit_name] = None
                # Report the connection as failed
                self.power_units[unit_name] = None
                if unit_name not in self.bad_hardware:
                    self.bad_hardware.add(unit_name)

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(unit, temp_info['status_' + unit]['outlet_statuses'])
                        if 'outlet_statuses' in temp_info['status_' + unit]
                        else '{}:{}'.format(unit, temp_info['status_' + unit]['status'])
                        for unit in sorted(params.POWER_UNITS)
                        ]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Power units are {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(unit, self.info['status_' + unit]['outlet_statuses'])
                            if 'outlet_statuses' in temp_info['status_' + unit]
                            else '{}:{}'.format(unit, temp_info['status_' + unit]['status'])
                            for unit in sorted(params.POWER_UNITS)
                            ]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Power units are {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    def _parse_input(self, outlet_list, unit=''):
        """Parse an input list from the power control script.

        Two options:
        - If `unit` is given then all the outlets should be from that unit, so they might just
          be numbers (e.g. "power on PDU1 1,2,3"). This is rarely used.
        - More commonly, unit won't be given and outlet_list will be a list of outlet names,
          which might be from various different units (e.g. "power on foc1,filt1,leds").
          This might also include outlet groups, which will be expanded (e.g. leds->led1,led2).

        The power daemon expects two equal-length lists of `current_units` and `current_outlets`,
        with the latter being only unit numbers not names.
        This function will parse the input names and return those lists.

        Example:
        -------
        "power on foc1,filt1,leds"
        - First 'leds' is a group, which is expanded to 'led1' and 'led2'
        - Now we have four outlets: 'foc1' and 'filt1' are on unit 'EAST', 'led1' is on 'PDU1' and
          'led2' is on 'PDU2'.
        - So this function will return two lists:
          outlets = ['EAST','EAST','PDU1','PDU2']
          units = ['filt1','foc1','led1','led2']
          Note they will be sorted in the order of outlet then unit.

        """
        if unit in params.POWER_UNITS:
            # A specific unit was given, all the outlets should be numbers from that unit.
            outlets = self._get_valid_outlets(unit, outlet_list)
            units = [unit] * len(outlets)
        else:
            # A list of outlet names was given, which might contain groups and/or be from multiple
            # different units.

            # First expand any group names.
            for outlet in outlet_list.copy():
                if outlet in params.POWER_GROUPS:
                    outlet_list.remove(outlet)
                    outlet_list += params.POWER_GROUPS[outlet]

            # Now remove duplicate outlets.
            # This could happen either from user input "power on foc1,foc1,foc1" or from expanding
            # groups that contain an outlet name already added.
            # TODO: Really this whole daemon should be rewritten to use sets...
            outlet_list = list(set(outlet_list))

            # We have a list of unique outlet names, now we need to find their matching units.
            # This funcion will return the UNIQUE list of units, so not a direct map between
            # outlet and unit.
            unit_list = self._units_from_names(outlet_list)

            # Finally for each unit go through and get which of the outlets from the initial list
            # are on that unit.
            # The final lists of `units` and `outlets` will be of the same length and a direct
            # mapping between the two, so order matters once they're created.
            units = []
            outlets = []
            for unit in sorted(unit_list):
                valid_outlets = self._get_valid_outlets(unit, outlet_list)
                outlets += sorted(valid_outlets)
                units += [unit] * len(valid_outlets)

        return outlets, units

    def _units_from_names(self, name_list):
        """Given a list of outlet names, return the units they are on.

        If multiple outlets are given that are on the same unit this function will only return the
        unit name once.
        """
        unit_list = []
        for name in name_list:
            found_units = []
            for unit in params.POWER_UNITS:
                try:
                    if name in params.POWER_UNITS[unit]['NAMES'] or str(name) in ['0', 'all']:
                        found_units.append(unit)
                except Exception:
                    pass  # for UPSs, that don't have NAMES
            if len(found_units) > 1 and str(name) not in ['0', 'all']:
                raise ValueError('Duplicate names defined in params')
            for unit in found_units:
                if unit not in unit_list:
                    unit_list.append(unit)
        return sorted(unit_list)

    def _get_valid_outlets(self, unit, outlet_list):
        """Check outlets are valid for the given unit, and convert any names to numbers."""
        if 'NAMES' not in params.POWER_UNITS[unit]:
            return []
        names = params.POWER_UNITS[unit]['NAMES']
        n_outlets = len(names)
        valid_list = []
        for outlet in outlet_list:
            if outlet == 'all':
                valid_list = [0]
            elif outlet.isdigit():
                x = int(outlet)
                if 0 <= x < (n_outlets + 1):
                    valid_list.append(x)
            elif outlet in names:
                valid_list.append(names.index(outlet) + 1)
        return sorted(valid_list)

    # Control functions
    def on(self, outlet_list, unit=''):
        """Power on given outlet(s)."""
        outlets, units = self._parse_input(outlet_list, unit)
        if len(outlets) == 0:
            raise ValueError('No valid outlets or groups')

        self.current_outlets = outlets
        self.current_units = units
        self.on_flag = 1

    def off(self, outlet_list, unit=''):
        """Power off given outlet(s)."""
        outlets, units = self._parse_input(outlet_list, unit)
        if len(outlets) == 0:
            raise ValueError('No valid outlets or groups')

        self.current_outlets = outlets
        self.current_units = units
        self.off_flag = 1

    def reboot(self, outlet_list, unit=''):
        """Reboot given outlet(s)."""
        outlets, units = self._parse_input(outlet_list, unit)
        if len(outlets) == 0:
            raise ValueError('No valid outlets or groups')

        self.current_outlets = outlets
        self.current_units = units
        self.reboot_flag = 1

    def dashboard_switch(self, outlet_name, enable, dashboard_username):
        """Switch a named switch parameter on or off from the web dashboard.

        This function is restricted to only the dashboard IP for specific outlets,
        and also has extra logging.
        See https://github.com/GOTO-OBS/g-tecs/issues/535 for details.
        """
        client_ip = self._get_client_ip()
        if client_ip != params.DASHBOARD_IP:
            return 1
        if outlet_name not in params.DASHBOARD_ALLOWED_OUTLETS:
            return 1
        outlets, units = self._parse_input([outlet_name])
        if len(outlets) == 0:
            return 1

        if enable:
            out_str = f'Web dashboard user {dashboard_username} turning on "{outlet_name}"'
        else:
            out_str = f'Web dashboard user {dashboard_username} turning off "{outlet_name}"'
        self.log.info(out_str)
        self.current_outlets = outlets
        self.current_units = units
        if enable:
            self.on_flag = 1
        else:
            self.off_flag = 1

    def get_info_string(self, verbose=False, force_update=False):
        """Get a string for printing status info."""
        info = self.get_info(force_update)
        if not verbose:
            msg = ''
            for unit in params.POWER_UNITS:
                unit_class = params.POWER_UNITS[unit]['CLASS']
                ip = params.POWER_UNITS[unit]['IP']
                if 'PORT' in params.POWER_UNITS[unit]:
                    port = params.POWER_UNITS[unit]['PORT']
                    port_str = ':{}'.format(port)
                status = info['status_' + unit]
                if 'status' in status:
                    unit_status = status['status']
                    msg += '{} ({}{})       [{}]\n'.format(unit, ip, port_str, unit_status)
                else:
                    msg += '{} ({}{})\n'.format(unit, ip, port_str)
                if 'UPS' in unit_class:
                    msg += '   Load: {: >5}%\n'.format(status['load'])
                    msg += '   Remaining: {}% ({}s)\n'.format(status['percent'], status['time'])
                if 'ATS' in unit_class:
                    if status['source'] == 'A':
                        source_status = status['status_A']
                    else:
                        source_status = status['status_B']
                    msg += '   Current source: {} ({})\n'.format(status['source'], source_status)
                if 'NAMES' in params.POWER_UNITS[unit]:
                    names = params.POWER_UNITS[unit]['NAMES']
                    for outlet in names:
                        outlet_name = '({}):'.format(outlet)
                        outlet_no = names.index(outlet) + 1
                        outlet_status = status[outlet].capitalize()
                        if outlet[0] != '_':
                            msg += '   Outlet {:<2} {: <15} [{}]\n'.format(
                                outlet_no, outlet_name, outlet_status)
            msg = msg.rstrip()
        else:
            msg = '####### POWER INFO ########\n'
            for unit in params.POWER_UNITS:
                unit_class = params.POWER_UNITS[unit]['CLASS']
                ip = params.POWER_UNITS[unit]['IP']
                if 'PORT' in params.POWER_UNITS[unit]:
                    port = params.POWER_UNITS[unit]['PORT']
                    port_str = ':{}'.format(port)
                msg += 'UNIT {} ({}{})\n'.format(unit, ip, port_str)
                status = info['status_' + unit]
                if 'UPS' in unit_class:
                    msg += 'Status: {}\n'.format(status['status'])
                    msg += 'Current load:       {}%\n'.format(status['load'])
                    msg += 'Percent remaining:  {}%\n'.format(status['percent'])
                    msg += 'Time remaining:     {}s\n'.format(status['time'])
                if 'ATS' in unit_class:
                    msg += 'Status: {}\n'.format(status['status'])
                    msg += 'Current source:     {}\n'.format(status['source'])
                    msg += 'Source A status:    {}\n'.format(status['status_A'])
                    msg += 'Source B status:    {}\n'.format(status['status_B'])
                if 'NAMES' in params.POWER_UNITS[unit]:
                    names = params.POWER_UNITS[unit]['NAMES']
                    for outlet in names:
                        outlet_name = '({}):'.format(outlet)
                        outlet_no = names.index(outlet) + 1
                        outlet_status = status[outlet].capitalize()
                        msg += 'Outlet {: <2} {: <15} {}\n'.format(
                            outlet_no, outlet_name, outlet_status)
                msg += '~~~~~~~\n'

            msg += 'Uptime: {:.1f}s\n'.format(info['uptime'])
            msg += 'Timestamp: {}\n'.format(info['timestamp'])
            msg += '###########################'
        return msg


if __name__ == '__main__':
    daemon = PowerDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
