#!/usr/bin/env python

########################################################################
#                            mnt_daemon.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#              G-TeCS daemon to access SiTech mount control            #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
from math import sin, cos, acos, pi, radians, degrees
import sys
import Pyro4
import threading
import time
from astropy.time import Time
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.controls import mnt_control
from gtecs.tecs_modules.astronomy import find_ha, check_alt_limit
from gtecs.tecs_modules.daemons import HardwareDaemon

########################################################################
# Mount daemon class

class MntDaemon(HardwareDaemon):
    """
    Mount daemon class

    Contains x functions:
    - get_info()
    - slew_to_radec(ra,dec)
    - slew_to_target()
    - start_tracking()
    - full_stop()
    - park()
    - unpark()
    - set_target_ra(ra)
    - set_target_dec(dec)
    - set_target(dec)
    - offset(direction)
    - set_step()
    """

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'mnt')

        ### command flags
        self.get_info_flag = 1
        self.slew_radec_flag = 0
        self.slew_target_flag = 0
        self.start_tracking_flag = 0
        self.full_stop_flag = 0
        self.set_blinky_mode_flag = 0
        self.park_flag = 0
        self.unpark_flag = 0
        self.set_target_ra_flag = 0
        self.set_target_dec_flag = 0
        self.set_target_flag = 0

        ### mount variables
        self.info = {}
        self.step = params.DEFAULT_OFFSET_STEP
        self.mount_status = 'Unknown'
        self.target_ra = None
        self.target_dec = None
        self.temp_ra = None
        self.temp_dec = None
        self.utc = Time.now()
        self.utc.precision = 0  # only integer seconds
        self.utc_str = self.utc.iso
        self.set_blinky = False

        ### start control thread
        t = threading.Thread(target=self.mnt_control)
        t.daemon = True
        t.start()

        # start ra check thread
        if params.FREEZE_DEC:
            t2 = threading.Thread(target=self._ra_check_thread)
            t2.daemon = True
            t2.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def mnt_control(self):
        self.logfile.info('Daemon control thread started')

        ### connect to SiTechExe
        IP_address = params.SITECH_HOST
        port = params.SITECH_PORT
        self.sitech = mnt_control.SiTech(IP_address, port)

        while(self.running):
            self.time_check = time.time()

            ### control functions
            # request info
            if(self.get_info_flag):
                # save info
                info = {}
                self.mount_status = self.sitech.status
                info['status'] = self.mount_status
                info['mount_alt'] = self.sitech.alt
                info['mount_az'] = self.sitech.az
                info['mount_ra'] = self.sitech.ra
                info['mount_dec'] = self.sitech.dec
                info['target_ra'] = self.target_ra
                info['target_dec'] = self.target_dec
                info['target_dist'] = self._get_target_distance()
                info['lst'] = self.sitech.sidereal_time
                info['ha'] = find_ha(info['mount_ra'], info['lst'])

                self.utc = Time.now()
                self.utc.precision = 0  # only integer seconds
                info['utc'] = self.utc.iso
                info['step'] = self.step
                info['uptime'] = time.time()-self.start_time
                info['ping'] = time.time()-self.time_check
                now = Time.now()
                now.precision = 0
                info['timestamp'] = now.iso
                self.info = info
                self.get_info_flag = 0

            # slew to given coordinates
            if(self.slew_radec_flag):
                self.logfile.info('Slewing to %.2f,%.2f',
                                  self.temp_ra, self.temp_dec)
                c = self.sitech.slew_to_radec(self.temp_ra, self.temp_dec)
                if c: self.logfile.info(c)
                self.slew_radec_flag = 0
                self.temp_ra = None
                self.temp_dec = None

            # slew to target
            if(self.slew_target_flag):
                self.logfile.info('Slewing to target')
                c = self.sitech.slew_to_radec(self.target_ra, self.target_dec)
                if c: self.logfile.info(c)
                self.slew_target_flag = 0

            # start tracking
            if(self.start_tracking_flag):
                c = self.sitech.track()
                if c: self.logfile.info(c)
                self.start_tracking_flag = 0

            # stop all motion (tracking or slewing)
            if(self.full_stop_flag):
                c = self.sitech.halt()
                if c: self.logfile.info(c)
                self.full_stop_flag = 0

            # turn blinky mode on or off
            if(self.set_blinky_mode_flag):
                c = self.sitech.set_blinky_mode(self.set_blinky)
                if c: self.logfile.info(c)
                self.set_blinky = False
                self.set_blinky_mode_flag = 0

            # park the mount
            if(self.park_flag):
                c = self.sitech.park()
                if c: self.logfile.info(c)
                self.park_flag = 0

            # unpark the mount
            if(self.unpark_flag):
                c = self.sitech.unpark()
                if c: self.logfile.info(c)
                self.unpark_flag = 0

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount control functions
    def get_info(self):
        """Return mount status info"""
        self.get_info_flag = 1
        time.sleep(0.5)
        return self.info

    def slew_to_radec(self,ra,dec):
        """Slew to specified coordinates"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            return 'ERROR: Already slewing'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked, need to unpark before slewing'
        elif self.mount_status == 'IN BLINKY MODE':
            return 'ERROR: Mount is in blinky mode, motors disabled'
        elif check_alt_limit(ra*360./24., dec, self.utc):
            return 'ERROR: Target too low, cannot slew'
        else:
            self.temp_ra = ra
            self.temp_dec = dec
            self.slew_radec_flag = 1
            return 'Slewing to coordinates'

    def slew_to_target(self):
        """Slew to current set target"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            return 'ERROR: Already slewing'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked, need to unpark before slewing'
        elif self.mount_status == 'IN BLINKY MODE':
            return 'ERROR: Mount is in blinky mode, motors disabled'
        elif self.target_ra == None or self.target_dec == None:
            return 'ERROR: Target not set'
        elif check_alt_limit(self.target_ra*360./24., self.target_dec, self.utc):
            return 'ERROR: Target too low, cannot slew'
        else:
            self.slew_target_flag = 1
            return 'Slewing to target'

    def start_tracking(self):
        """Starts mount tracking"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Tracking':
            return 'ERROR: Already tracking'
        elif self.mount_status == 'Slewing':
            return 'ERROR: Currently slewing, will track when reached target'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked'
        elif self.mount_status == 'IN BLINKY MODE':
            return 'ERROR: Mount is in blinky mode, motors disabled'
        else:
            self.start_tracking_flag = 1
            return 'Started tracking'

    def full_stop(self):
        """Stops mount moving (slewing or tracking)"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Stopped':
            return 'ERROR: Already stopped'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked'
        else:
            self.full_stop_flag = 1
            return 'Stopping mount'

    def blinky(self, activate):
        """Turn on or off blinky mode"""
        if activate and self.sitech.blinky:
            return 'ERROR: Already in blinky mode'
        elif not activate and not self.sitech.blinky:
            return 'ERROR: Already not in blinky mode'
        else:
            self.set_blinky = activate
            self.set_blinky_mode_flag = 1
            if activate:
                return 'Turning on blinky mode'
            else:
                return 'Turning off blinky mode'

    def park(self):
        """Moves the mount to the park position"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Parked':
            return 'ERROR: Already parked'
        elif self.mount_status == 'IN BLINKY MODE':
            return 'ERROR: Mount is in Blinky Mode, motors disabled'
        else:
            self.park_flag = 1
            return 'Parking mount'

    def unpark(self):
        """Unpark the mount"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status != 'Parked':
            return 'ERROR: Mount is not parked'
        else:
            self.unpark_flag = 1
            return 'Unparking mount'

    def set_target_ra(self,ra):
        """Set the target RA"""
        self.target_ra = ra
        self.logfile.info('set ra to %.4f', self.target_ra)
        return """Setting target RA"""

    def set_target_dec(self,dec):
        """Set the target Dec"""
        self.target_dec = dec
        self.logfile.info('set dec to %.4f', self.target_dec)
        return """Setting target Dec"""

    def set_target(self,ra,dec):
        """Set the target location"""
        self.target_ra = ra
        self.logfile.info('set ra to %.4f', self.target_ra)
        self.target_dec = dec
        self.logfile.info('set dec to %.4f', self.target_dec)
        return """Setting target"""

    def offset(self,direction):
        """Offset in a specified (cardinal) direction"""
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            return 'ERROR: Already slewing'
        elif self.mount_status == 'Parked':
            return 'ERROR: Mount is parked'
        elif self.mount_status == 'IN BLINKY MODE':
            return 'ERROR: Mount is in Blinky Mode, motors disabled'
        elif direction not in ['north','south','east','west']:
            return 'ERROR: Invalid direction'
        else:
            step_deg = self.step/3600.
            step_ra = (step_deg*24./360.)/cos(self.mount_dec*pi/180.)
            step_dec = step_deg
            if direction == 'north':
                ra = self.sitech.ra
                dec = self.sitech.dec + step_dec
            elif direction == 'south':
                ra = self.sitech.ra
                dec = self.sitech.dec - step_dec
            elif direction == 'east':
                ra = self.sitech.ra + step_ra
                dec = self.sitech.dec
            elif direction == 'west':
                ra = self.sitech.ra - step_ra
                dec = self.sitech.dec
            if check_alt_limit(ra*360./24.,dec,self.utc):
                return 'ERROR: Target too low, cannot slew'
            else:
                self.temp_ra = ra
                self.temp_dec = dec
                self.slew_radec_flag = 1
                return 'Slewing to offset coordinates'

    def set_step(self,offset):
        self.step = offset
        return 'New offset step set'

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Internal functions
    def _get_target_distance(self):
        """Return the distance to the current target"""
        # Need to catch error if target not yet set
        if self.target_ra == None or self.target_dec == None:
            return None
        if not params.FREEZE_DEC:
            t_ra = self.target_ra
            t_dec = self.target_dec
            m_ra = self.sitech.ra
            m_dec = self.sitech.dec
            t_alt = 90 - t_dec
            m_alt = 90 - m_dec
            D_ra = (t_ra - m_ra)*360./24.
            S1 = cos(radians(t_alt))*cos(radians(m_alt))
            S2 = sin(radians(t_alt))*sin(radians(m_alt))*cos(radians(D_ra))
            S = degrees(acos(S1+S2))
            return S
        else:
            t_ra = self.target_ra
            m_ra = self.sitech.ra
            D_ra = (t_ra - m_ra)*360./24.
            return abs(D_ra)

    def _ra_check_thread(self):
        '''A thread to check the ra distance and cancel slewing when it's
        reached the target.

        Required for when the FREEZE_DEC is set, so the mount doesn't keep
        trying to slew to the dec target.

        If activated it will check the telescope when slewing, and if it's
        reached the RA target then stop the slewing and start tracking.
        '''

        ### connect to SiTechExe
        IP_address = params.SITECH_HOST
        port = params.SITECH_PORT
        self.sitech = mnt_control.SiTech(IP_address, port)

        ra_distance = 0
        while True:
            if self.sitech.slewing:
                sleep_time = 0.1
                ra_distance_new = self._get_target_distance()
                print(ra_distance_new, abs(ra_distance_new - ra_distance))
                if ra_distance_new < 0.001 and abs(ra_distance_new - ra_distance) < 0.0001:
                    self.logfile.info('Reached RA target, stopping slew')
                    self.sitech.halt()
                    time.sleep(0.1)
                    self.sitech.track()
                    ra_distance = 0
                else:
                    ra_distance = ra_distance_new
                time.sleep(0.1)
            else:
                time.sleep(1)

########################################################################

def start():
    '''
    Create Pyro server, register the daemon and enter request loop
    '''
    host = params.DAEMONS['mnt']['HOST']
    port = params.DAEMONS['mnt']['PORT']
    pyroID = params.DAEMONS['mnt']['PYROID']

    # Check the daemon isn't already running
    if not misc.there_can_only_be_one('mnt'):
        sys.exit()

    # Start the daemon
    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        mnt_daemon = MntDaemon()
        uri = pyro_daemon.register(mnt_daemon, objectId=pyroID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        mnt_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=mnt_daemon.status_function)

    # Loop has closed
    mnt_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
