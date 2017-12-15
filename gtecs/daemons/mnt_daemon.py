#!/usr/bin/env python
"""
Daemon to access SiTech mount control
"""

import sys
import time
from math import sin, cos, acos, pi, radians, degrees
import Pyro4
import threading

import astropy.units as u
from astropy.time import Time
from astropy.coordinates import SkyCoord

from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs.controls import mnt_control
from gtecs.astronomy import find_ha, check_alt_limit, radec_from_altaz
from gtecs.daemons import HardwareDaemon


DAEMON_ID = 'mnt'
DAEMON_HOST = params.DAEMONS[DAEMON_ID]['HOST']
DAEMON_PORT = params.DAEMONS[DAEMON_ID]['PORT']


class MntDaemon(HardwareDaemon):
    """Mount hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        self.daemon_id = DAEMON_ID
        HardwareDaemon.__init__(self, self.daemon_id)

        ### command flags
        self.get_info_flag = 1
        self.slew_radec_flag = 0
        self.slew_target_flag = 0
        self.slew_altaz_flag = 0
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
        self.temp_alt = None
        self.temp_az = None
        self.utc = Time.now()
        self.utc.precision = 0  # only integer seconds
        self.utc_str = self.utc.iso
        self.set_blinky = False

        self.dependency_error = 0
        self.dependency_check_time = 0

        ### connect to SiTechExe
        # Once, and we'll see if both threads can use it
        IP_address = params.SITECH_HOST
        port = params.SITECH_PORT
        self.sitech = mnt_control.SiTech(IP_address, port)

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

        # start ra check thread
        if params.FREEZE_DEC:
            t2 = threading.Thread(target=self._ra_check_thread)
            t2.daemon = True
            t2.start()


    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

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

            ### control functions
            # request info
            if self.get_info_flag:
                try:
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
                except:
                    self.logfile.error('get_info command failed')
                    self.logfile.debug('', exc_info=True)
                self.get_info_flag = 0

            # slew to given coordinates
            if self.slew_radec_flag:
                try:
                    self.logfile.info('Slewing to ra %.2f, dec%.2f',
                                      self.temp_ra, self.temp_dec)
                    c = self.sitech.slew_to_radec(self.temp_ra, self.temp_dec)
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('slew_radec command failed')
                    self.logfile.debug('', exc_info=True)
                self.temp_ra = None
                self.temp_dec = None
                self.slew_radec_flag = 0

            # slew to target
            if self.slew_target_flag:
                try:
                    self.logfile.info('Slewing to target')
                    c = self.sitech.slew_to_radec(self.target_ra, self.target_dec)
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('slew_target command failed')
                    self.logfile.debug('', exc_info=True)
                self.slew_target_flag = 0

            # slew to given alt/az
            if self.slew_altaz_flag:
                try:
                    self.logfile.info('Slewing to alt %.2f, az %.2f',
                                      self.temp_alt, self.temp_az)
                    c = self.sitech.slew_to_altaz(self.temp_alt, self.temp_az)
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('slew_altaz command failed')
                    self.logfile.debug('', exc_info=True)
                self.temp_alt = None
                self.temp_az = None
                self.slew_altaz_flag = 0

            # start tracking
            if self.start_tracking_flag:
                try:
                    c = self.sitech.track()
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('start_tracking command failed')
                    self.logfile.debug('', exc_info=True)
                self.start_tracking_flag = 0

            # stop all motion (tracking or slewing)
            if self.full_stop_flag:
                try:
                    c = self.sitech.halt()
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('full_stop command failed')
                    self.logfile.debug('', exc_info=True)
                self.full_stop_flag = 0

            # turn blinky mode on or off
            if self.set_blinky_mode_flag:
                try:
                    c = self.sitech.set_blinky_mode(self.set_blinky)
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('set_blinky_mode command failed')
                    self.logfile.debug('', exc_info=True)
                self.set_blinky = False
                self.set_blinky_mode_flag = 0

            # park the mount
            if self.park_flag:
                try:
                    c = self.sitech.park()
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('park command failed')
                    self.logfile.debug('', exc_info=True)
                self.park_flag = 0

            # unpark the mount
            if self.unpark_flag:
                try:
                    c = self.sitech.unpark()
                    if c: self.logfile.info(c)
                except:
                    self.logfile.error('unpark command failed')
                    self.logfile.debug('', exc_info=True)
                self.unpark_flag = 0

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return


    # Mount control functions
    def get_info(self):
        """Return mount status info"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set flag
        self.get_info_flag = 1

        # Wait, then return the updated info dict
        time.sleep(0.5)
        return self.info


    def slew_to_radec(self, ra, dec):
        """Slew to specified coordinates"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if not (0 <= ra < 24):
            raise ValueError('RA in hours must be between 0 and 24')
        if not (-90 <= dec <= 90):
            raise ValueError('Dec in degrees must be between -90 and +90')
        if check_alt_limit(ra*360./24., dec, Time.now()):
            raise misc.HorizonError('Target too low, cannot slew')

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            raise misc.HardwareStatusError('Already slewing')
        elif self.mount_status == 'Parked':
            raise misc.HardwareStatusError('Mount is parked, need to unpark before slewing')
        elif self.mount_status == 'IN BLINKY MODE':
            raise misc.HardwareStatusError('Mount is in blinky mode, motors disabled')

        # Set values
        self.temp_ra = ra
        self.temp_dec = dec

        # Set flag
        self.slew_radec_flag = 1

        return 'Slewing to coordinates'


    def slew_to_target(self):
        """Slew to current set target"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if self.target_ra == None or self.target_dec == None:
            raise misc.HardwareStatusError('Target not set')
        if check_alt_limit(self.target_ra*360./24., self.target_dec, Time.now()):
            raise misc.HorizonError('Target too low, cannot slew')

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            raise misc.HardwareStatusError('Already slewing')
        elif self.mount_status == 'Parked':
            raise misc.HardwareStatusError('Mount is parked, need to unpark before slewing')
        elif self.mount_status == 'IN BLINKY MODE':
            raise misc.HardwareStatusError('Mount is in blinky mode, motors disabled')

        # Set flag
        self.slew_target_flag = 1

        return 'Slewing to target'


    def slew_to_altaz(self, alt, az):
        """Slew to specified alt/az"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if not (0 <= alt < 90):
            raise ValueError('Alt in degrees must be between 0 and 90')
        if not (0 <= az < 360):
            raise ValueError('Az in degrees must be between 0 and 360')
        if alt < params.MIN_ELEVATION:
            raise misc.HorizonError('Target too low, cannot slew')

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            raise misc.HardwareStatusError('Already slewing')
        elif self.mount_status == 'Parked':
            raise misc.HardwareStatusError('Mount is parked, need to unpark before slewing')
        elif self.mount_status == 'IN BLINKY MODE':
            raise misc.HardwareStatusError('Mount is in blinky mode, motors disabled')

        # Set values
        self.temp_alt = alt
        self.temp_az = az
        ra, dec = radec_from_altaz(alt, az, Time.now())
        self.target_ra = ra*24/360.
        self.target_dec = dec

        # Set flag
        self.slew_altaz_flag = 1

        return 'Slewing to alt/az'


    def start_tracking(self):
        """Starts mount tracking"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Tracking':
            raise misc.HardwareStatusError('Already tracking')
        elif self.mount_status == 'Slewing':
            raise misc.HardwareStatusError('Currently slewing, will track when reached target')
        elif self.mount_status == 'Parked':
            raise misc.HardwareStatusError('Mount is parked')
        elif self.mount_status == 'IN BLINKY MODE':
            raise misc.HardwareStatusError('Mount is in blinky mode, motors disabled')
        if check_alt_limit(self.info['mount_ra']*360./24., self.info['mount_dec'], Time.now()):
            raise misc.HardwareStatusError('Mount is currently below horizon, cannot track')

        # Set flag
        self.start_tracking_flag = 1

        return 'Started tracking'


    def full_stop(self):
        """Stops mount moving (slewing or tracking)"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Stopped':
            raise misc.HardwareStatusError('Already stopped')
        elif self.mount_status == 'Parked':
            raise misc.HardwareStatusError('Mount is parked')

        # Set flag
        self.full_stop_flag = 1

        return 'Stopping mount'


    def blinky(self, activate):
        """Turn on or off blinky mode"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check current status
        if activate and self.sitech.blinky:
            raise misc.HardwareStatusError('Already in blinky mode')
        elif not activate and not self.sitech.blinky:
            raise misc.HardwareStatusError('Already not in blinky mode')

        # Set values
        self.set_blinky = activate

        # Set flag
        self.set_blinky_mode_flag = 1

        if activate:
            s = 'Turning on blinky mode'
        else:
            s = 'Turning off blinky mode'
        return s


    def park(self):
        """Moves the mount to the park position"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Parked':
            raise misc.HardwareStatusError('Already parked')
        elif self.mount_status == 'IN BLINKY MODE':
            raise misc.HardwareStatusError('Mount is in Blinky Mode, motors disabled')

        # Set flag
        self.park_flag = 1

        return 'Parking mount'


    def unpark(self):
        """Unpark the mount"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status != 'Parked':
            raise misc.HardwareStatusError('Mount is not parked')

        # First turn off blinky mode
        self.set_blinky = False
        self.set_blinky_mode_flag = 1
        time.sleep(0.2)

        # Set flag
        self.unpark_flag = 1

        return 'Unparking mount'


    def set_target_ra(self, ra):
        """Set the target RA"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if not (0 <= ra < 24):
            raise ValueError('RA in hours must be between 0 and 24')

        # Set values
        self.target_ra = ra

        self.logfile.info('Set target RA to %.4f', ra)
        return 'Setting target RA'


    def set_target_dec(self, dec):
        """Set the target Dec"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if not (-90 <= dec <= 90):
            raise ValueError('Dec in degrees must be between -90 and +90')

        # Set values
        self.target_dec = dec

        self.logfile.info('Set target Dec to %.4f', dec)
        return 'Setting target Dec'


    def set_target(self, ra, dec):
        """Set the target location"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if not (0 <= ra < 24):
            raise ValueError('RA in hours must be between 0 and 24')
        if not (-90 <= dec <= 90):
            raise ValueError('Dec in degrees must be between -90 and +90')

        # Set values
        self.target_ra = ra
        self.target_dec = dec

        self.logfile.info('Set target RA to %.4f', ra)
        self.logfile.info('Set target Dec to %.4f', dec)
        return 'Setting target'


    def offset(self, direction):
        """Offset in a specified (cardinal) direction"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if direction.lower() not in ['north', 'south', 'east', 'west']:
            raise ValueError('Invalid direction')

        # Check current status
        self.get_info_flag = 1
        time.sleep(0.1)
        if self.mount_status == 'Slewing':
            raise misc.HardwareStatusError('Already slewing')
        elif self.mount_status == 'Parked':
            raise misc.HardwareStatusError('Mount is parked')
        elif self.mount_status == 'IN BLINKY MODE':
            raise misc.HardwareStatusError('Mount is in Blinky Mode, motors disabled')

        # Calculate offset position
        step_deg = self.step/3600.
        step_ra = (step_deg*24./360.)/cos(self.sitech.dec*pi/180.)
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
            raise misc.HorizonError('Target too low, cannot slew')

        # Set values
        self.target_ra = ra
        self.target_dec = dec

        # Set flag
        self.slew_target_flag = 1

        return 'Slewing to offset coordinates'


    def set_step(self, offset):
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        if int(offset) < 0:
            raise ValueError('Offset value must be > 0')

        # Set values
        self.step = offset

        return 'New offset step set'


    # Internal functions
    def _get_target_distance(self):
        """Return the distance to the current target"""
        # Need to catch error if target not yet set
        if self.target_ra == None or self.target_dec == None:
            return None
        m_ra = self.sitech.ra
        m_dec = self.sitech.dec
        t_ra = self.target_ra
        t_dec = self.target_dec
        if not params.FREEZE_DEC:
            m_c = SkyCoord(m_ra, m_dec, unit=(u.hour, u.deg))
            t_c = SkyCoord(t_ra, t_dec, unit=(u.hour, u.deg))
            return t_c.separation(m_c).deg
        else:
            # note m_dec for both
            m_c = SkyCoord(m_ra, m_dec, unit=(u.hour, u.deg))
            t_c = SkyCoord(t_ra, m_dec, unit=(u.hour, u.deg))
            sep = t_c.separation(m_c).deg
            if sep < 0.07:
                return 0
            else:
                return sep


    def _ra_check_thread(self):
        """A thread to check the ra distance and cancel slewing when it's
        reached the target.

        Required for when the FREEZE_DEC is set, so the mount doesn't keep
        trying to slew to the dec target.

        If activated it will check the telescope when slewing, and if it's
        reached the RA target then stop the slewing and start tracking.
        """
        import numpy as np
        ra_distance = 0
        i = 0
        j = 0
        while True:
            if self.sitech.slewing:
                sleep_time = 0.1
                ra_distance_new = np.around(self._get_target_distance(),6)
                print(ra_distance_new, abs(ra_distance_new - ra_distance), i, j)
                if ra_distance_new < 0.01 and abs(ra_distance_new - ra_distance) < 0.0001:
                    i += 1
                if abs(ra_distance_new - ra_distance) == 0:
                    j += 1
                if i > 10 or j > 10:
                    self.logfile.info('Reached RA target, stopping slew')
                    self.sitech.halt()
                    time.sleep(0.1)
                    self.sitech.track()
                    ra_distance = 0
                    i = 0
                    j = 0
                else:
                    ra_distance = ra_distance_new
                time.sleep(0.1)
            else:
                time.sleep(1)


if __name__ == "__main__":
    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(DAEMON_ID):
        sys.exit()

    # Create the daemon object
    daemon = MntDaemon()

    # Start the daemon
    with Pyro4.Daemon(host=DAEMON_HOST, port=DAEMON_PORT) as pyro_daemon:
        uri = pyro_daemon.register(daemon, objectId=DAEMON_ID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=daemon.status_function)

    # Loop has closed
    daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)
