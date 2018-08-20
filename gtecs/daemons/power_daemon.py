#!/usr/bin/env python
"""Daemon to control APC PDUs and UPSs."""

import threading
import time

from astropy.time import Time

from gtecs import misc
from gtecs import params
from gtecs.daemons import HardwareDaemon
from gtecs.hardware.power import APCPDU, APCUPS, ETH8020
from gtecs.hardware.power import FakePDU, FakeUPS


class PowerDaemon(HardwareDaemon):
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
        self.log.info('Daemon control thread started')

        while(self.running):
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time
                self.force_check_flag = False

                # Try to connect to the hardware
                self._connect()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's sucsessful
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
                        power = self.power_units[unit]
                        self.log.info('Power on unit {} outlet {}'.format(unit, outlet))
                        c = power.on(outlet)
                        if c:
                            self.log.info(c)
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
                        power = self.power_units[unit]
                        self.log.info('Power off unit {} outlet {}'.format(unit, outlet))
                        c = power.off(outlet)
                        if c:
                            self.log.info(c)
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
                        power = self.power_units[unit]
                        self.log.info('Reboot unit {} outlet {}'.format(unit, outlet))
                        c = power.reboot(outlet)
                        if c:
                            self.log.info(c)
                except Exception:
                    self.log.error('reboot command failed')
                    self.log.debug('', exc_info=True)
                self.current_units = []
                self.current_outlets = []
                self.reboot_flag = 0
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _connect(self):
        """Connect to hardware."""
        for unit_name in params.POWER_UNITS:
            # Connect to each unit
            if not self.power_units[unit_name]:
                unit_params = params.POWER_UNITS[unit_name].copy()
                unit_class = unit_params['CLASS']
                unit_ip = unit_params['IP']

                # create power object by class
                if unit_class == 'FakePDU':
                    self.power_units[unit_name] = FakePDU(unit_ip)
                    self.log.info('Connected to {}'.format(unit_name))

                elif unit_class == 'FakeUPS':
                    self.power_units[unit_name] = FakeUPS(unit_ip)
                    self.log.info('Connected to {}'.format(unit_name))

                elif unit_class == 'APCPDU':
                    try:
                        self.power_units[unit_name] = APCPDU(unit_ip)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        self.log.error('Failed to connect to {}'.format(unit_name))
                        if unit_name not in self.bad_hardware:
                            self.bad_hardware.add(unit_name)

                elif unit_class == 'APCUPS':
                    try:
                        self.power_units[unit_name] = APCUPS(unit_ip)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        self.log.error('Failed to connect to {}'.format(unit_name))
                        if unit_name not in self.bad_hardware:
                            self.bad_hardware.add(unit_name)

                elif unit_class == 'ETH8020':
                    try:
                        unit_port = int(unit_params['PORT'])
                        unit_nc = unit_params['NC'] if 'NC' in unit_params else 0
                        self.power_units[unit_name] = ETH8020(unit_ip, unit_port, unit_nc)
                        self.log.info('Connected to {}'.format(unit_name))
                        if unit_name in self.bad_hardware:
                            self.bad_hardware.remove(unit_name)
                    except Exception:
                        self.power_units[unit_name] = None
                        self.log.error('Failed to connect to {}'.format(unit_name))
                        if unit_name not in self.bad_hardware:
                            self.bad_hardware.add(unit_name)

        if len(self.bad_hardware) > 0 and not self.hardware_error:
            self.log.warning('Hardware error detected')
            self.hardware_error = True
        elif len(self.bad_hardware) == 0 and self.hardware_error:
            self.log.warning('Hardware error cleared')
            self.hardware_error = False

        # Finally check if we need to report an error
        self._check_errors()

    def _get_info(self):
        """Get the latest status info from the heardware."""
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
                if power.unit_type == 'PDU':
                    outlet_statuses = power.status()
                elif power.unit_type == 'UPS':
                    outlet_statuses = power.outlet_status()
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
                temp_info['status_' + unit_name] = temp_status
            except Exception:
                self.log.error('Failed to get {} info'.format(unit_name))
                self.log.debug('', exc_info=True)
                temp_status[unit_name] = None
                # Report the connection as failed
                self.power_units[unit_name] = None
                if unit_name not in self.bad_hardware:
                    self.bad_hardware.add(unit_name)

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    def _parse_input(self, outlet_list, unit=''):
        if unit in params.POWER_UNITS:
            # specific unit given, all outlets should be from that unit
            outlets = self._get_valid_outlets(unit, outlet_list)
            units = [unit] * len(outlets)
        else:
            # first check for group names
            for outlet in outlet_list.copy():
                if outlet in params.POWER_GROUPS:
                    outlet_list.remove(outlet)
                    outlet_list += params.POWER_GROUPS[outlet]

            # remove duplicate outlets
            outlet_list = list(set(outlet_list))

            # a list of outlet names, we need to find their matching units
            unit_list = self._units_from_names(outlet_list)
            units = []
            outlets = []
            for unit in unit_list:
                valid_outlets = self._get_valid_outlets(unit, outlet_list)
                outlets += valid_outlets
                units += [unit] * len(valid_outlets)
        return outlets, units

    def _units_from_names(self, name_list):
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
        return unit_list

    def _get_valid_outlets(self, unit, outlet_list):
        """Check outlets are valid and convert any names to numbers."""
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
        return valid_list

    # Control functions
    def on(self, outlet_list, unit=''):
        """Power on given outlet(s)."""
        # Check input
        outlets, units = self._parse_input(outlet_list, unit)
        if len(outlets) == 0:
            raise ValueError('No valid outlets or groups')

        # Set values
        self.current_outlets = outlets
        self.current_units = units

        # Set flag
        self.on_flag = 1

        return 'Turning on power'

    def off(self, outlet_list, unit=''):
        """Power off given outlet(s)."""
        # Check input
        outlets, units = self._parse_input(outlet_list, unit)
        if len(outlets) == 0:
            raise ValueError('No valid outlets or groups')

        # Set values
        self.current_outlets = outlets
        self.current_units = units

        # Set flag
        self.off_flag = 1

        return 'Turning off power'

    def reboot(self, outlet_list, unit=''):
        """Reboot given outlet(s)."""
        # Check input
        outlets, units = self._parse_input(outlet_list, unit)
        if len(outlets) == 0:
            raise ValueError('No valid outlets or groups')

        # Set values
        self.current_outlets = outlets
        self.current_units = units

        # Set flag
        self.reboot_flag = 1
        return 'Rebooting power'


if __name__ == "__main__":
    daemon_id = 'power'
    with misc.make_pid_file(daemon_id):
        PowerDaemon()._run()
