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
        self.power_list = params.POWER_LIST
        self.power_status = 'None yet'
        self.outlet_list = []
        self.status_flag = 1

        ### start control thread
        t = threading.Thread(target=self.power_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def power_control(self):
        self.logfile.info('Daemon control thread started')

        ### connect to power object
        IP = params.POWER_IP
        port = params.POWER_PORT
        if params.POWER_TYPE == 'APCPower':
            power = power_control.APCPower(IP)
        elif params.POWER_TYPE == 'EthPower':
            power = power_control.EthPower(IP, port)
        else:
            power = power_control.FakePower()

        while(self.running):
            self.time_check = time.time()

            # check power status every 30 seconds
            delta = self.time_check - self.start_time
            if (delta % 30 < 1 and self.status_flag == 1) or self.status_flag == -1:
                try:
                    cmd = params.POWER_CHECK_SCRIPT
                    power_status = misc.cmd_timeout(cmd, timeout=10.)
                    assert isinstance(power_status, str) or isinstance(power_status, unicode)
                    assert len(power_status) == power.count
                    self.power_status = power_status
                except:
                    self.logfile.error('ERROR GETTING POWER STATUS')
                    self.logfile.debug('', exc_info=True)
                    self.power_status = 'x' * power.count
                misc.kill_processes(params.POWER_CHECK_SCRIPT,params.DAEMONS['power']['HOST'])
                self.status_flag = 0
            if delta % 30 > 1:
                self.status_flag = 1

            ### control functions
            # request info
            if(self.get_info_flag):
                info = {}
                info['status'] = self.power_status
                info['status_dict'] = {}
                power_status = str(self.power_status)
                for i in range(len(params.POWER_LIST)):
                    if power_status[i] == '1':
                        info['status_dict'][params.POWER_LIST[i]] = 'On'
                    elif power_status[i] == str(power.off_value):
                        info['status_dict'][params.POWER_LIST[i]] = 'Off'
                    else:
                        info['status_dict'][params.POWER_LIST[i]] = 'ERROR!'
                info['uptime'] = time.time() - self.start_time
                info['ping'] = time.time() - self.time_check
                now = datetime.datetime.utcnow()
                info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")
                self.info = info
                self.get_info_flag = 0

            # power on a specified outlet
            if(self.on_flag):
                for outlet in self.outlet_list:
                    self.logfile.info('Power on outlet ' + str(outlet))
                    c = power.on(outlet)
                    if c: self.logfile.info(c)
                self.outlet_list = []
                self.on_flag = 0
                self.status_flag = -1

            # power off a specified outlet
            if(self.off_flag):
                for outlet in self.outlet_list:
                    self.logfile.info('Power off outlet ' + str(outlet))
                    c = power.off(outlet)
                    if c: self.logfile.info(c)
                self.outlet_list = []
                self.off_flag = 0
                self.status_flag = -1

            # reboot a specified outlet
            if(self.reboot_flag):
                for outlet in self.outlet_list:
                    self.logfile.info('Reboot outlet ' + str(outlet))
                    c = power.reboot(outlet)
                    if c: self.logfile.info(c)
                self.outlet_list = []
                self.reboot_flag = 0
                self.status_flag = -1

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Power control functions
    def get_info(self):
        """Return power status info"""
        self.status_flag = -1
        self.get_info_flag = 1
        time.sleep(0.5)
        return self.info

    def on(self, outlet_list):
        """Power on given outlet(s)"""
        self.outlet_list = self._get_valid_outlets(outlet_list)
        if len(self.outlet_list) == 0:
            return 'ERROR: Unknown outlet'
        else:
            self.on_flag = 1
            return 'Turning on power'

    def off(self, outlet_list):
        """Power off given outlet(s)"""
        self.outlet_list = self._get_valid_outlets(outlet_list)
        if len(self.outlet_list) == 0:
            return 'ERROR: Unknown outlet'
        else:
            self.off_flag = 1
            return 'Turning off power'

    def reboot(self, outlet_list):
        """Reboot a given outlet(s)"""
        self.outlet_list = self._get_valid_outlets(outlet_list)
        if len(self.outlet_list) == 0:
            return 'ERROR: Unknown outlet'
        else:
            self.reboot_flag = 1
            return 'Rebooting power'

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Internal functions
    def _get_valid_outlets(self, outlet_list):
        """Check outlets are valid and convert any names to numbers"""
        valid_list = []
        for outlet in outlet_list:
            if outlet == 'all':
                valid_list = [0]
            elif outlet.isdigit():
                x = int(outlet)
                if 0 <= x < (len(params.POWER_LIST) + 1):
                    valid_list.append(x)
            elif outlet in params.POWER_LIST:
                valid_list.append(params.POWER_LIST.index(outlet) + 1)
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
