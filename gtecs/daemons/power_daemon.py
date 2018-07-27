#!/usr/bin/env python
"""
Daemon to control APC PDUs and UPSs
"""

import os
import sys
import time
import datetime
from math import *
import Pyro4
import threading

from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs.controls import power_control
from gtecs.daemons import HardwareDaemon, run


DAEMON_ID = 'power'


class PowerDaemon(HardwareDaemon):
    """Power hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        self.daemon_id = DAEMON_ID
        HardwareDaemon.__init__(self, self.daemon_id)

        ### command flags
        self.get_info_flag = 1
        self.on_flag = 0
        self.off_flag = 0
        self.reboot_flag = 0

        ### power variables
        self.info = None

        self.power_status = {}

        self.current_units = []
        self.current_outlets = []

        self.check_status_flag = 1
        self.status_check_time = 0
        self.check_period = params.POWER_CHECK_PERIOD

        self.dependency_error = 0
        self.dependency_check_time = 0

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()


    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

        # make power objects once, outside the loop
        power_units = {}
        for unit_name in params.POWER_UNITS:
            unit_class = params.POWER_UNITS[unit_name]['CLASS']
            unit_ip = params.POWER_UNITS[unit_name]['IP']
            # fake hardware
            if unit_class == 'FakePDU':
                power_units[unit_name] = power_control.FakePDU(unit_ip)
            elif unit_class == 'FakeUPS':
                power_units[unit_name] = power_control.FakeUPS(unit_ip)
            # APC hardware
            elif unit_class == 'APCPDU':
                power_units[unit_name] = power_control.APCPDU(unit_ip)
            elif unit_class == 'APCUPS':
                power_units[unit_name] = power_control.APCUPS(unit_ip)
            # Ethernet power unit
            elif unit_class == 'ETH8020':
                unit_port = int(params.POWER_UNITS[unit_name]['PORT'])
                try:
                    nc = params.POWER_UNITS[unit_name]['NC']
                except:
                    nc = 0
                power_units[unit_name] = power_control.ETH8020(unit_ip, unit_port, nc)

        while(self.running):
            self.time_check = time.time()

            ### check dependencies
            if (self.time_check - self.dependency_check_time) > 2:
                if not misc.dependencies_are_alive(self.daemon_id):
                    if not self.dependency_error:
                        self.logfile.error('Dependencies are not responding')
                        self.dependency_error = 1
                else:
                    if self.dependency_error:
                        self.logfile.info('Dependencies responding again')
                        self.dependency_error = 0
                self.dependency_check_time = time.time()

            if self.dependency_error:
                time.sleep(5)
                continue

            # autocheck status every X seconds (if not already forced)
            delta = self.time_check - self.status_check_time
            if delta > self.check_period:
                self.check_status_flag = 1

            # check power status
            if self.check_status_flag:
                try:
                    for unit in power_units:
                        power = power_units[unit]
                        if power.unit_type == 'PDU':
                            try:
                                status = power.status()
                                self.power_status[unit] = status
                            except:
                                self.logfile.error('ERROR GETTING POWER STATUS, UNIT %s' %unit)
                                self.logfile.debug('', exc_info=True)
                                names = params.POWER_UNITS[unit]['NAMES']
                                self.power_status[unit] = 'X'*len(names)
                        elif power.unit_type == 'UPS':
                            try:
                                status = power.status()
                                percent_remaining = power.percent_remaining()
                                time_remaining = power.time_remaining()
                                load = power.load()
                                outlet_status = power.outlet_status()
                                self.power_status[unit] = (status, percent_remaining, time_remaining, load, outlet_status)
                            except:
                                self.logfile.error('ERROR GETTING POWER STATUS, UNIT %s' %unit)
                                self.logfile.debug('', exc_info=True)
                                names = params.POWER_UNITS[unit]['NAMES']
                                outlet_status = 'X'*len(names)
                                self.power_status[unit] = ('ERROR','ERROR','ERROR','ERROR',outlet_status)
                    self.status_check_time = time.time()
                except:
                    self.logfile.error('check_status command failed')
                    self.logfile.debug('', exc_info=True)
                self.check_status_flag = 0

            ### control functions
            # request info
            if self.get_info_flag:
                try:
                    info = {}
                    for unit in power_units:
                        power = power_units[unit]

                        if power.unit_type == 'PDU':
                            status = self.power_status[unit]
                            names = params.POWER_UNITS[unit]['NAMES']

                            info['status_'+unit] = {}
                            for i in range(len(names)):
                                if status[i] == str(power.on_value):
                                    info['status_'+unit][names[i]] = 'On'
                                elif status[i] == str(power.off_value):
                                    info['status_'+unit][names[i]] = 'Off'
                                else:
                                    info['status_'+unit][names[i]] = 'ERROR'
                        elif power.unit_type == 'UPS':
                            status, percent_remaining, time_remaining, load, outlet_status = self.power_status[unit]

                            info['status_'+unit] = {}
                            info['status_'+unit]['status'] = status
                            info['status_'+unit]['percent'] = percent_remaining
                            info['status_'+unit]['time'] = time_remaining
                            info['status_'+unit]['load'] = load

                            names = params.POWER_UNITS[unit]['NAMES']

                            for i in range(len(names)):
                                if outlet_status[i] == str(power.on_value):
                                    info['status_'+unit][names[i]] = 'On'
                                elif outlet_status[i] == str(power.off_value):
                                    info['status_'+unit][names[i]] = 'Off'
                                else:
                                    info['status_'+unit][names[i]] = 'ERROR'

                    info['uptime'] = time.time() - self.start_time
                    info['ping'] = time.time() - self.time_check
                    now = datetime.datetime.utcnow()
                    info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

                    self.info = info
                except:
                    self.logfile.error('get_info command failed')
                    self.logfile.debug('', exc_info=True)
                self.get_info_flag = 0

            # power on a specified outlet
            if self.on_flag:
                try:
                    for unit, outlet in zip(self.current_units, self.current_outlets):
                        power = power_units[unit]
                        self.logfile.info('Power on unit {} outlet {}'.format(unit,outlet))
                        c = power.on(outlet)
                        if c: self.logfile.info(c)
                except:
                    self.logfile.error('on command failed')
                    self.logfile.debug('', exc_info=True)
                self.current_units = []
                self.current_outlets = []
                self.on_flag = 0
                self.check_status_flag = 1

            # power off a specified outlet
            if self.off_flag:
                try:
                    for unit, outlet in zip(self.current_units, self.current_outlets):
                        power = power_units[unit]
                        self.logfile.info('Power off unit {} outlet {}'.format(unit,outlet))
                        c = power.off(outlet)
                        if c: self.logfile.info(c)
                except:
                    self.logfile.error('off command failed')
                    self.logfile.debug('', exc_info=True)
                self.current_units = []
                self.current_outlets = []
                self.off_flag = 0
                self.check_status_flag = 1

            # reboot a specified outlet
            if self.reboot_flag:
                try:
                    for unit, outlet in zip(self.current_units, self.current_outlets):
                        power = power_units[unit]
                        self.logfile.info('Reboot unit {} outlet {}'.format(unit,outlet))
                        c = power.reboot(outlet)
                        if c: self.logfile.info(c)
                except:
                    self.logfile.error('reboot command failed')
                    self.logfile.debug('', exc_info=True)
                self.current_units = []
                self.current_outlets = []
                self.reboot_flag = 0
                self.check_status_flag = 1

            time.sleep(params.DAEMON_SLEEP_TIME) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return


    # Power control functions
    def get_info(self):
        """Return power status info"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set flag
        self.check_status_flag = 1
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.5)
        return self.info


    def get_info_simple(self):
        """Return plain status dict, or None"""
        try:
            info = self.get_info()
        except:
            return None
        return info


    def on(self, outlet_list, unit=''):
        """Power on given outlet(s)"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

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
        """Power off given outlet(s)"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

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
        """Reboot a given outlet(s)"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

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


    # Internal functions
    def _parse_input(self, outlet_list, unit=''):
        if unit in params.POWER_UNITS:
            # specific unit given, all outlets should be from that unit
            outlets = self._get_valid_outlets(unit, outlet_list)
            units = [unit]*len(outlets)
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
                units += [unit]*len(valid_outlets)
        return outlets, units


    def _units_from_names(self, name_list):
        unit_list = []
        for name in name_list:
            found_units = []
            for unit in params.POWER_UNITS:
                try:
                    if name in params.POWER_UNITS[unit]['NAMES'] or str(name) in ['0','all']:
                        found_units.append(unit)
                except:
                    pass # for UPSs, that don't have NAMES
            if len(found_units) > 1 and str(name) not in ['0','all']:
                raise ValueError('Duplicate names defined in params')
            for unit in found_units:
                if unit not in unit_list:
                    unit_list.append(unit)
        return unit_list


    def _get_valid_outlets(self, unit, outlet_list):
        """Check outlets are valid and convert any names to numbers"""
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


if __name__ == "__main__":
    daemon = PowerDaemon()
    run(daemon, DAEMON_ID)
