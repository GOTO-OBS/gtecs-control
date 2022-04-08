"""Classes to control ASA mounts using the Python interface to ASASDK."""

import logging
import time

from asa import ASAMount

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

from ...astronomy import apparent_to_j2000, j2000_to_apparent


class DDM500:
    """ASA mount control class using the ASASDK C++ package.

    Parameters
    ----------
    address : str
        Mount server IP
    port : int
        Mount server port

    log : logger, optional
        logger to log to
        default = None
    log_debug : bool, optional
        log debug strings?
        default = False

    """

    def __init__(self, address, port, log=None, log_debug=False):
        self.address = address
        self.port = port
        self.buffer_size = 1024

        self._status_update_time = 0

        # Create a logger if one isn't given
        if log is None:
            logging.basicConfig(level=logging.INFO)
            log = logging.getLogger('mount')
            log.setLevel(level=logging.DEBUG)
        self.log = log
        self.log_debug = log_debug

        # Create mount class
        self.mount = ASAMount(address, port)

        # Connect to the mount server and device
        self.mount.tcp_connect()
        if not self.mount.tcp_connected():
            raise ValueError('Could not connect to mount at {}:{}'.format(self.address, self.port))
        self.connect()

        # Update status when starting
        self._update_status()

    def __del__(self):
        try:
            # self.disconnect()
            self.mount.tcp_disconnect()
        except OSError:
            pass

    def connect(self):
        """Connect to the mount device."""
        if not self.mount.device_connected():
            self.mount.device_connect()

    @property
    def connected(self):
        """Check connection to the mount device."""
        return self.mount.tcp_connected() and self.mount.device_connected()

    def disconnect(self):
        """Disconnect from the mount device."""
        self.mount.device_disconnect()

    def _update_status(self):
        """Read and store status values."""
        # Only update if we need to, to save sending multiple commands
        if (time.time() - self._status_update_time) > 0.5:
            # Get main status
            status_dict = self.mount.get_status()
            self._status_dict = status_dict
            self._jd = status_dict['UTC'][0]
            self._sidereal_time = status_dict['LAST'][0]
            self._ra_jnow = status_dict['RightAscension']
            self._dec_jnow = status_dict['Declination']
            # Need to "uncook" from apparent to J2000
            ra_j2000, dec_j2000 = apparent_to_j2000(self._ra_jnow * 360 / 24,
                                                    self._dec_jnow,
                                                    self._jd)
            self._ra = ra_j2000 * 24 / 360
            if self._ra >= 24:
                self._ra -= 24
            self._dec = dec_j2000
            self._az = status_dict['Azimuth']
            self._alt = status_dict['Elevation']
            self._slewing = status_dict['Slewing']
            self._tracking = status_dict['Tracking']
            self._initializing = status_dict['Initializing']
            self._motors_started = status_dict['MotorsStarted']

            self._position_error = {'ra': status_dict['PositionErrorC'][0],
                                    'dec': status_dict['PositionErrorC'][1]}
            self._tracking_error = {'ra': status_dict['TrackingError'][0],
                                    'dec': status_dict['TrackingError'][1]}
            self._velocity = {'ra': status_dict['Velocity'][0],
                              'dec': status_dict['Velocity'][1]}
            self._acceleration = {'ra': status_dict['Acceleration'][0],
                                  'dec': status_dict['Acceleration'][1]}
            self._current = {'ra': status_dict['CurrentQ'][0],
                             'dec': status_dict['CurrentQ'][1]}
            self._tracking_rate = {'ra': self.mount.get_rate_primary_axis(),
                                   'dec': self.mount.get_rate_secondary_axis()}

            # Get other properties
            self._parked = self.mount.at_park()

            # store update time
            self._status_update_time = time.time()

    @property
    def jd(self):
        """Return current Julian Date."""
        self._update_status()
        return self._jd

    @property
    def sidereal_time(self):
        """Return the current sidereal time."""
        self._update_status()
        return self._sidereal_time

    @property
    def status(self):
        """Return the current mount status."""
        self._update_status()
        if not self.connected:
            status = 'CONNECTION ERROR'
        elif not self._motors_started:
            status = 'MOTORS OFF'
        elif self._parked:
            status = 'Parked'
        elif self._slewing:
            status = 'Slewing'
        elif self._tracking:
            status = 'Tracking'
        else:
            status = 'Stopped'
        return status

    @property
    def tracking(self):
        """Return if the mount is currently tracking."""
        self._update_status()
        return self._tracking

    @property
    def nonsidereal(self):
        """Return if the mount has a non-sidereal tracking rate set."""
        return self.tracking_rate['ra'] != 0 or self.tracking_rate['dec'] != 0

    @property
    def slewing(self):
        """Return if the mount is currently slewing."""
        self._update_status()
        return self._slewing

    # @property
    # def parking(self):
    #     """Return if the mount is currently parking."""
    #     self._update_status()
    #     return self._parking

    @property
    def parked(self):
        """Return if the mount is currently parked."""
        self._update_status()
        return self._parked

    @property
    def motors_on(self):
        """Return if the mount motors are currently on."""
        self._update_status()
        return self._motors_started

    @property
    def ra(self):
        """Return the current pointing RA."""
        self._update_status()
        return self._ra

    @property
    def dec(self):
        """Return the current pointing Dec."""
        self._update_status()
        return self._dec

    @property
    def alt(self):
        """Return the current altitude."""
        self._update_status()
        return self._alt

    @property
    def az(self):
        """Return the current azimuth."""
        self._update_status()
        return self._az

    @property
    def position_error(self):
        """Return the current position error."""
        self._update_status()
        return self._position_error

    @property
    def tracking_error(self):
        """Return the current tracking error."""
        self._update_status()
        return self._tracking_error

    @property
    def motor_current(self):
        """Return the current motor current."""
        self._update_status()
        return self._current

    @property
    def tracking_rate(self):
        """Return the current tracking rate."""
        self._update_status()
        return self._tracking_rate

    def slew_to_radec(self, ra, dec, ra_rate=None, dec_rate=None, set_target=True):
        """Slew to given RA and Dec coordinates (J2000), and set tracking rate (arcseconds/sec)."""
        if set_target:
            self.target_radec = (ra, dec)

        # first need to "cook" the coordinates into apparent
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log and self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        if ra_rate is None:
            ra_rate = 0
        if dec_rate is None:
            dec_rate = 0

        return self.mount.slew_to_star_async(ra_jnow, dec_jnow, ra_rate, dec_rate)

    def slew_to_altaz(self, alt, az, set_target=True):
        """Slew mount to given Alt/Az."""
        if set_target:
            self.target_altaz = (alt, az)

        return self.mount.slew_to_azele_async(az, alt)

    def sync_radec(self, ra, dec):
        """Set current pointing to given RA and Dec coordinates (in J2000)."""
        # first need to "cook" the coordinates into apparent
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log and self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        return self.mount.sync_to_coordinates(ra, dec)

    def track(self):
        """Start tracking at the siderial rate."""
        return self.mount.tracking(True)

    def park(self):
        """Move mount to park position."""
        return self.mount.slew_to_park_async()

    def unpark(self):
        """Unpark the mount so it can accept slew commands."""
        return self.mount.unpark()

    def halt(self):
        """Abort slew (if slewing) and stop tracking (if tracking)."""
        return self.mount.abort_slew()

    def start_motors(self):
        """Start the mount motors."""
        return self.mount.start_motors()

    def stop_motors(self):
        """Stop the mount motors."""
        return self.mount.stop_motors()

    def set_motor_power(self, activate):
        """Turn the mount motors on or off."""
        if activate:
            return self.start_motors()
        else:
            return self.stop_motors()

    def offset(self, direction, distance):
        """Set offset in the given direction by the given distance (in arcsec)."""
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError('Invalid direction "{}" (should be [N,E,S,W])'.format(direction))
        if not self.tracking:
            raise ValueError('Can only offset when tracking')
        angle = {'N': 0, 'E': 90, 'S': 180, 'W': 270}
        old_coord = SkyCoord(self.ra * u.hourangle, self.dec * u.deg)
        new_coord = old_coord.directional_offset_by(angle[direction] * u.deg, distance * u.arcsec)
        self.slew_to_radec(new_coord.ra.hourangle, new_coord.dec.deg, set_target=False)

    def error_check(self):
        """Check for any errors raised by the mount."""
        error_raised = self.mount.error_raised()
        if error_raised:
            return error_raised

    def warning_check(self):
        """Check for any warnings raised by the mount."""
        warning_raised = self.mount.warning_raised()
        if warning_raised:
            return warning_raised
