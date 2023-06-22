#!/usr/bin/env python3
"""Daemon to access mount control."""

import os
import threading
import time

import astropy.units as u
from astropy.coordinates import AltAz, HADec, SkyCoord
from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import errors
from gtecs.control import params
from gtecs.control.astronomy import (get_moon_distance, get_moon_params, get_sunalt,
                                     observatory_location
                                     )
from gtecs.control.daemons import BaseDaemon
from gtecs.control.hardware.mount import DDM500, FakeDDM500, SiTech

import numpy as np


class MntDaemon(BaseDaemon):
    """Mount hardware daemon class."""

    def __init__(self):
        super().__init__('mnt')

        # hardware
        self.mount = None

        # command flags
        self.slew_flag = 0
        self.track_flag = 0
        self.halt_flag = 0
        self.park_flag = 0
        self.unpark_flag = 0
        self.offset_flag = 0
        self.guide_flag = 0
        self.sync_flag = 0
        self.set_trackrate_flag = 0
        self.set_blinky_flag = 0
        self.set_motor_power_flag = 0
        self.clear_error_flag = 0

        # mount variables
        self.target = None
        self.last_move_time = None
        self.offset_direction = None
        self.offset_distance = None
        self.guide_direction = None
        self.guide_duration = None
        self.sync_position = None
        self.trackrate_ra = 0
        self.trackrate_dec = 0
        self.set_blinky = False
        self.set_motor_power = True

        # position offset (stored in a file, so it isn't forgotten if we restart)
        self.position_offset_file = os.path.join(params.FILE_PATH, 'mount_offset')
        try:
            with open(self.position_offset_file, 'r') as f:
                self.position_offset = [float(i) for i in f.read().strip().split()]
            if self.position_offset == [0, 0]:
                self.position_offset = None
        except Exception:
            self.log.error('Failed to read mount offset file')
            self.log.debug('', exc_info=True)
            self.position_offset = None
            with open(self.position_offset_file, 'w') as f:
                f.write('0 0')

        # astronomy params
        self.location = observatory_location()

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

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

                # Check if the mount has passed the limits and should stop
                # This is a nice idea, but including the Slewing status means we can never get
                # out if it triggers. You'd need to be able to detect if it's moving towards
                # or away form the limit, and that's tricky.
                # self._limit_check()

            # control functions
            # slew to target
            if self.slew_flag:
                try:
                    msg = f'Slewing from {self._pos_str()} to {self._pos_str(self.target)}'
                    self.log.info(msg)
                    # Convert into mount position
                    coord = self._offset_desired_to_mount(self.target)
                    if self.position_offset is not None:
                        msg = f'Offset desired position ({self._pos_str(self.target)}) '
                        msg += f'to mount position ({self._pos_str(coord)})'
                        self.log.debug(msg)
                    if isinstance(coord, SkyCoord):
                        c = self.mount.slew_to_radec(coord.ra.hourangle, coord.dec.deg)
                    elif isinstance(coord, AltAz):
                        c = self.mount.slew_to_altaz(coord.alt.deg, coord.az.deg)
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('slew command failed')
                    self.log.debug('', exc_info=True)
                self.slew_flag = 0
                self.force_check_flag = True

            # start tracking
            if self.track_flag:
                try:
                    self.log.info('Starting tracking')
                    self.log.debug(f'current position: {self._pos_str()}')
                    c = self.mount.track()
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('track command failed')
                    self.log.debug('', exc_info=True)
                self.track_flag = 0
                self.force_check_flag = True

            # stop all motion (tracking or slewing)
            if self.halt_flag:
                try:
                    self.log.info('Halting mount')
                    self.log.debug(f'current position: {self._pos_str()}')
                    c = self.mount.halt()
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('halt command failed')
                    self.log.debug('', exc_info=True)
                self.halt_flag = 0
                self.force_check_flag = True

            # park the mount
            if self.park_flag:
                try:
                    self.log.info('Parking mount')
                    self.log.debug(f'current position: {self._pos_str()}')
                    c = self.mount.park()
                    if c:
                        self.log.info(c)
                    self.last_move_time = self.loop_time
                except Exception:
                    self.log.error('park command failed')
                    self.log.debug('', exc_info=True)
                # clear the stored coordinates
                self.target = None
                self.park_flag = 0
                self.force_check_flag = True

            # unpark the mount
            if self.unpark_flag:
                try:
                    self.log.info('Unparking mount')
                    self.log.debug(f'current position: {self._pos_str()}')
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
                    msg = f'Offsetting {self.offset_direction} {self.offset_distance} arcsec'
                    self.log.info(msg)
                    self.log.debug(f'current position: {self._pos_str()}')
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
                    msg = f'Pulse guiding {self.guide_direction} for {self.guide_duration} ms'
                    self.log.info(msg)
                    self.log.debug(f'current position: {self._pos_str()}')
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

            # sync
            if self.sync_flag:
                try:
                    msg = f'Syncing position from {self._pos_str()}'
                    msg += f' to {self._pos_str(self.sync_position)}'
                    self.log.info(msg)
                    # Convert into mount position
                    coord = self._offset_desired_to_mount(self.sync_position)
                    if self.position_offset is not None:
                        msg = f'Offset desired position ({self._pos_str(self.target)}) '
                        msg += f'to mount position ({self._pos_str(coord)})'
                        self.log.debug(msg)
                    c = self.mount.sync_radec(coord.ra.hourangle, coord.dec.deg)
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('sync_to_radec command failed')
                    self.log.debug('', exc_info=True)
                self.sync_flag = 0
                self.sync_position = None
                self.force_check_flag = True

            # set trackrate
            if self.set_trackrate_flag:
                try:
                    msg = f'Setting track rate to ({self.trackrate_ra},{self.trackrate_dec})'
                    self.log.info(msg)
                    self.log.debug(f'current position: {self._pos_str()}')
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
            if self.set_blinky_flag:
                try:
                    mode = 'on' if self.set_blinky else 'off'
                    self.log.info(f'Turing blinky mode {mode}')
                    self.log.debug(f'current position: {self._pos_str()}')
                    c = self.mount.set_blinky_mode(self.set_blinky)
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('set_blinky command failed')
                    self.log.debug('', exc_info=True)
                self.set_blinky = None
                self.set_blinky_flag = 0
                self.force_check_flag = True

            # power motors on or off
            if self.set_motor_power_flag:
                try:
                    mode = 'on' if self.set_motor_power else 'off'
                    self.log.info(f'Turing motors {mode}')
                    self.log.debug(f'current position: {self._pos_str()}')
                    c = self.mount.set_motor_power(self.set_motor_power)
                    if c:
                        self.log.info(c)
                except Exception:
                    self.log.error('set_motor_power command failed')
                    self.log.debug('', exc_info=True)
                self.set_motor_power = None
                self.set_motor_power_flag = 0
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
                                        fake_parking=params.FAKE_MOUNT_PARKING,
                                        log=self.log,
                                        log_debug=params.MOUNT_DEBUG,
                                        )
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
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from mount
        try:
            temp_info['status'] = self.mount.status

            # Get the actual pointing coordinates
            temp_info['mount_ra_pointing'] = self.mount.ra
            temp_info['mount_dec_pointing'] = self.mount.dec
            temp_info['mount_alt_pointing'] = self.mount.alt
            temp_info['mount_az_pointing'] = self.mount.az

            now = Time(temp_info['time'], format='unix')
            coords = SkyCoord(temp_info['mount_ra_pointing'] * u.hourangle,
                              temp_info['mount_dec_pointing'] * u.deg,
                              )
            coords_hadec = coords.transform_to(HADec(obstime=now, location=self.location))
            temp_info['mount_ha_pointing'] = coords_hadec.ha.hourangle

            if self.position_offset is None:
                temp_info['mount_ra'] = temp_info['mount_ra_pointing']
                temp_info['mount_dec'] = temp_info['mount_dec_pointing']
                temp_info['mount_alt'] = temp_info['mount_alt_pointing']
                temp_info['mount_az'] = temp_info['mount_az_pointing']
                temp_info['mount_ha'] = temp_info['mount_ha_pointing']
            else:
                # Need to convert using given offset
                coords = self._offset_mount_to_desired(coords)
                temp_info['mount_ra'] = coords.ra.hourangle
                temp_info['mount_dec'] = coords.dec.deg
                coords_altaz = coords.transform_to(AltAz(obstime=now, location=self.location))
                temp_info['mount_alt'] = coords_altaz.alt.deg
                temp_info['mount_az'] = coords_altaz.az.deg
                coords_hadec = coords.transform_to(HADec(obstime=now, location=self.location))
                temp_info['mount_ha'] = coords_hadec.ha.hourangle

            if isinstance(self.mount, SiTech):
                temp_info['class'] = 'SITECH'
                # temp_info['nonsidereal'] = self.mount.nonsidereal
            elif isinstance(self.mount, (DDM500, FakeDDM500)):
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
                    self.log.error(f'Mount raises error: {error_status}')
                    temp_info['error_status'] = error_status
                    temp_info['error_status_time'] = self.loop_time
                elif (self.info and 'error_status' in self.info and self.clear_error_flag == 0):
                    # Keep old errors until a new one is raised, or we clear it
                    temp_info['error_status'] = self.info['error_status']
                    temp_info['error_status_time'] = self.info['error_status_time']
                else:
                    self.clear_error_flag = 0
                    temp_info['error_status'] = None
                    temp_info['error_status_time'] = None

                warning_status = self.mount.warning_check()
                if warning_status is not None:
                    self.log.warning(f'Mount raises warning: {warning_status}')
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
            temp_info['mount_ra_pointing'] = None
            temp_info['mount_dec_pointing'] = None
            temp_info['mount_alt_pointing'] = None
            temp_info['mount_az_pointing'] = None
            temp_info['mount_ha_pointing'] = None
            temp_info['mount_ra'] = None
            temp_info['mount_dec'] = None
            temp_info['mount_alt'] = None
            temp_info['mount_az'] = None
            temp_info['mount_ha'] = None
            if isinstance(self.mount, SiTech):
                temp_info['class'] = 'SITECH'
                temp_info['nonsidereal'] = None
            elif isinstance(self.mount, (DDM500, FakeDDM500)):
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

            lst = now.sidereal_time(kind='apparent', longitude=self.location).hourangle
            temp_info['lst'] = lst

            sun_alt = get_sunalt(now)
            temp_info['sun_alt'] = sun_alt

            moon_alt, moon_ill, moon_phase = get_moon_params(now)
            temp_info['moon_alt'] = moon_alt
            temp_info['moon_ill'] = moon_ill
            temp_info['moon_phase'] = moon_phase

            if self.current_position is not None:
                temp_info['moon_dist'] = get_moon_distance(self.current_position.ra.deg,
                                                           self.current_position.dec.deg,
                                                           now)
            else:
                temp_info['moon_dist'] = None
        except Exception:
            self.log.error('Failed to get astronomy info')
            self.log.debug('', exc_info=True)
            temp_info['lst'] = None
            temp_info['sun_alt'] = None
            temp_info['moon_alt'] = None
            temp_info['moon_ill'] = None
            temp_info['moon_phase'] = None
            temp_info['moon_dist'] = None

        # Get other internal info
        if isinstance(self.target, SkyCoord):
            temp_info['target_ra'] = self.target.ra.hourangle
            temp_info['target_dec'] = self.target.dec.deg
            temp_info['target_alt'] = None
            temp_info['target_az'] = None
        elif isinstance(self.target, AltAz):
            temp_info['target_ra'] = None
            temp_info['target_dec'] = None
            temp_info['target_alt'] = self.target.alt.deg
            temp_info['target_az'] = self.target.az.deg
        else:
            temp_info['target_ra'] = None
            temp_info['target_dec'] = None
            temp_info['target_alt'] = None
            temp_info['target_az'] = None
        temp_info['target_dist'] = self.target_distance
        temp_info['last_move_time'] = self.last_move_time
        temp_info['trackrate_ra'] = self.trackrate_ra
        temp_info['trackrate_dec'] = self.trackrate_dec
        temp_info['nonsidereal'] = self.trackrate_ra != 0 or self.trackrate_dec != 0
        temp_info['position_offset'] = self.position_offset

        # Write debug log line
        try:
            if not self.info:
                self.log.debug('Mount is {} [{}]'.format(temp_info['status'],
                                                         self._pos_str(coords)))
            elif temp_info['status'] != self.info['status']:
                self.log.debug('Mount is {} [{}]'.format(temp_info['status'],
                                                         self._pos_str(coords)))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    def _limit_check(self):
        """Check if the mount position is past the valid limits."""
        try:
            self._within_limits(self.current_position)
        except Exception:
            self.log.error(f'Mount is outside of limits [{self._pos_str()}]')
            self.log.debug('', exc_info=True)
            # Stop any movement
            if self.info['status'] in ['Tracking', 'Slewing']:
                self.log.error('Stopping mount')
                self.force_check_flag = True
                self.halt_flag = 1

    @property
    def current_position(self):
        """Get the current pointing position as an Astropy SkyCoord."""
        if self.mount is None:
            return None
        coords = SkyCoord(self.mount.ra, self.mount.dec, unit=(u.hour, u.deg))
        return self._offset_mount_to_desired(coords)

    def _offset_desired_to_mount(self, coords):
        """Use the internal offset to convert the desired coordinates to the mount position."""
        if self.position_offset is None:
            return coords
        offset_distance = self.position_offset[0] * u.deg
        offset_angle = self.position_offset[1] * u.deg
        # This is simple, since the distance and baring are always constant.
        # We can't offset from AltAz, so we have to convert to ICRS first then back afterwards.
        if isinstance(coords, SkyCoord):
            new_coords = coords.directional_offset_by(offset_angle, offset_distance)
        if isinstance(coords, AltAz):
            coords_altaz = coords
            coords = SkyCoord(coords_altaz).transform_to('icrs')
            new_coords = coords.directional_offset_by(offset_angle, offset_distance)
            new_coords.transform_to(AltAz(obstime=coords_altaz.obstime, location=self.location))
        return new_coords

    def _offset_mount_to_desired(self, coords):
        """Use the internal offset to correct the mount coordinates to the desired position."""
        if self.position_offset is None:
            return coords
        # We have point B (the mount position) and want to find the coordinates of point A.
        # We know the distance from A to B (d) and the angle from A to B (theta).
        d = self.position_offset[0] * u.deg
        theta = self.position_offset[1] * u.deg
        # HOWEVER the offset angle from B to A is NOT the same as A to B
        # (or anything neat like 180-theta), so we can't just offset back.
        # There's a lot of horrible trig, but it is doable.
        # Consider a triangle with point C at the northern celestial pole.
        # The distance from A to C is 90 - the declination of A.
        # The distance from B to C is 90 - the declination of B.
        # The angle ACB is the difference is right ascension between A and B.
        # Using the spherical law of sines:
        # 1) sin(theta) / sin(90-dec_B) = sin(ra_B-ra_A) / sin(d)
        # Therefore we can find ra_A:
        new_ra = coords.ra - np.arcsin(np.sin(theta) * np.sin(d) / np.cos(coords.dec))
        # Finding dec_A is much worse, since we don't know the angle ABC (phi)
        # or the length from A to C (which is 90 - dec_A).
        # But we can use two forms of the spherical law of cosines:
        # 2) cos(90-dec_A) = cos(90-dec_B)*cos(d) + sin(90-dec_B)*sin(d)*cos(phi)
        # 3) cos(phi) = -cos(theta)*cos(ra_B-ra_A) + sin(theta)*sin(ra_B-ra_A)*cos(90-dec_A)
        # then replace cos(phi) in 2 with 3 and rearrange:
        x1 = np.sin(coords.dec) * np.cos(d)
        x2 = np.cos(coords.dec) * np.sin(d) * np.cos(theta) * np.cos(coords.ra - new_ra)
        y = np.cos(coords.dec) * np.sin(d) * np.sin(theta) * np.sin(coords.ra - new_ra)
        new_dec = np.arcsin((x1 - x2) / (1 - y))
        # That's it, we have the coordinates of point A.
        new_coords = SkyCoord(new_ra, new_dec)
        return new_coords

    @property
    def target_distance(self):
        """Return the distance from the current position to the current target."""
        if self.current_position is None:
            return None
        if self.target is None:
            return None
        return self.current_position.separation(self.target).deg

    def _within_limits(self, coords):
        """Check if the given coordinates are within the mount limits."""
        if isinstance(coords, SkyCoord):
            coords_altaz = coords.transform_to(AltAz(obstime=Time.now(), location=self.location))
        elif isinstance(coords, AltAz):
            coords_altaz = coords
            coords = SkyCoord(coords_altaz).transform_to('icrs')
        else:
            raise ValueError('Coordinates must be an astropy `SkyCoord` or `AltAz` object')

        # Check if position is above horizon
        if coords_altaz.alt.deg < params.MIN_ELEVATION:
            msg = f'Target alt ({coords_altaz.alt.deg:.1f} deg)'
            msg += f' is below limit ({params.MIN_ELEVATION:.1f} deg)'
            msg += ', cannot slew'
            raise ValueError(msg)
        # Check if position is within hour angle limits
        coords_hadec = coords.transform_to(HADec(obstime=Time.now(), location=self.location))
        if abs(coords_hadec.ha.hour) > params.MAX_HOURANGLE:
            msg = f'Target hour angle ({coords_hadec.ha.hour:.1f}h)'
            msg += f' is outside limit (±{params.MAX_HOURANGLE:.1f}h)'
            msg += ', cannot slew'
            raise ValueError(msg)
        return

    def _pos_str(self, coords=None):
        """Return a simple string reporting the given position, or the current position if None."""
        if coords is None:
            coords = self.current_position
        if coords is None:
            return None
        if isinstance(coords, SkyCoord):
            coords_altaz = coords.transform_to(AltAz(obstime=Time.now(), location=self.location))
        elif isinstance(coords, AltAz):
            coords_altaz = coords
            coords = SkyCoord(coords_altaz).transform_to('icrs')
        pos_str = '{:.4f} {:.4f} ({:.2f} {:.2f})'.format(
            coords.ra.deg,
            coords.dec.deg,
            coords_altaz.alt.deg,
            coords_altaz.az.deg,
        )
        return pos_str

    # Control functions
    def set_target(self, coords):
        """Set the target position to the given coordinates (either ra/dec or alt/az)."""
        # Check input
        if not isinstance(coords, (SkyCoord, AltAz)):
            raise ValueError('Coordinates must be an astropy `SkyCoord` or `AltAz` object')
        # NB We don't check if the target is within limits here, only when slewing

        self.target = coords
        self.log.info(f'Set target to {self._pos_str(coords)} ({coords.__class__.__name__})')
        return 'Setting target'

    def clear_target(self):
        """Clear any stored target."""
        if self.target is not None:
            self.target = None
            self.log.info('Cleared target')
            return 'Cleared target'

    def slew(self, coords=None):
        """Slew to specified coordinates (either ra/dec or alt/az).

        If coordinates are not given, slew to the saved target (if there is one).
        """
        # Check input
        if coords is None:
            if self.target is not None:
                coords = self.target
            else:
                raise ValueError('No coordinates given, and target not set')
        if not isinstance(coords, (SkyCoord, AltAz)):
            raise ValueError('Coordinates must be an astropy `SkyCoord` or `AltAz` object')

        # Set the target (even if we can't slew to it right now)
        self.set_target(coords)

        # Check if target is within the limits
        if not isinstance(coords, AltAz):
            coords_altaz = coords.transform_to(AltAz(obstime=Time.now(), location=self.location))
        else:
            coords_altaz = coords
            coords = SkyCoord(coords_altaz).transform_to('icrs')
        try:
            self._within_limits(self.current_position)
        except Exception:
            raise

        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise errors.HardwareStatusError('Already slewing')
        elif self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked, need to unpark before slewing')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise errors.HardwareStatusError('Mount is in blinky mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise errors.HardwareStatusError('Mount motors are powered off')

        # Set flag
        self.force_check_flag = True
        self.slew_flag = 1

        return f'Slewing to coordinates ({self.target_distance:.2f} deg)'

    def track(self):
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
        try:
            self._within_limits(self.current_position)
        except Exception:
            raise

        # Set flag
        self.force_check_flag = True
        self.track_flag = 1

        return 'Started tracking'

    def halt(self):
        """Stop the mount moving (slewing or tracking)."""
        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Stopped':
            return 'Already stopped'
        elif self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked')

        # Set flag
        self.force_check_flag = True
        self.halt_flag = 1

        return 'Stopping mount'

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
            self.halt_flag = 1
            time.sleep(0.2)

        # If we are parked then we need to turn off blinky mode or turn on the motors
        if self.info['status'] == 'Parked':
            if isinstance(self.mount, SiTech):
                self.set_blinky = False
                self.set_blinky_flag = 1
            elif isinstance(self.mount, (DDM500, FakeDDM500)):
                self.set_motor_power = True
                self.set_motor_power_flag = 1
            time.sleep(0.2)

        # Set flag
        self.force_check_flag = True
        self.unpark_flag = 1

        return 'Unparking mount'

    def offset(self, direction, distance):
        """Offset in a specified (cardinal) direction by the given distance."""
        # Check input
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError(f'Invalid direction "{direction}" (should be [N,E,S,W])')

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
        if not isinstance(self.mount, (DDM500, FakeDDM500)):
            raise NotImplementedError('Only ASA mounts mounts have pulse guiding implemented')

        # Check input
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError(f'Invalid direction "{direction}" (should be [N,E,S,W])')

        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise errors.HardwareStatusError('Already slewing')
        elif self.info['status'] == 'Parked':
            raise errors.HardwareStatusError('Mount is parked')
        elif self.info['status'] == 'Stopped':
            raise errors.HardwareStatusError('Mount is stopped, can only pulse guide when tracking')
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

    def sync_mount(self, coords):
        """Sync the mount to the position."""
        # Check input
        if not isinstance(coords, SkyCoord):
            raise ValueError('Coordinates must be an astropy `SkyCoord` object')

        # Set values
        self.sync_position = coords

        # Set flag
        self.force_check_flag = True
        self.sync_flag = 1

        return 'Syncing position to given coordinates'

    def set_position_offset(self, coords):
        """Set an internal offset using the difference between the given and current positions."""
        # Check input
        if not isinstance(coords, SkyCoord):
            raise ValueError('Coordinates must be an astropy `SkyCoord` object')
        if self.position_offset is not None:
            raise ValueError('An offset is already set, clear it before setting a new one')

        # Get difference between current and given positions
        distance = coords.separation(self.current_position).deg
        angle = coords.position_angle(self.current_position).deg

        # Set values
        self.position_offset = (distance, angle)
        with open(self.position_offset_file, 'w') as f:
            f.write(f'{distance:f} {angle:f}')

        msg = f'Set internal offset to dist={distance:.2f} deg angle={angle:.2f} deg'
        self.log.info(msg)
        return (msg)

    def clear_position_offset(self):
        """Clear the internal position offset."""
        self.position_offset = None
        with open(self.position_offset_file, 'w') as f:
            f.write('0 0')

        self.log.info('Cleared internal position offset')
        return 'Cleared internal position offset'

    def set_trackrate(self, ra_rate=0, dec_rate=0):
        """Set tracking rate in RA and Dec in arcseconds per second (0=default)."""
        if isinstance(self.mount, (DDM500, FakeDDM500)):
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
        self.set_blinky_flag = 1

        if activate:
            s = 'Turning on blinky mode'
        else:
            s = 'Turning off blinky mode'
        return s

    def power_motors(self, activate):
        """Turn on or off the mount motors."""
        if not isinstance(self.mount, (DDM500, FakeDDM500)):
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

    def clear_error(self):
        """Clear any mount errors."""
        if not isinstance(self.mount, DDM500):
            raise NotImplementedError('Only ASA mounts allow errors to be cleared')

        self.log.info('Clearing mount error')
        self.log.debug(f'Current error: "{self.info["error_status"]}"')
        c = self.mount.clear_error()
        if c:
            self.log.info(c)
        self.clear_error_flag = 1

        return 'Cleared any errors'


if __name__ == '__main__':
    with make_pid_file('mnt'):
        MntDaemon()._run()
