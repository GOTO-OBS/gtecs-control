#!/usr/bin/env python

########################################################################
#                            power_daemon.py                           #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#         G-TeCS daemon to control APC power distribution unit         #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
from math import *
import time, datetime
import sys
import os
import Pyro4
import threading
from six.moves import range
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.controls import power_control
from gtecs.tecs_modules.daemons import HardwareDaemon

########################################################################
# Power daemon class

class PowerDaemon(HardwareDaemon):
    """
    Power daemon class

    Contains x functions:
    - get_info()
    - on(outletname/number/'all')
    - off(outletname/number/'all')
    """

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'power')

        ### command flags
        self.get_info_flag = 1
        self.on_flag = 0
        self.off_flag = 0
        self.reboot_flag = 0

        ### power variables
        self.info = {}

        self.power_status = {}

        self.current_unit = 'none'
        self.current_outlets = []

        self.check_status_flag = 1
        self.status_check_time = 0
        self.check_period = params.POWER_CHECK_PERIOD

        ### start control thread
        t = threading.Thread(target=self.power_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def power_control(self):
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

        while(self.running):
            self.time_check = time.time()

            # autocheck status every X seconds (if not already forced)
            delta = self.time_check - self.status_check_time
            if delta > self.check_period:
                self.check_status_flag = 1

            # check power status
            if(self.check_status_flag):
                for unit in power_units:
                    power = power_units[unit]

                    if power.unit_type == 'PDU':
                        try:
                            status = power.status()
                            self.power_status[unit] = status
                        except:
                            self.logfile.error('ERROR GETTING POWER STATUS, UNIT %s' %unit)
                            self.logfile.debug('', exc_info=True)
                            self.power_status[unit] = 'ERROR'
                    elif power.unit_type == 'UPS':
                        try:
                            status = power.status()
                            percent_remaining = power.percent_remaining()
                            time_remaining = power.time_remaining()
                            self.power_status[unit] = (status, percent_remaining, time_remaining)
                        except:
                            self.logfile.error('ERROR GETTING POWER STATUS, UNIT %s' %unit)
                            self.logfile.debug('', exc_info=True)
                            self.power_status[unit] = ('ERROR','ERROR','ERROR')

                self.status_check_time = time.time()
                self.check_status_flag = 0

            ### control functions
            # request info
            if(self.get_info_flag):
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
                        status, percent_remaining, time_remaining = self.power_status[unit]

                        info['status_'+unit] = {}
                        info['status_'+unit]['status'] = status
                        info['status_'+unit]['percent'] = percent_remaining
                        info['status_'+unit]['time'] = time_remaining

                info['uptime'] = time.time() - self.start_time
                info['ping'] = time.time() - self.time_check
                now = datetime.datetime.utcnow()
                info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")
                self.info = info
                self.get_info_flag = 0

            # power on a specified outlet
            if(self.on_flag):
                power = power_units[self.current_unit]
                for outlet in self.current_outlets:
                    self.logfile.info('Power on outlet ' + str(outlet))
                    c = power.on(outlet)
                    if c: self.logfile.info(c)
                self.current_unit = 'none'
                self.current_outlets = []
                self.on_flag = 0
                self.check_status_flag = 1

            # power off a specified outlet
            if(self.off_flag):
                power = power_units[self.current_unit]
                for outlet in self.current_outlets:
                    self.logfile.info('Power off outlet ' + str(outlet))
                    c = power.off(outlet)
                    if c: self.logfile.info(c)
                self.current_unit = 'none'
                self.current_outlets = []
                self.off_flag = 0
                self.check_status_flag = 1

            # reboot a specified outlet
            if(self.reboot_flag):
                power = power_units[self.current_unit]
                for outlet in self.current_outlets:
                    self.logfile.info('Reboot outlet ' + str(outlet))
                    c = power.reboot(outlet)
                    if c: self.logfile.info(c)
                self.current_unit = 'none'
                self.current_outlets = []
                self.reboot_flag = 0
                self.check_status_flag = 1

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Power control functions
    def get_info(self):
        """Return power status info"""
        self.check_status_flag = 1
        self.get_info_flag = 1
        time.sleep(0.5)
        return self.info

    def on(self, unit, outlet_list):
        """Power on given outlet(s)"""
        self.current_unit = unit
        self.current_outlets = self._get_valid_outlets(unit, outlet_list)
        if len(self.current_outlets) == 0:
            return 'ERROR: Unknown outlet'
        else:
            self.on_flag = 1
            return 'Turning on power'

    def off(self, unit, outlet_list):
        """Power off given outlet(s)"""
        self.current_unit = unit
        self.current_outlets = self._get_valid_outlets(unit, outlet_list)
        if len(self.current_outlets) == 0:
            return 'ERROR: Unknown outlet'
        else:
            self.off_flag = 1
            return 'Turning off power'

    def reboot(self, unit, outlet_list):
        """Reboot a given outlet(s)"""
        self.current_unit = unit
        self.current_outlets = self._get_valid_outlets(unit, outlet_list)
        if len(self.current_outlets) == 0:
            return 'ERROR: Unknown outlet'
        else:
            self.reboot_flag = 1
            return 'Rebooting power'

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Internal functions
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

########################################################################

def start():
    '''
    Create Pyro server, register the daemon and enter request loop
    '''
    host = params.DAEMONS['power']['HOST']
    port = params.DAEMONS['power']['PORT']
    pyroID = params.DAEMONS['power']['PYROID']

    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        power_daemon = PowerDaemon()
        uri = pyro_daemon.register(power_daemon, objectId=pyroID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        power_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=power_daemon.status_function)

    # Loop has closed
    power_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
