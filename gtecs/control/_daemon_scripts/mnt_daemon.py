#!/usr/bin/env python3
"""Daemon to access mount control."""

import threading
import time

import astropy.units as u
from astropy.coordinates import AltAz, SkyCoord
from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import errors
from gtecs.control import params
from gtecs.control.astronomy import (altaz_from_radec, get_ha,
                                     get_moon_distance, get_moon_params, get_sunalt,
                                     observatory_location, radec_from_altaz,
                                     within_mount_limits)
from gtecs.control.daemons import BaseDaemon
from gtecs.control.hardware.mount import DDM500, DDM500SDK, FakeDDM500, SiTech


class MntDaemon(BaseDaemon):
    """Mount hardware daemon class."""

    def __init__(self):
        super().__init__('mnt')

        # hardware
        self.mount = None

        # command flags
        self.slew_target_flag = 0
        self.slew_altaz_flag = 0
        self.start_tracking_flag = 0
        self.full_stop_flag = 0
        self.set_trackrate_flag = 0
        self.set_blinky_mode_flag = 0
        self.set_motor_power_flag = 0
        self.park_flag = 0
        self.unpark_flag = 0
        self.set_target_ra_flag = 0
        self.set_target_dec_flag = 0
        self.set_target_flag = 0
        self.offset_flag = 0
        self.guide_flag = 0

        # mount variables
        self.target_ra = None
        self.target_dec = None
        self.target_alt = None
        self.target_az = None
        self.targeting = None
        self.last_move_time = None
        self.set_blinky = False
        self.set_motor_power = True
        self.offset_direction = None
        self.offset_distance = None
        self.guide_direction = None
        self.guide_duration = None
        self.trackrate_ra = 0
        self.trackrate_dec = 0

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
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
                # Keep looping, it should retry the connection until it's successful
                if self.hardware_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

                # If there is an error then getting info failed.
                # Restart the loop to try reconnecting above.
                if self.hardware_error:
                    continue

                # Check if the mount has passed the limits and should stop
                # This is a nice idea, but including the Slewing status means we can never get
                # out if it triggers. You'd need to be able to detect if it's moving towards
                # or away form the limit, and that's tricky.
                # self._limit_check()

            # control functions
            # slew to target
            if self.slew_target_flag:
                try:
                    target_alt, target_az = altaz_from_radec(
                        self.target_ra * 360 / 24, self.target_dec)
                    targ_str = '{:.4f} {:.4f} ({:.2f} {:.2f})'.format(
                        self.target_ra * 360 / 24, self.target_dec, target_alt, target_az)
                    self.log.info('Slewing from {} to {}'.format(self._pos_str(), targ_str))
                    c = self.mount.slew_to_radec(self.target_ra, self.target_dec)
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('slew_target command failed')
                    self.log.debug('', exc_info=True)
                self.slew_target_flag = 0
                self.force_check_flag = True

            # slew to given alt/az
            if self.slew_altaz_flag:
                try:
                    target_ra, target_dec = radec_from_altaz(self.target_alt, self.target_az)
                    targ_str = '{:.4f} {:.4f} ({:.2f} {:.2f})'.format(
                        target_ra * 360 / 24, target_dec, self.target_alt, self.target_az)
                    self.log.info('Slewing from {} to {}'.format(self._pos_str(), targ_str))
                    c = self.mount.slew_to_altaz(self.target_alt, self.target_az)
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('slew_altaz command failed')
                    self.log.debug('', exc_info=True)
                self.slew_altaz_flag = 0
                self.force_check_flag = True

            # start tracking
            if self.start_tracking_flag:
                try:
                    self.log.info('Starting tracking')
                    self.log.debug('pos = {}'.format(self._pos_str()))
                    c = self.mount.track()
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('start_tracking command failed')
                    self.log.debug('', exc_info=True)
                self.start_tracking_flag = 0
                self.force_check_flag = True

            # stop all motion (tracking or slewing)
            if self.full_stop_flag:
                try:
                    self.log.info('Halting mount')
                    self.log.debug('pos = {}'.format(self._pos_str()))
                    c = self.mount.halt()
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('full_stop command failed')
                    self.log.debug('', exc_info=True)
                self.full_stop_flag = 0
                self.force_check_flag = True

            # set trackrate
            if self.set_trackrate_flag:
                try:
                    self.log.info('Setting track rate to ({},{})'.format(
                        self.trackrate_ra, self.trackrate_dec))
                    self.log.debug('pos = {}'.format(self._pos_str()))
                    c = self.mount.set_trackrate(self.trackrate_ra, self.trackrate_dec)
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('set_trackrate command failed')
                    self.log.debug('', exc_info=True)
                self.set_trackrate_flag = 0
                self.force_check_flag = True

            # turn blinky mode on or off
            if self.set_blinky_mode_flag:
                try:
                    mode = 'on' if self.set_blinky else 'off'
                    self.log.info('Turing blinky mode {}'.format(mode))
                    c = self.mount.set_blinky_mode(self.set_blinky)
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('set_blinky_mode command failed')
                    self.log.debug('', exc_info=True)
                self.set_blinky = False
                self.set_blinky_mode_flag = 0
                self.force_check_flag = True

            # power motors on or off
            if self.set_motor_power_flag:
                try:
                    mode = 'on' if self.set_motor_power else 'off'
                    self.log.info('Turing motors {}'.format(mode))
                    c = self.mount.set_motor_power(self.set_motor_power)
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('set_motor_power command failed')
                    self.log.debug('', exc_info=True)
                self.set_motor_power = True
                self.set_motor_power_flag = 0
                self.force_check_flag = True

            # park the mount
            if self.park_flag:
                try:
                    self.log.info('Parking mount')
                    self.log.debug('pos = {}'.format(self._pos_str()))
                    c = self.mount.park()
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('park command failed')
                    self.log.debug('', exc_info=True)
                # clear the stored coordinates
                self.target_ra = None
                self.target_dec = None
                self.target_alt = None
                self.target_az = None
                self.targeting = None
                self.park_flag = 0
                self.force_check_flag = True

            # unpark the mount
            if self.unpark_flag:
                try:
                    self.log.info('Unparking mount')
                    self.log.debug('pos = {}'.format(self._pos_str()))
                    c = self.mount.unpark()
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('unpark command failed')
                    self.log.debug('', exc_info=True)
                self.unpark_flag = 0
                self.force_check_flag = True

            # offset
            if self.offset_flag:
                try:
                    self.log.info('Offsetting {} {} arcsec'.format(
                        self.offset_direction, self.offset_distance))
                    self.log.debug('pos = {}'.format(self._pos_str()))
                    c = self.mount.offset(self.offset_direction, self.offset_distance)
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('offset command failed')
                    self.log.debug('', exc_info=True)
                self.offset_flag = 0
                self.offset_direction = None
                self.offset_distance = None
                self.force_check_flag = True

            # pulse guide
            if self.guide_flag:
                try:
                    self.log.info('Pulse guiding {} for {} ms'.format(
                        self.guide_direction, self.guide_duration))
                    self.log.debug('pos = {}'.format(self._pos_str()))
                    c = self.mount.pulse_guide(self.guide_direction, self.guide_duration)
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('pulse_guide command failed')
                    self.log.debug('', exc_info=True)
                self.guide_flag = 0
                self.guide_direction = None
                self.guide_duration = None
                self.force_check_flag = True

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _connect(self):
        """Connect to hardware."""
        # Connect to the mount
        if self.mount is None:
            try:
                if params.FAKE_MOUNT:
                    self.mount = FakeDDM500(params.MOUNT_HOST,
                                            params.MOUNT_PORT,
                                            log=self.log,
                                            log_debug=params.MOUNT_DEBUG,
                                            )
                elif params.MOUNT_CLASS == 'SITECH':
                    self.mount = SiTech(params.MOUNT_HOST,
                                        params.MOUNT_PORT,
                                        log=self.log,
                                        log_debug=params.MOUNT_DEBUG,
                                        )
                elif params.MOUNT_CLASS == 'ASA':
                    self.mount = DDM500(params.MOUNT_HOST,
                                        params.MOUNT_PORT,
                                        log=self.log,
                                        log_debug=params.MOUNT_DEBUG,
                                        )
                elif params.MOUNT_CLASS == 'ASASDK':
                    self.mount = DDM500SDK(params.MOUNT_HOST,
                                           params.MOUNT_PORT,
                                           log=self.log,
                                           log_debug=params.MOUNT_DEBUG,
                                           )
                    # try resetting the device connetion to clear any errors
                    self.mount.disconnect()
                    time.sleep(0.5)
                    self.mount.connect()
                else:
                    raise ValueError('Unknown mount class')
                self.log.info('Connected to mount')
                if 'mount' in self.bad_hardware:
                    self.bad_hardware.remove('mount')
            except Exception:
                self.mount = None
                if 'mount' not in self.bad_hardware:
                    self.log.error('Failed to connect to mount')
                    self.bad_hardware.add('mount')

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

        # Get info from mount
        try:
            temp_info['status'] = self.mount.status
            temp_info['mount_alt'] = self.mount.alt
            temp_info['mount_az'] = self.mount.az
            temp_info['mount_ra'] = self.mount.ra
            temp_info['mount_dec'] = self.mount.dec
            temp_info['lst'] = self.mount.sidereal_time
            temp_info['ha'] = get_ha(temp_info['mount_ra'] * 360 / 24,
                                     temp_info['mount_dec'],
                                     Time(temp_info['time'], format='unix'))
            if isinstance(self.mount, SiTech):
                temp_info['class'] = 'SITECH'
                # temp_info['nonsidereal'] = self.mount.nonsidereal
            elif isinstance(self.mount, (DDM500, DDM500SDK, FakeDDM500)):
                temp_info['class'] = 'ASA'
                temp_info['position_error'] = self.mount.position_error
                temp_info['tracking_error'] = self.mount.tracking_error
                temp_info['motor_current'] = self.mount.motor_current
                temp_info['tracking_rate'] = self.mount.tracking_rate
                temp_info['motors_on'] = self.mount.motors_on

                # Save a history of errors so we can add to image headers
                if (self.info and 'position_error_history' in self.info and
                        self.info['position_error_history'] is not None):
                    p_error_history = self.info['position_error_history']
                else:
                    p_error_history = []
                if (self.info and 'tracking_error_history' in self.info and
                        self.info['tracking_error_history'] is not None):
                    t_error_history = self.info['tracking_error_history']
                else:
                    t_error_history = []
                if (self.info and 'motor_current_history' in self.info and
                        self.info['motor_current_history'] is not None):
                    current_history = self.info['motor_current_history']
                else:
                    current_history = []
                p_error_history = [hist for hist in p_error_history
                                   if hist[0] > self.loop_time - params.MOUNT_HISTORY_PERIOD]
                t_error_history = [hist for hist in t_error_history
                                   if hist[0] > self.loop_time - params.MOUNT_HISTORY_PERIOD]
                current_history = [hist for hist in current_history
                                   if hist[0] > self.loop_time - params.MOUNT_HISTORY_PERIOD]
                p_error_history.append((self.loop_time, temp_info['position_error']))
                t_error_history.append((self.loop_time, temp_info['tracking_error']))
                current_history.append((self.loop_time, temp_info['motor_current']))
                temp_info['position_error_history'] = p_error_history
                temp_info['tracking_error_history'] = t_error_history
                temp_info['motor_current_history'] = current_history

                # Log any errors or warnings from the mount, along with the time of occurrence
                error_status = self.mount.error_check()
                if error_status is not None:
                    self.log.error('Mount raises error: {}'.format(error_status))
                    temp_info['error_status'] = error_status
                    temp_info['error_status_time'] = self.loop_time
                elif (self.info and 'error_status' in self.info):
                    # Keep old errors until a new one is raised
                    temp_info['error_status'] = self.info['error_status']
                    temp_info['error_status_time'] = self.info['error_status_time']
                else:
                    temp_info['error_status'] = None
                    temp_info['error_status_time'] = None

                warning_status = self.mount.warning_check()
                if warning_status is not None:
                    self.log.warning('Mount raises warning: {}'.format(warning_status))
                    temp_info['warning_status'] = warning_status
                    temp_info['warning_status_time'] = self.loop_time
                elif (self.info and 'warning_status' in self.info):
                    # Keep old warnings until a new one is raised
                    temp_info['warning_status'] = self.info['warning_status']
                    temp_info['warning_status_time'] = self.info['warning_status_time']
                else:
                    temp_info['warning_status'] = None
                    temp_info['warning_status_time'] = None

        except Exception:
            self.log.error('Failed to get mount info')
            self.log.debug('', exc_info=True)
            temp_info['status'] = None
            temp_info['mount_alt'] = None
            temp_info['mount_az'] = None
            temp_info['mount_ra'] = None
            temp_info['mount_dec'] = None
            temp_info['lst'] = None
            temp_info['ha'] = None
            if isinstance(self.mount, SiTech):
                temp_info['class'] = 'SITECH'
                temp_info['nonsidereal'] = None
            elif isinstance(self.mount, (DDM500, DDM500SDK, FakeDDM500)):
                temp_info['class'] = 'ASA'
                temp_info['position_error'] = None
                temp_info['tracking_error'] = None
                temp_info['motor_current'] = None
                temp_info['tracking_rate'] = None
                temp_info['motors_on'] = None
                temp_info['position_error_history'] = None
                temp_info['tracking_error_history'] = None
                temp_info['motor_current_history'] = None
            # Report the connection as failed
            self.mount = None
            if 'mount' not in self.bad_hardware:
                self.bad_hardware.add('mount')

        # Get astronomy info
        try:
            now = Time(temp_info['time'], format='unix')
            sun_alt = get_sunalt(now)
            temp_info['sun_alt'] = sun_alt

            moon_alt, moon_ill, moon_phase = get_moon_params(now)
            temp_info['moon_alt'] = moon_alt
            temp_info['moon_ill'] = moon_ill
            temp_info['moon_phase'] = moon_phase

            if temp_info['mount_ra'] is not None and temp_info['mount_dec'] is not None:
                moon_dist = get_moon_distance(temp_info['mount_ra'] * 360 / 24,
                                              temp_info['mount_dec'],
                                              now,
                                              )
            else:
                moon_dist = None
            temp_info['moon_dist'] = moon_dist
        except Exception:
            self.log.error('Failed to get astronomy info')
            self.log.debug('', exc_info=True)
            temp_info['sun_alt'] = None
            temp_info['moon_alt'] = None
            temp_info['moon_ill'] = None
            temp_info['moon_phase'] = None
            temp_info['moon_dist'] = None

        # Get other internal info
        temp_info['target_ra'] = self.target_ra
        temp_info['target_dec'] = self.target_dec
        temp_info['target_alt'] = self.target_alt
        temp_info['target_az'] = self.target_az
        temp_info['target_dist'] = self._get_target_distance()
        temp_info['targeting'] = self.targeting
        temp_info['last_move_time'] = self.last_move_time
        temp_info['trackrate_ra'] = self.trackrate_ra
        temp_info['trackrate_dec'] = self.trackrate_dec
        temp_info['nonsidereal'] = self.trackrate_ra != 0 or self.trackrate_dec != 0

        # Write debug log line
        try:
            if not self.info:
                self.log.debug('Mount is {}'.format(temp_info['status']))
            elif temp_info['status'] != self.info['status']:
                self.log.debug('Mount is {}'.format(temp_info['status']))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    def _limit_check(self):
        """Check if the mount position is past the valid limits."""
        if not within_mount_limits(self.info['mount_ra'] * 360 / 24,
                                   self.info['mount_dec'],
                                   Time.now()):
            self.log.error('Mount is outside of limits')
            if self.info['status'] in ['Tracking', 'Slewing']:
                self.log.error('Stopping mount')
                self.force_check_flag = True
                self.full_stop_flag = 1

    def _get_target_distance(self):
        """Return the distance to the current target."""
        if self.mount is None:
            return None
        if (self.targeting == 'radec' and
                self.target_ra is not None and self.target_dec is not None):
            current_coord = SkyCoord(self.mount.ra, self.mount.dec, unit=(u.hour, u.deg))
            target_coord = SkyCoord(self.target_ra, self.target_dec, unit=(u.hour, u.deg))
            return current_coord.separation(target_coord).deg
        elif (self.targeting == 'altaz' and
                self.target_alt is not None and self.target_az is not None):
            now = Time.now()
            location = observatory_location()
            current_coord = AltAz(alt=self.mount.alt * u.deg, az=self.mount.az * u.deg,
                                  obstime=now, location=location)
            target_coord = AltAz(alt=self.target_alt * u.deg, az=self.target_az * u.deg,
                                 obstime=now, location=location)
            return current_coord.separation(target_coord).deg
        else:
            return None

    def _pos_str(self):
        """Return a simple string reporting the current position."""
        pos_str = '{:.4f} {:.4f} ({:.2f} {:.2f})'.format(
            self.info['mount_ra'] * 360 / 24, self.info['mount_dec'],
            self.info['mount_alt'], self.info['mount_az'])
        return pos_str

    # Control functions
    def slew_to_radec(self, ra=None, dec=None):
        """Slew to specified coordinates.

        If coordinates are not given, slew to the saved target.
        """
        # Check input
        if ra is None:
            ra = self.target_ra
        if dec is None:
            dec = self.target_dec
        if ra is None or dec is None:
            raise errors.HardwareStatusError('No coordinates given, and target not set')
        if not (0 <= ra < 24):
            raise ValueError('RA in hours must be between 0 and 24')
        if not (-90 <= dec <= 90):
            raise ValueError('Dec in degrees must be between -90 and +90')
        if not within_mount_limits(ra * 360 / 24, dec, Time.now()):
            raise ValueError('Target is outside of mount limits, cannot slew')

        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise errors.HardwareStatusError('Already slewing')
        elif self.info['status'] == 'Parked' and not isinstance(self.mount, DDM500SDK):
            # SDK doesn't need to unpark before slewing
            raise errors.HardwareStatusError('Mount is parked, need to unpark before slewing')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise errors.HardwareStatusError('Mount is in blinky mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise errors.HardwareStatusError('Mount motors are powered off')

        # Set values
        self.target_ra = ra
        self.target_dec = dec
        self.target_alt = None
        self.target_az = None
        self.targeting = 'radec'

        # Set flag
        self.force_check_flag = True
        self.slew_target_flag = 1

        return 'Slewing to coordinates ({:.2f} deg)'.format(self._get_target_distance())

    def slew_to_altaz(self, alt, az):
        """Slew to specified alt/az."""
        # Check input
        if not (0 <= alt < 90):
            raise ValueError('Alt in degrees must be between 0 and 90')
        if not (0 <= az < 360):
            raise ValueError('Az in degrees must be between 0 and 360')
        if not alt > params.MIN_ELEVATION:
            raise ValueError('Target is below {} alt, cannot slew'.format(params.MIN_ELEVATION))

        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise errors.HardwareStatusError('Already slewing')
        elif self.info['status'] == 'Parked' and not isinstance(self.mount, DDM500SDK):
            # SDK doesn't need to unpark before slewing
            raise errors.HardwareStatusError('Mount is parked, need to unpark before slewing')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise errors.HardwareStatusError('Mount is in blinky mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise errors.HardwareStatusError('Mount motors are powered off')

        # Set values
        self.target_alt = alt
        self.target_az = az
        self.target_ra = None
        self.target_dec = None
        self.targeting = 'altaz'

        # Set flag
        self.force_check_flag = True
        self.slew_altaz_flag = 1

        return 'Slewing to alt/az ({:.2f} deg)'.format(self._get_target_distance())

    def slew_to_altaz_sidereal(self, alt, az):
        """Slew to specified alt/az by targeting the RA/Dec coordinates."""
        # Check input
        if not (0 <= alt < 90):
            raise ValueError('Alt in degrees must be between 0 and 90')
        if not (0 <= az < 360):
            raise ValueError('Az in degrees must be between 0 and 360')

        # ASA SDK count Az=0 from south, which is different from Astropy
        if isinstance(self.mount, DDM500SDK):
            az -= 180
            if az < 0:
                az += 360

        # Convert to RA/Dec and use that function instead.
        ra, dec = radec_from_altaz(alt, az, Time.now())
        ra = ra * 24 / 360
        return self.slew_to_radec(ra, dec)

    def start_tracking(self):
        """Start the mount tracking."""
        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Tracking':
            return 'Already tracking'
        elif self.info['status'] == 'Slewing':
            return 'Currently slewing, will track when reached target'
        elif self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise errors.HardwareStatusError('Mount is in blinky mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise errors.HardwareStatusError('Mount motors are powered off')
        if not within_mount_limits(self.info['mount_ra'] * 360 / 24,
                                   self.info['mount_dec'],
                                   Time.now()):
            raise errors.HardwareStatusError('Mount is past limits, cannot track')

        # Set flag
        self.force_check_flag = True
        self.start_tracking_flag = 1

        return 'Started tracking'

    def full_stop(self):
        """Stop the mount moving (slewing or tracking)."""
        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Stopped':
            return 'Already stopped'
        elif self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked')

        # Set flag
        self.force_check_flag = True
        self.full_stop_flag = 1

        return 'Stopping mount'

    def set_trackrate(self, ra_rate=0, dec_rate=0):
        """Set tracking rate in RA and Dec in arcseconds per second (0=default)."""
        if isinstance(self.mount, (DDM500, DDM500SDK, FakeDDM500)):
            raise NotImplementedError('Mount trackrate command is not implemented')

        # Set values
        self.trackrate_ra = ra_rate
        self.trackrate_dec = dec_rate

        # Set flag
        self.force_check_flag = True
        self.set_trackrate_flag = 1

        if ra_rate == 0 and dec_rate == 0:
            s = 'Resetting track rate to sidereal'
        else:
            s = 'Setting track rate'
        return s

    def blinky(self, activate):
        """Turn on or off blinky mode."""
        if not isinstance(self.mount, SiTech):
            raise NotImplementedError('Only SiTech mounts use blinky mode')

        # Check current status
        self.wait_for_info()
        if activate and self.mount.blinky:
            return 'Already in blinky mode'
        elif not activate and not self.mount.blinky:
            return 'Already not in blinky mode'

        # Set values
        self.set_blinky = activate

        # Set flag
        self.force_check_flag = True
        self.set_blinky_mode_flag = 1

        if activate:
            s = 'Turning on blinky mode'
        else:
            s = 'Turning off blinky mode'
        return s

    def power_motors(self, activate):
        """Turn on or off the mount motors."""
        if not isinstance(self.mount, (DDM500, DDM500SDK, FakeDDM500)):
            raise NotImplementedError('Only ASA mounts allow motors to be powered')

        # Check current status
        self.wait_for_info()
        if activate and self.mount.motors_on:
            return 'Motors are already on'
        elif not activate and not self.mount.motors_on:
            return 'Motors are already off'

        # Set values
        self.set_motor_power = activate

        # Set flag
        self.force_check_flag = True
        self.set_motor_power_flag = 1

        if activate:
            s = 'Turning on mount motors'
        else:
            s = 'Turning off mount motors'
        return s

    def park(self):
        """Move the mount to the park position."""
        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Parked':
            return 'Already parked'
        elif self.info['status'] == 'Parking':
            return 'Already parking'
        elif self.info['status'] == 'IN BLINKY MODE':
            raise errors.HardwareStatusError('Mount is in Blinky Mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise errors.HardwareStatusError('Mount motors are powered off')

        # Set flag
        self.force_check_flag = True
        self.park_flag = 1

        return 'Parking mount'

    def unpark(self):
        """Unpark the mount."""
        # Check current status
        self.wait_for_info()
        if self.info['status'] not in ['Parked', 'Parking']:
            return 'Mount is not parked'

        # If we're already parking then stop it
        if self.info['status'] == 'Parking':
            self.full_stop_flag = 1
            time.sleep(0.2)

        # If we are parked then we need to turn off blinky mode or turn on the motors
        if self.info['status'] == 'Parked':
            if isinstance(self.mount, SiTech):
                self.set_blinky = False
                self.set_blinky_mode_flag = 1
            elif isinstance(self.mount, (DDM500, DDM500SDK, FakeDDM500)):
                self.set_motor_power = True
                self.set_motor_power_flag = 1
            time.sleep(0.2)

        # Set flag
        self.force_check_flag = True
        self.unpark_flag = 1

        return 'Unparking mount'

    def set_target_ra(self, ra):
        """Set the target RA."""
        # Check input
        if not (0 <= ra < 24):
            raise ValueError('RA in hours must be between 0 and 24')

        # Check current status
        self.wait_for_info()
        if isinstance(self.mount, SiTech) and self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked, can not set target')

        # Set values
        self.target_ra = ra
        self.target_alt = None
        self.target_az = None
        self.targeting = 'radec'

        self.log.info('Set target RA to {:.4f}'.format(ra))
        return 'Setting target RA'

    def set_target_dec(self, dec):
        """Set the target Dec."""
        # Check input
        if not (-90 <= dec <= 90):
            raise ValueError('Dec in degrees must be between -90 and +90')

        # Check current status
        self.wait_for_info()
        if isinstance(self.mount, SiTech) and self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked, can not set target')

        # Set values
        self.target_dec = dec
        self.target_alt = None
        self.target_az = None
        self.targeting = 'radec'

        self.log.info('Set target Dec to {:.4f}'.format(dec))
        return 'Setting target Dec'

    def set_target(self, ra, dec):
        """Set the target location."""
        # Check input
        if not (0 <= ra < 24):
            raise ValueError('RA in hours must be between 0 and 24')
        if not (-90 <= dec <= 90):
            raise ValueError('Dec in degrees must be between -90 and +90')

        # Check current status
        self.wait_for_info()
        if isinstance(self.mount, SiTech) and self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked, can not set target')

        # Set values
        self.target_ra = ra
        self.target_dec = dec
        self.target_alt = None
        self.target_az = None
        self.targeting = 'radec'

        self.log.info('Set target RA to {:.4f}'.format(ra))
        self.log.info('Set target Dec to {:.4f}'.format(dec))
        return 'Setting target'

    def clear_target(self):
        """Clear the stored target."""
        # Check current status
        self.wait_for_info()

        # Set values
        self.target_ra = None
        self.target_dec = None
        self.target_alt = None
        self.target_az = None
        self.targeting = None

        self.log.info('Cleared target')
        return 'Cleared target'

    def offset(self, direction, distance):
        """Offset in a specified (cardinal) direction by the given distance."""
        # Check input
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError('Invalid direction "{}" (should be [N,E,S,W])'.format(direction))

        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise errors.HardwareStatusError('Already slewing')
        elif self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise errors.HardwareStatusError('Mount is in Blinky Mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise errors.HardwareStatusError('Mount motors are powered off')

        # Set values
        self.offset_direction = direction
        self.offset_distance = distance

        # Set flag
        self.force_check_flag = True
        self.offset_flag = 1

        return 'Slewing to offset coordinates'

    def pulse_guide(self, direction, duration):
        """Pulse guide in a specified (cardinal) direction for the given time."""
        if not isinstance(self.mount, (DDM500, DDM500SDK, FakeDDM500)):
            raise NotImplementedError('Only ASA mounts mounts have pulse guiding implemented')

        # Check input
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError('Invalid direction "{}" (should be [N,E,S,W])'.format(direction))

        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise errors.HardwareStatusError('Already slewing')
        elif self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise errors.HardwareStatusError('Mount is in Blinky Mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise errors.HardwareStatusError('Mount motors are powered off')

        # Set values
        self.guide_direction = direction
        self.guide_duration = duration

        # Set flag
        self.force_check_flag = True
        self.guide_flag = 1

        return 'Pulse guiding'


if __name__ == '__main__':
    with make_pid_file('mnt'):
        MntDaemon()._run()
