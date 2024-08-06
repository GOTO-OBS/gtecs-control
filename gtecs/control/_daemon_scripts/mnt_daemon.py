#!/usr/bin/env python3
"""Daemon to access mount control."""

import os
import threading
import time

import astropy.units as u
from astropy.coordinates import AltAz, Angle, HADec, SkyCoord
from astropy.time import Time

from gtecs.common.style import rtxt, ytxt
from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.astronomy import (get_moon_distance, get_moon_params, get_sunalt,
                                     observatory_location)
from gtecs.control.daemons import BaseDaemon, HardwareError
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
        self.history = {}

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

                # Check if the mount has passed the limits and should stop
                # This is a nice idea, but including the Slewing status means we can never get
                # out if it triggers. You'd need to be able to detect if it's moving towards
                # or away from the limit, and that's tricky.
                # It's also probably better to try and have it move out itself, and for the encoder
                # limits it's safer if it completes a flip than we stop when it's vertical.
                # For now we can log if it's past the limits but not try to stop.
                self._limit_check(force_stop=False)

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
                        reply = self.mount.slew_to_radec(coord.ra.hourangle, coord.dec.deg)
                    elif isinstance(coord, AltAz):
                        reply = self.mount.slew_to_altaz(coord.alt.deg, coord.az.deg)
                    if reply:
                        self.log.info(reply)
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
                    reply = self.mount.track()
                    if reply:
                        self.log.info(reply)
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
                    reply = self.mount.halt()
                    if reply:
                        self.log.info(reply)
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
                    reply = self.mount.park()
                    if reply:
                        self.log.info(reply)
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
                    reply = self.mount.unpark()
                    if reply:
                        self.log.info(reply)
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
                    reply = self.mount.offset(self.offset_direction, self.offset_distance)
                    if reply:
                        self.log.info(reply)
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
                    reply = self.mount.pulse_guide(self.guide_direction, self.guide_duration)
                    if reply:
                        self.log.info(reply)
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
                    reply = self.mount.sync_radec(self.sync_ra, self.sync_dec)
                    if reply:
                        self.log.info(reply)
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

    # Internal functions
    def _connect(self):
        """Connect to hardware.

        If the connection fails the hardware will be added to the bad_hardware list,
        which will trigger a hardware_error.
        """
        if self.mount is not None:
            # Already connected
            return

        if params.FAKE_MOUNT:
            self.log.info('Creating Mount simulator')
            self.mount = FakeDDM500(
                params.MOUNT_HOST,
                params.MOUNT_PORT,
                log=self.log,
                log_debug=params.MOUNT_DEBUG,
            )
            return

        if params.MOUNT_CLASS == 'SITECH':
            try:
                self.log.info('Connecting to SiTech')
                self.mount = SiTech(
                    params.MOUNT_HOST,
                    params.MOUNT_PORT,
                    log=self.log,
                    log_debug=params.MOUNT_DEBUG,
                )

                # Connection successful
                self.log.info('Connected to SiTech')
                if 'sitech' in self.bad_hardware:
                    self.bad_hardware.remove('sitech')

            except Exception:
                # Connection failed
                self.mount = None
                if 'sitech' not in self.bad_hardware:
                    self.log.error('Failed to connect to SiTech')
                    self.bad_hardware.add('sitech')

        elif params.MOUNT_CLASS == 'ASA':
            try:
                self.log.info('Connecting to AutoSlew')
                self.mount = DDM500(
                    params.MOUNT_HOST,
                    params.MOUNT_PORT,
                    fake_parking=params.FAKE_MOUNT_PARKING,
                    force_pier_side=params.FORCE_MOUNT_PIER_SIDE,
                    report_extra=True,
                    report_history_limit=params.MOUNT_HISTORY_PERIOD,
                    log=self.log,
                    log_debug=params.MOUNT_DEBUG,
                )
                # Connection successful
                self.log.info('Connected to AutoSlew')
                if 'autoslew' in self.bad_hardware:
                    self.bad_hardware.remove('autoslew')

            except Exception:
                # Connection failed
                self.mount = None
                if 'autoslew' not in self.bad_hardware:
                    self.log.error('Failed to connect to AutoSlew')
                    self.bad_hardware.add('autoslew')

    def _get_info(self):
        """Get the latest status info from the hardware.

        This function will check if any piece of hardware is not responding and save it to
        the bad_hardware list if so, which will trigger a hardware_error.
        """
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

            # Check if the mount is within the position limits
            temp_info['min_elevation'] = params.MIN_ELEVATION
            temp_info['max_hourangle'] = params.MAX_HOURANGLE
            within_elevation = temp_info['mount_alt_pointing'] > temp_info['min_elevation']
            within_hourangle = abs(temp_info['mount_ha_pointing']) < temp_info['max_hourangle']
            temp_info['elevation_within_limits'] = bool(within_elevation)  # needs to be bool,
            temp_info['hourangle_within_limits'] = bool(within_hourangle)  # not np.bool_

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
                temp_info['tracking_rate'] = self.mount.tracking_rate
                temp_info['motors_on'] = self.mount.motors_on
                temp_info['pier_side'] = self.mount.pier_side
                if params.FORCE_MOUNT_PIER_SIDE in [0, 1]:
                    temp_info['target_pier_side'] = params.FORCE_MOUNT_PIER_SIDE
                else:
                    temp_info['target_pier_side'] = None

                # Extra info from the report command
                temp_info['encoder_position'] = self.mount.encoder_position
                temp_info['position_error'] = self.mount.position_error
                temp_info['tracking_error'] = self.mount.tracking_error
                temp_info['velocity'] = self.mount.velocity
                temp_info['acceleration'] = self.mount.acceleration
                temp_info['motor_current'] = self.mount.motor_current

                # Save history internally so we can add to the image headers
                self.history['encoder_position'] = self.mount.encoder_position_history
                self.history['position_error'] = self.mount.position_error_history
                self.history['tracking_error'] = self.mount.tracking_error_history
                self.history['velocity'] = self.mount.velocity_history
                self.history['acceleration'] = self.mount.acceleration_history
                self.history['motor_current'] = self.mount.motor_current_history

                # Check if the mount is within the encoder position limits
                temp_info['encoder_position_limits'] = {
                    'ra': (params.ENCODER_RA_MIN, params.ENCODER_RA_MAX),
                    'dec': (params.ENCODER_DEC_MIN, params.ENCODER_DEC_MAX),
                }
                within_ra = self.mount.within_ra_limits(
                    temp_info['encoder_position_limits']['ra'][0],
                    temp_info['encoder_position_limits']['ra'][1]
                )
                within_dec = self.mount.within_dec_limits(
                    temp_info['encoder_position_limits']['dec'][0],
                    temp_info['encoder_position_limits']['dec'][1]
                )
                temp_info['encoder_ra_within_limits'] = within_ra
                temp_info['encoder_dec_within_limits'] = within_dec
                temp_info['encoder_position_within_limits'] = within_ra and within_dec

                # Log any errors or warnings from the mount, along with the time of occurrence
                error_status = self.mount.error_check()
                if isinstance(error_status, str):
                    error_status = error_status.strip().replace('\n', ': ').replace('\r', '')
                if error_status is not None:
                    temp_info['error_status'] = error_status
                    if (not self.info or 'error_status' not in self.info or
                            self.info['error_status'] != error_status):
                        # Log only if it's a new error
                        self.log.error(f'Mount raises error: {error_status}')
                        temp_info['error_status_time'] = self.loop_time
                    else:
                        # Remember the time it began
                        temp_info['error_status_time'] = self.info['error_status_time']
                    if self.clear_error_flag == 1:
                        # We tried to clear, but the error is still there
                        self.log.error(f'Mount raises error: {error_status}, clear failed')
                        self.clear_error_flag = 0
                elif (self.info and 'error_status' in self.info and self.clear_error_flag == 0):
                    # No error, but we want to keep until a new one is raised or we clear it
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
                # Report the connection as failed
                self.mount = None
                if 'sitech' not in self.bad_hardware:
                    self.bad_hardware.add('sitech')
            elif isinstance(self.mount, (DDM500, FakeDDM500)):
                temp_info['class'] = 'ASA'
                temp_info['tracking_rate'] = None
                temp_info['motors_on'] = None
                temp_info['pier_side'] = None
                temp_info['target_pier_side'] = None
                temp_info['encoder_position'] = None
                temp_info['position_error'] = None
                temp_info['tracking_error'] = None
                temp_info['velocity'] = None
                temp_info['acceleration'] = None
                temp_info['motor_current'] = None
                temp_info['encoder_position_limits'] = None
                temp_info['encoder_ra_within_limits'] = None
                temp_info['encoder_dec_within_limits'] = None
                temp_info['encoder_position_within_limits'] = None
                temp_info['error_status'] = None
                temp_info['error_status_time'] = None
                temp_info['warning_status'] = None
                temp_info['warning_status_time'] = None

                # Report the connection as failed
                self.mount = None
                if 'autoslew' not in self.bad_hardware:
                    self.bad_hardware.add('autoslew')

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

    def _limit_check(self, force_stop=True):
        """Check if the mount position is past the valid limits."""
        if self.info['elevation_within_limits'] is False:
            msg = 'Mount alt ({:.1f} deg) is below limit ({:.1f} deg)'.format(
                self.info['mount_alt'], self.info['min_elevation'])
            self.log.error(msg)
            should_stop = True
        if self.info['hourangle_within_limits'] is False:
            msg = 'Mount hour angle ({:.1f}h) is outside limit (±{:.1f}h)'.format(
                self.info['mount_ha'], self.info['max_hourangle'])
            self.log.error(msg)
            should_stop = True
        if self.info['class'] == 'ASA':
            if self.info['encoder_ra_within_limits'] is False:
                msg = 'RA encoder position ({:.1f}) is outside limits ({:.1f},{:.1f})'.format(
                    self.info['encoder_position']['ra'],
                    self.info['encoder_position_limits']['ra'][0],
                    self.info['encoder_position_limits']['ra'][1],
                )
                self.log.error(msg)
                should_stop = True
            if self.info['encoder_dec_within_limits'] is False:
                msg = 'Dec encoder position ({:.1f}) is outside limits ({:.1f},{:.1f})'.format(
                    self.info['encoder_position']['dec'],
                    self.info['encoder_position_limits']['dec'][0],
                    self.info['encoder_position_limits']['dec'][1],
                )
                self.log.error(msg)
                should_stop = True
            if (self.info['target_pier_side'] is not None and
                    self.info['pier_side'] != self.info['target_pier_side']):
                msg = 'Mount pier side ({}) is not the target side ({})'.format(
                    self.info['pier_side'], self.info['target_pier_side'])
                self.log.error(msg)
                should_stop = True

        if force_stop and should_stop:
            # Stop any movement
            if self.info['status'] in ['Tracking', 'Slewing']:
                self.log.warning('Stopping mount')
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

    def check_limits(self, coords):
        """Check if the given coordinates are within the mount limits."""
        if isinstance(coords, SkyCoord):
            coords_altaz = coords.transform_to(AltAz(obstime=Time.now(), location=self.location))
        elif isinstance(coords, AltAz):
            coords_altaz = coords
            coords = SkyCoord(coords_altaz).transform_to('icrs')
        else:
            raise ValueError('Coordinates must be an astropy `SkyCoord` or `AltAz` object')

        # Check if position is above horizon
        if coords_altaz.alt.deg < self.info['min_elevation']:
            msg = 'Target alt ({:.1f} deg) is below limit ({:.1f} deg), cannot slew'.format(
                coords_altaz.alt.deg, self.info['min_elevation'])
            raise HardwareError(msg)
        # Check if position is within hour angle limits
        coords_hadec = coords.transform_to(HADec(obstime=Time.now(), location=self.location))
        if abs(coords_hadec.ha.hour) > self.info['max_hourangle']:
            msg = 'Target hour angle ({:.1f}h) is outside limit (±{}h), cannot slew'.format(
                coords_hadec.ha.hour, self.info['max_hourangle'])
            raise HardwareError(msg)
        # Unfortunately we can't check the encoder limits here, there's no function to get the
        # encoder position for a given target.
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
        if not isinstance(coords, (SkyCoord, AltAz)):
            raise ValueError('Coordinates must be an astropy `SkyCoord` or `AltAz` object')
        # NB We don't check if the target is within limits here, only when slewing

        self.target = coords
        self.log.info(f'Set target to {self._pos_str(coords)} ({coords.__class__.__name__})')

    def clear_target(self):
        """Clear any stored target."""
        if self.target is not None:
            self.target = None
            self.log.info('Cleared target')

    def slew(self, coords=None):
        """Slew to specified coordinates (either ra/dec or alt/az).

        If coordinates are not given, slew to the saved target (if there is one).
        """
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
        self.check_limits(coords)

        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise HardwareError('Already slewing')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise HardwareError('Mount is in blinky mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise HardwareError('Mount motors are powered off')

        self.force_check_flag = True
        self.slew_flag = 1

    def track(self):
        """Start the mount tracking."""
        # Check if we're currently within the limits
        self.check_limits(self.current_position)

        # Check current status
        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise HardwareError('Mount is slewing, will track when reached target')
        elif self.info['status'] == 'Parked':
            raise HardwareError('Mount is parked')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise HardwareError('Mount is in blinky mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise HardwareError('Mount motors are powered off')

        if self.info['status'] != 'Tracking':
            self.force_check_flag = True
            self.track_flag = 1

    def halt(self):
        """Stop the mount moving (slewing or tracking)."""
        self.wait_for_info()
        if self.info['status'] == 'Parked':
            raise HardwareError('Mount is parked')

        if self.info['status'] != 'Stopped':
            self.force_check_flag = True
            self.halt_flag = 1

    def park(self):
        """Move the mount to the park position."""
        self.wait_for_info()
        if self.info['status'] == 'IN BLINKY MODE':
            raise HardwareError('Mount is in Blinky Mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise HardwareError('Mount motors are powered off')

        if self.info['status'] not in ['Parked', 'Parking']:
            self.force_check_flag = True
            self.park_flag = 1

    def unpark(self):
        """Unpark the mount."""
        self.wait_for_info()
        if self.info['status'] == 'Parking':
            self.halt_flag = 1
            time.sleep(0.2)
        if self.info['status'] == 'Parked':
            if isinstance(self.mount, SiTech):
                self.set_blinky = False
                self.set_blinky_flag = 1
            elif isinstance(self.mount, (DDM500, FakeDDM500)):
                self.set_motor_power = True
                self.set_motor_power_flag = 1
            time.sleep(0.2)
            self.force_check_flag = True
            self.unpark_flag = 1

    def offset(self, direction, distance):
        """Offset in a specified (cardinal) direction by the given distance."""
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError(f'Invalid direction "{direction}" (should be [N,E,S,W])')

        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise HardwareError('Already slewing')
        elif self.info['status'] == 'Parked':
            raise HardwareError('Mount is parked')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise HardwareError('Mount is in Blinky Mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise HardwareError('Mount motors are powered off')

        self.offset_direction = direction
        self.offset_distance = distance
        self.force_check_flag = True
        self.offset_flag = 1

    def pulse_guide(self, direction, duration):
        """Pulse guide in a specified (cardinal) direction for the given time."""
        if not isinstance(self.mount, (DDM500, FakeDDM500)):
            raise NotImplementedError('Only ASA mounts mounts have pulse guiding implemented')
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError(f'Invalid direction "{direction}" (should be [N,E,S,W])')

        self.wait_for_info()
        if self.info['status'] == 'Slewing':
            raise HardwareError('Already slewing')
        elif self.info['status'] == 'Parked':
            raise HardwareError('Mount is parked')
        elif self.info['status'] == 'Stopped':
            raise HardwareError('Mount is stopped, can only pulse guide when tracking')
        elif self.info['status'] == 'IN BLINKY MODE':
            raise HardwareError('Mount is in Blinky Mode, motors disabled')
        elif self.info['status'] == 'MOTORS OFF':
            raise HardwareError('Mount motors are powered off')

        self.guide_direction = direction
        self.guide_duration = duration
        self.force_check_flag = True
        self.guide_flag = 1

    def sync_mount(self, coords):
        """Sync the mount to the position."""
        if not isinstance(coords, SkyCoord):
            raise ValueError('Coordinates must be an astropy `SkyCoord` object')

        self.sync_position = coords
        self.force_check_flag = True
        self.sync_flag = 1

    def set_position_offset(self, coords):
        """Set an internal offset using the difference between the given and current positions."""
        if not isinstance(coords, SkyCoord):
            raise ValueError('Coordinates must be an astropy `SkyCoord` object')
        if self.position_offset is not None:
            raise ValueError('An offset is already set, clear it before setting a new one')

        distance = coords.separation(self.current_position).deg
        angle = coords.position_angle(self.current_position).deg
        self.position_offset = (distance, angle)
        with open(self.position_offset_file, 'w') as f:
            f.write(f'{distance:f} {angle:f}')
        self.log.info(f'Set internal offset to dist={distance:.2f} deg angle={angle:.2f} deg')

    def clear_position_offset(self):
        """Clear the internal position offset."""
        self.position_offset = None
        with open(self.position_offset_file, 'w') as f:
            f.write('0 0')
        self.log.info('Cleared internal position offset')

    def set_trackrate(self, ra_rate=0, dec_rate=0):
        """Set tracking rate in RA and Dec in arcseconds per second (0=default)."""
        if isinstance(self.mount, (DDM500, FakeDDM500)):
            raise NotImplementedError('Mount trackrate command is not implemented')

        self.trackrate_ra = ra_rate
        self.trackrate_dec = dec_rate
        self.force_check_flag = True
        self.set_trackrate_flag = 1

    def blinky(self, command):
        """Turn on or off blinky mode."""
        if not isinstance(self.mount, SiTech):
            raise NotImplementedError('Only SiTech mounts use blinky mode')
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        self.wait_for_info()
        if command == 'on' and not self.mount.blinky:
            self.log.info('Enabling blinky mode')
            self.set_blinky = True
            self.force_check_flag = True
            self.set_blinky_mode_flag = 1
        elif command == 'off' and self.mount.blinky:
            self.log.info('Disabling blinky mode')
            self.set_blinky = False
            self.force_check_flag = True
            self.set_blinky_mode_flag = 1

    def power_motors(self, command):
        """Turn on or off the mount motors."""
        if not isinstance(self.mount, (DDM500, FakeDDM500)):
            raise NotImplementedError('Only ASA mounts allow motors to be powered')
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        self.wait_for_info()
        if command == 'on' and not self.mount.motors_on:
            self.log.info('Turning on mount motors')
            self.set_motor_power = True
            self.force_check_flag = True
            self.set_motor_power_flag = 1
        elif command == 'off' and self.mount.motors_on:
            self.log.info('Turning off mount motors')
            self.set_motor_power = False
            self.force_check_flag = True
            self.set_motor_power_flag = 1

    def clear_error(self):
        """Clear any mount errors."""
        if not isinstance(self.mount, DDM500):
            raise NotImplementedError('Only ASA mounts allow errors to be cleared')

        self.log.info('Clearing mount error')
        self.log.debug(f'Current error: "{self.info["error_status"]}"')
        reply = self.mount.clear_error()
        if reply:
            self.log.info(reply)
        self.clear_error_flag = 1

    def get_history(self):
        """Get the mount history values for the header.

        This was previously part of the usual get_info() function, but the values made the
        dict too long so it was split out.
        """
        return self.history

    # Info function
    def get_info_string(self, verbose=False, force_update=False):
        """Get a string for printing status info."""
        info = self.get_info(force_update)
        if not verbose:
            msg = ''
            status = info['status']
            if status != 'Slewing':
                if status == 'Tracking' and 'nonsidereal' in info and info['nonsidereal']:
                    status += ' (non-sidereal)'
                msg += 'MOUNT ({})        [{}]\n'.format(params.MOUNT_HOST, status)
            else:
                if info['target_dist']:
                    msg += 'MOUNT ({})        Slewing ({:.2f} deg)\n'.format(
                        params.MOUNT_HOST, info['target_dist'])
                else:
                    msg += 'MOUNT ({})        Slewing\n'.format(params.MOUNT_HOST)

            alt, az = info['mount_alt'], info['mount_az']
            ra, dec = info['mount_ra'], info['mount_dec']
            ra_str = Angle(ra * u.hour).to_string(sep=':', precision=1)
            dec_str = Angle(dec * u.deg).to_string(sep=':', precision=1, alwayssign=True)

            msg += '  RA:  {:>11} | {:8.4f} deg    Alt: {:7.3f}\n'.format(
                ra_str, ra * 360 / 24, alt)
            msg += '  Dec: {:>11} | {:8.4f} deg     Az: {:7.3f}\n'.format(
                dec_str, dec, az)

            # Warnings and errors
            if info['error_status'] is not None:
                t = Time(info['error_status_time'], format='unix', precision=0)
                msg += rtxt('ERROR: "{}" (at {})\n'.format(info['error_status'], t.iso))
            if info['warning_status'] is not None:
                t = Time(info['warning_status_time'], format='unix', precision=0)
                msg += ytxt('WARNING: "{}" (at {})\n'.format(info['warning_status'], t.iso))
            if not info['elevation_within_limits']:
                msg += ytxt('WARNING: Alt < {:.1f} deg\n'.format(info['min_elevation']))
            if not info['hourangle_within_limits']:
                msg += ytxt('WARNING: HA > ±{:.1f}h\n'.format(info['max_hourangle']))
            if self.info['class'] == 'ASA':
                if not info['encoder_position_within_limits']:
                    msg += ytxt('WARNING: Mount has exceed encoder limits (may have flipped)\n')
                if (self.info['target_pier_side'] is not None and
                        self.info['pier_side'] != self.info['target_pier_side']):
                    msg += ytxt('WARNING: Pier side ({}) is flipped (target={})\n'.format(
                        self.info['pier_side'], self.info['target_pier_side']))
            if info['moon_dist'] <= 30:
                msg += ytxt('WARNING: Moon dist < 30 deg ({:.2f})\n'.format(
                    info['moon_dist']))
            msg = msg.rstrip()

        else:
            msg = '####### MOUNT INFO ########\n'
            if info['status'] != 'Slewing':
                msg += 'Status: {}\n'.format(info['status'])
            else:
                if info['target_dist']:
                    msg += 'Status: {} ({:.2f} deg)\n'.format(info['status'], info['target_dist'])
                else:
                    msg += 'Status: {}\n'.format(info['status'])
            msg += '~~~~~~~\n'
            ra, dec = info['mount_ra'], info['mount_dec']
            ra_str = Angle(ra * u.hour).to_string(sep=':', precision=1)
            dec_str = Angle(dec * u.deg).to_string(sep=':', precision=1, alwayssign=True)
            msg += 'Telescope RA:      {:>11} / {:8.4f} deg\n'.format(ra_str, ra * 360 / 24)
            msg += 'Telescope Dec:     {:>11} / {:8.4f} deg\n'.format(dec_str, dec)

            if info['target_alt'] is None:
                # Assume RA/Dec target, unless Alt/Az is set
                if info['target_ra'] is not None:
                    ra = info['target_ra']
                    ra_str = Angle(ra * u.hour).to_string(sep=':', precision=1)
                    msg += 'Target RA:         {:>11} / {:8.4f} deg\n'.format(ra_str, ra * 360 / 24)
                else:
                    msg += 'Target RA:         NOT SET\n'
                if info['target_dec'] is not None:
                    dec = info['target_dec']
                    dec_str = Angle(dec * u.deg).to_string(sep=':', precision=1, alwayssign=True)
                    msg += 'Target Dec:        {:>11} / {:8.4f} deg\n'.format(dec_str, dec)
                else:
                    msg += 'Target Dec:        NOT SET\n'

            msg += 'Mount Alt:         {:8.4f} deg\n'.format(info['mount_alt'])
            msg += 'Mount Az:          {:8.4f} deg\n'.format(info['mount_az'])
            if not info['elevation_within_limits']:
                msg += ytxt('  WARNING: Alt < {:.1f} deg\n'.format(info['min_elevation']))

            if info['target_alt'] is not None:
                msg += 'Target Alt:        {:8.4f} deg\n'.format(info['target_alt'])
                msg += 'Target Az:         {:8.4f} deg\n'.format(info['target_az'])

            if info['target_dist'] is not None:
                msg += 'Target distance:   {:8.4f} deg\n'.format(info['target_dist'])
            else:
                msg += 'Target distance:   NO TARGET\n'

            msg += '~~~~~~~\n'
            if info['class'] == 'SITECH':
                if info['trackrate_ra'] == 0:
                    msg += 'RA track rate:     SIDEREAL\n'
                else:
                    msg += 'RA track rate:     {:.2f} arcsec/sec\n'.format(info['trackrate_ra'])
                if info['trackrate_dec'] == 0:
                    msg += 'Dec track rate:    SIDEREAL\n'
                else:
                    msg += 'Dec track rate:    {:.2f} arcsec/sec\n'.format(info['trackrate_dec'])
            elif info['class'] == 'ASA':
                if info['error_status'] is not None:
                    t = Time(info['error_status_time'], format='unix', precision=0)
                    msg += rtxt('ERROR: "{}" (at {})\n'.format(info['error_status'], t.iso))
                if info['warning_status'] is not None:
                    t = Time(info['warning_status_time'], format='unix', precision=0)
                    msg += ytxt('WARNING: "{}" (at {})\n'.format(
                        info['warning_status'], t.iso))

                msg += 'Pier side:         {} ({})\n'.format(
                    'West' if info['pier_side'] == 0 else 'East',  # Use ASA convention
                    info['pier_side'],
                )
                if (self.info['target_pier_side'] is not None and
                        self.info['pier_side'] != self.info['target_pier_side']):
                    msg += ytxt('  WARNING: Pier side ({}) is flipped (target={})\n'.format(
                        self.info['pier_side'], self.info['target_pier_side']))

                if info['tracking_rate']['ra'] == 0:
                    msg += 'RA track rate:     SIDEREAL\n'
                else:
                    msg += 'RA track rate:   {:>+9.4f} arcsec/sec\n'.format(
                        info['tracking_rate']['ra'])
                if info['tracking_rate']['dec'] == 0:
                    msg += 'Dec track rate:    SIDEREAL\n'
                else:
                    msg += 'Dec track rate:  {:>+9.4f} arcsec/sec\n'.format(
                        info['tracking_rate']['dec'])

                msg += 'RA encoder pos:   {:>+9.4f} deg (limits:{:.0f},{:.0f})\n'.format(
                    info['encoder_position']['ra'],
                    info['encoder_position_limits']['ra'][0],
                    info['encoder_position_limits']['ra'][1],
                )
                msg += 'Dec encoder pos:  {:>+9.4f} deg (limits:{:.0f},{:.0f})\n'.format(
                    info['encoder_position']['dec'],
                    info['encoder_position_limits']['dec'][0],
                    info['encoder_position_limits']['dec'][1],
                )
                if not info['encoder_position_within_limits']:
                    msg += ytxt('  WARNING: Mount has exceed encoder limits (may have flipped)\n')

                msg += 'RA position err:  {:>+9.4f} arcsec\n'.format(
                    info['position_error']['ra'])
                msg += 'Dec position err: {:>+9.4f} arcsec\n'.format(
                    info['position_error']['dec'])
                # msg += 'RA tracking err:  {:>+9.4f} arcsec\n'.format(
                #     info['tracking_error']['ra'])
                # msg += 'Dec tracking err: {:>+9.4f} arcsec\n'.format(
                #     info['tracking_error']['dec'])
                msg += 'RA tracking err:        N/A arcsec\n'  # see DDM500._get_report()
                msg += 'Dec tracking err:       N/A arcsec\n'
                msg += 'RA velocity:      {:>+9.4f} arcsec/sec\n'.format(
                    info['velocity']['ra'])
                msg += 'Dec velocity:     {:>+9.4f} arcsec/sec\n'.format(
                    info['velocity']['dec'])
                # msg += 'RA acceleration:  {:>+9.4f} arcsec/sec²\n'.format(
                #     info['acceleration']['ra'])
                # msg += 'Dec acceleration: {:>+9.4f} arcsec/sec²\n'.format(
                #     info['acceleration']['dec'])
                msg += 'RA acceleration:        N/A arcsec/sec²\n'  # see DDM500._get_report()
                msg += 'Dec acceleration:       N/A arcsec/sec²\n'
                msg += 'RA current:         {:>5.2f} A\n'.format(
                    info['motor_current']['ra'])
                msg += 'Dec current:        {:>5.2f} A\n'.format(
                    info['motor_current']['dec'])

            msg += '~~~~~~~\n'
            lst_str = Angle(info['lst'] * u.hourangle).to_string(sep=':', precision=1)
            msg += 'Sidereal Time:     {:>11}\n'.format(lst_str)
            msg += 'Hour Angle:        {:+6.2f} h\n'.format(info['mount_ha'])
            if not info['hourangle_within_limits']:
                msg += ytxt('WARNING: HA > ±{:.1f}h\n'.format(info['max_hourangle']))

            msg += 'Sun alt:           {:+6.2f} deg\n'.format(info['sun_alt'])
            msg += 'Moon alt:          {:+6.2f} deg\n'.format(info['moon_alt'])
            msg += 'Moon illumination: {:>6.1%} ({})\n'.format(info['moon_ill'], info['moon_phase'])
            msg += 'Moon distance:     {:6.2f} deg\n'.format(info['moon_dist'])
            if info['moon_dist'] <= 30:
                msg += ytxt('  WARNING: Moon dist < 30 deg ({:.2f})\n'.format(
                    info['moon_dist']))

            msg += '~~~~~~~\n'
            msg += 'Uptime: {:.1f}s\n'.format(info['uptime'])
            msg += 'Timestamp: {}\n'.format(info['timestamp'])
            msg += '###########################'
        return msg


if __name__ == '__main__':
    daemon = MntDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
