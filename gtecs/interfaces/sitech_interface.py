#!/usr/bin/env python

########################################################################
#                         sitech_interface.py                          #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#      G-TeCS PyRO wrapper for ASCOM commands to SiTech controler      #
#                  NOTE: REQUIRES ASCOM (WINDOWS ONLY)                 #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import os, sys
from math import *
import time
import Pyro4
import threading
import win32com.client
import socket
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params

########################################################################
# SiTech functions
class SiTech:
    """
    SiTech commands class

    Contains 12 functions:
    - slew_to_radec(ra,dec)
    - slew_to_target()
    - start_tracking()
    - full_stop()
    - park()
    - unpark()
    - set_target(ra=None,dec=None)
    - get_mount_status()
    - get_mount_altaz()
    - get_mount_radec()
    - get_target_radec()
    - get_lst()
    """
    def __init__(self):
        self.running = True

        ### set up logfile
        self.logfile = logger.getLogger('sitech', file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
        self.logfile.info('Daemon started')

        ### sitech variables
        self.tel = 'ASCOM.SiTechDll.Telescope'
        self.ascom = win32com.client.Dispatch(self.tel)
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount control functions
    def slew_to_radec(self,ra,dec):
        """Slew to specified coordinates (if not parked)"""
        self.ascom.Connected = True
        if not self.ascom.AtPark:
            self.logfile.info("Slewing to %.3f, %.3f" %(ra,dec))
            self.ascom.SlewToCoordinatesAsync(ra, dec)
        self.ascom.Connected = False

    def slew_to_target(self):
        """Slew to saved target coordinates (if not parked)"""
        self.ascom.Connected = True
        if not self.ascom.AtPark:
            self.logfile.info("Slewing to target")
            self.ascom.SlewToTargetAsync()
        self.ascom.Connected = False

    def start_tracking(self):
        """Set mount tracking at siderial rate"""
        self.ascom.Connected = True
        self.logfile.info("Tracking")
        self.ascom.Tracking = True
        self.ascom.Connected = False

    def full_stop(self):
        """Abort slew (if slewing) and stops tracking (if tracking)"""
        self.ascom.Connected = True
        if not self.ascom.AtPark:
            self.logfile.info("Stopping")
            self.ascom.AbortSlew()
            self.ascom.Tracking = False
        self.ascom.Connected = False

    def park(self):
        """Move mount to park position, won't move until unparked"""
        self.ascom.Connected = True
        self.logfile.info("Parking")
        self.ascom.Park()
        self.ascom.Connected = False

    def unpark(self):
        """Exit mount from park state (and starts tracking)"""
        self.ascom.Connected = True
        self.logfile.info("Unparking")
        self.ascom.Unpark()
        self.ascom.Connected = False

    def set_target(self,ra='unset',dec='unset'):
        """Set target data, can do each seperatly"""
        self.ascom.Connected = True
        if ra != 'unset':
            self.logfile.info("Setting Target RA to %.3f" %ra)
            self.ascom.TargetRightAscension = ra
        if dec != 'unset':
            self.logfile.info("Setting Target Dec to %.3f" %dec)
            self.ascom.TargetDeclination = dec
        self.ascom.Connected = False

    def set_target_ra(self,ra):
        """Set target RA"""
        self.ascom.Connected = True
        self.ascom.TargetRightAscension = ra
        self.logfile.info("Setting Target RA to %.3f" %ra)
        self.ascom.Connected = False

    def set_target_dec(self,dec):
        """Set target Dec"""
        self.ascom.Connected = True
        self.ascom.TargetDeclination = dec
        self.logfile.info("Setting Target Dec to %.3f" %dec)
        self.ascom.Connected = False

    def get_mount_status(self):
        """Return current mount status"""
        self.ascom.Connected = True
        if self.ascom.Slewing:
            status = 'Slewing'
        elif self.ascom.Tracking:
            status = 'Tracking'
        elif self.ascom.AtPark:
            status = 'Parked'
        else:
            status = 'Stopped'
        self.ascom.Connected = False
        return status

    def get_mount_altaz(self):
        """Return the current mount position"""
        self.ascom.Connected = True
        alt = self.ascom.Altitude
        az = self.ascom.Azimuth
        self.ascom.Connected = False
        return (alt,az)

    def get_mount_radec(self):
        """Return the current mount sky position"""
        self.ascom.Connected = True
        ra = self.ascom.RightAscension
        dec = self.ascom.Declination
        self.ascom.Connected = False
        return (ra,dec)

    def get_target_radec(self):
        """Return the current target's position (if one is set)"""
        self.ascom.Connected = True
        # Need to catch error if target not yet set
        try:
            ra = self.ascom.TargetRightAscension
        except:
            ra = None
        try:
            dec = self.ascom.TargetDeclination
        except:
            dec = None
        self.ascom.Connected = False
        return (ra,dec)

    def get_target_distance(self):
        """Return the distance to the current target"""
        self.ascom.Connected = True
        # Need to catch error if target not yet set
        try:
            t_ra = self.ascom.TargetRightAscension
        except:
            self.ascom.Connected = False
            return None
        try:
            t_dec = self.ascom.TargetDeclination
        except:
            self.ascom.Connected = False
            return None
        m_ra = self.ascom.RightAscension
        m_dec = self.ascom.Declination
        t_alt = 90 - t_dec
        m_alt = 90 - m_dec
        D_ra = (t_ra - m_ra)*360./24.
        S1 = cos(radians(t_alt))*cos(radians(m_alt))
        S2 = sin(radians(t_alt))*sin(radians(m_alt))*cos(radians(D_ra))
        S = degrees(acos(S1+S2))
        self.ascom.Connected = False
        return S

    def get_lst(self):
        """Return the current siderial time"""
        self.ascom.Connected = True
        lst = self.ascom.SiderealTime
        self.ascom.Connected = False
        return lst

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        return 'ping'

    def prod(self):
        return

    def status_function(self):
        return self.running

    def shutdown(self):
        self.running = False

def start():
    ########################################################################
    # Create Pyro control server
    pyro_daemon = Pyro4.Daemon(host=socket.gethostname(), port=9000)
    sitech_daemon = SiTech()

    uri = pyro_daemon.register(sitech_daemon,'sitech_interface')
    sitech_daemon.logfile.info('Starting SiTech interface daemon at',uri)

    Pyro4.config.COMMTIMEOUT = 5.
    pyro_daemon.requestLoop(loopCondition=sitech_daemon.status_function)

    sitech_daemon.logfile.info('Exiting SiTech interface daemon')
    time.sleep(1.)

if __name__ == "__main__":
    start()
