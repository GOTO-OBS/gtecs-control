"""Classes to control SiTechExe."""

import socket
import threading
import time

from astropy.time import Time

from ... import params
from ...astronomy import apparent_to_j2000, j2000_to_apparent


class SiTech(object):
    """SiTech servo controller class using TCP/IP commands."""

    def __init__(self, address, port, log=None, log_debug=False):
        self.address = address
        self.port = port
        self.buffer_size = 1024
        self.commands = {'GET_STATUS': 'ReadScopeStatus\n',
                         'GET_DESTINATION': 'ReadScopeDestination\n',
                         'SLEW_RADEC': 'GoTo {:.5f} {:.5f}\n',
                         'SLEW_RADEC_J2K': 'GoTo {:.5f} {:.5f} J2K\n',
                         'SLEW_ALTAZ': 'GoToAltAz {:.5f} {:.5f}\n',
                         'SYNC_RADEC': 'Sync {:.5f} {:.5f}\n',
                         'SYNC_RADEC_J2K': 'Sync {:.5f} {:.5f} J2K\n',
                         'SYNC_ALTAZ': 'SyncToAltAz {:.5f} {:.5f}\n',
                         'PARK': 'Park\n',
                         'UNPARK': 'UnPark\n',
                         'HALT': 'Abort\n',
                         'SET_TRACKMODE': 'SetTrackMode {:d} {:d} {:.5f} {:.5f}\n',
                         'PULSEGUIDE': 'PulseGuide {:d} {:d}\n',
                         'OFFSET': 'JogArcSeconds {} {:.5f}\n',
                         'BLINKY_ON': 'MotorsToBlinky\n',
                         'BLINKY_OFF': 'MotorsToAuto\n',
                         'J2K_TO_JNOW': 'CookCoordinates {:.5f} {:.5f}\n',
                         'JNOW_TO_J2K': 'UnCookCoordinates {:.5f} {:.5f}\n',
                         'CLOSE': 'CloseMe\n',
                         }
        self._status_update_time = 0

        self.log = log
        self.log_debug = log_debug

        # Create one persistent socket
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(5)
        self.socket.connect((self.address, self.port))
        self.thread_lock = threading.Lock()
        # Update status when starting
        self._update_status()

    def __del__(self):
        try:
            self.socket.send(self.commands['CLOSE'].encode())  # no reply
            self.socket.shutdown(socket.SHUT_RDWR)
            self.socket.close()
        except OSError:
            pass

    def _tcp_command(self, command_str):
        """Send a command string to the device, then fetch the reply and return it as a string."""
        try:
            if self.log and self.log_debug:
                self.log.debug('SEND:"{}"'.format(command_str[:-1]))
            with self.thread_lock:
                self.socket.send(command_str.encode())
                reply = self.socket.recv(self.buffer_size)
            if self.log and self.log_debug:
                self.log.debug('RECV:"{}"'.format(reply.decode()[:-1]))
            return reply.decode()
        except Exception as error:
            return 'SiTech socket error: {}'.format(error)

    def _parse_reply_string(self, reply_string):
        """Parse the return string from a SiTech command.

        The status  values are saved on on the SiTech object, and any attached
        message is returned.

        Returns None if there is no message,
        e.g. from just reading the status rather than sending a command.
        """
        # store update time
        self._status_update_time = time.time()

        # save reply string
        self._reply_string = reply_string

        # split the parameters from the reply string
        reply = reply_string.split(';')

        # a quick check
        if not len(reply) == 12:
            raise ValueError('Invalid SiTech return string: {}'.format(reply))

        # the message should be the last entry
        message = reply[-1][1:-1]  # strip leading '_' and trailing '\n'

        # parse boolean flags
        bools = int(reply[0])
        self._initialized = (bools & 1) > 0
        self._tracking = (bools & 2) > 0
        self._slewing = (bools & 4) > 0
        self._parking = (bools & 8) > 0
        self._parked = (bools & 16) > 0
        self._direction = 'east' if (bools & 32) > 0 else 'west'
        self._blinky = (bools & 64) > 0
        self._connection_error = (bools & 128) > 0
        self._limit_switches = {'primary_plus': (bools & 256) > 0,
                                'primary_minus': (bools & 512) > 0,
                                'secondary_plus': (bools & 1024) > 0,
                                'secondary_minus': (bools & 2048) > 0,
                                }
        self._homing_switches = {'primary': (bools & 4096) > 0,
                                 'secondary': (bools & 8192) > 0,
                                 }
        self._rotator_pos = (bools & 16384) > 0
        self._tracking_nonsidereal = (bools & 32768) > 0
        self._tracking_satellite = (bools & 32768) > 0

        # parse values
        self._ra_jnow = float(reply[1])
        self._dec_jnow = float(reply[2])
        self._alt = float(reply[3])
        self._az = float(reply[4])
        if message == 'ReadScopeDestination':
            self._dest_ra_jnow = float(reply[5])
            self._dest_dec_jnow = float(reply[6])
            self._dest_alt = float(reply[7])
            self._dest_az = float(reply[8])
        else:
            self._secondary_angle = float(reply[5])
            self._primary_angle = float(reply[6])
            self._sidereal_time = float(reply[7])
            self._jd = float(reply[8])
        self._hours = float(reply[9])
        self._airmass = float(reply[10])

        # need to "uncook" the SiTech coordinates into J2000
        if self._ra_jnow >= 24:  # fix for RA
            self._ra_jnow -= 24
        ra_j2000, dec_j2000 = apparent_to_j2000(self._ra_jnow * 360 / 24, self._dec_jnow, self._jd)
        self._ra = ra_j2000 * 24 / 360
        if self._ra >= 24:
            self._ra -= 24
        self._dec = dec_j2000
        if self.log and self.log_debug:
            self.log.debug('Uncooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                self._ra_jnow, self._dec_jnow, self._ra, self._dec))

        if len(message) == 0:
            return None
        else:
            return message

    def _update_status(self):
        """Read and store status values."""
        # Only update if we need to, to save sending multiple commands
        if (time.time() - self._status_update_time) > 0.5:
            reply_string = self._tcp_command(self.commands['GET_STATUS'])
            self._parse_reply_string(reply_string)
            reply_string = self._tcp_command(self.commands['GET_DESTINATION'])
            self._parse_reply_string(reply_string)

    @property
    def status(self):
        """Return the current mount status."""
        self._update_status()
        if self._connection_error and not params.FAKE_MOUNT:
            status = 'CONNECTION ERROR'
        elif self._parked:
            status = 'Parked'
        elif self._blinky:
            status = 'IN BLINKY MODE'
        elif self._slewing:
            status = 'Slewing'
        elif self._tracking:
            status = 'Tracking'
        elif self._parking:
            status = 'Parking'
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
        self._update_status()
        return self._tracking_nonsidereal

    @property
    def slewing(self):
        """Return if the mount is currently slewing."""
        self._update_status()
        return self._slewing

    @property
    def parking(self):
        """Return if the mount is currently parking."""
        self._update_status()
        return self._parking

    @property
    def parked(self):
        """Return if the mount is currently parked."""
        self._update_status()
        return self._parked

    @property
    def direction(self):
        """Return the direction the mount is pointing."""
        self._update_status()
        return self._direction

    @property
    def blinky(self):
        """Return if the mount is currently in blinky mode."""
        self._update_status()
        return self._blinky

    @property
    def connection_error(self):
        """Return if there is an error connecting to the mount."""
        self._update_status()
        return self._connection_error

    @property
    def limit_switches(self):
        """Return if the mount limit switches have been triggered."""
        self._update_status()
        return self._limit_switches

    @property
    def homing_switches(self):
        """Return if the mount homing switches have been triggered."""
        self._update_status()
        return self._homing_switches

    @property
    def ra(self):
        """Return the current RA (J2000)."""
        self._update_status()
        return self._ra

    @property
    def dec(self):
        """Return the current Dec (J2000)."""
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
    def secondary_angle(self):
        """Return the current secondary axis angle."""
        self._update_status()
        return self._secondary_angle

    @property
    def primary_angle(self):
        """Return the current primary axis angle."""
        self._update_status()
        return self._primary_angle

    @property
    def sidereal_time(self):
        """Return the current sidereal time."""
        self._update_status()
        return self._sidereal_time

    @property
    def jd(self):
        """Return the current Julian date."""
        self._update_status()
        return self._jd

    @property
    def hours(self):
        """Return the current hours number."""
        self._update_status()
        return self._hours

    def slew_to_radec(self, ra, dec):
        """Slew to given RA and Dec coordinates (in J2000)."""
        self.target_radec = (ra, dec)

        # first need to "cook" the coordinates into SiTech's JNow
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log and self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        command = self.commands['SLEW_RADEC'].format(float(ra_jnow), float(dec_jnow))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def slew_to_altaz(self, alt, az):
        """Slew mount to given Alt/Az."""
        self.target_altaz = (alt, az)

        # NB SiTech takes Az first, then Alt
        command = self.commands['SLEW_ALTAZ'].format(float(az), float(alt))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def sync_radec(self, ra, dec):
        """Set current pointing to given RA and Dec coordinates (in J2000)."""
        # first need to "cook" the coordinates into SiTech's JNow
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log and self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        command = self.commands['SYNC_RADEC'].format(float(ra_jnow), float(dec_jnow))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def sync_altaz(self, alt, az):
        """Set current pointing to given Alt/Az."""
        # NB SiTech takes Az first, then Alt
        command = self.commands['SYNC_ALTAZ'].format(float(az), float(alt))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def track(self):
        """Start tracking at the siderial rate."""
        command = self.commands['SET_TRACKMODE'].format(1, 0, 0, 0)
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def park(self):
        """Move mount to park position."""
        command = self.commands['PARK']
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def unpark(self):
        """Unpark the mount so it can accept slew commands."""
        command = self.commands['UNPARK']
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def halt(self):
        """Abort slew (if slewing) and stop tracking (if tracking)."""
        command = self.commands['HALT']
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def set_trackrate(self, ra_rate, dec_rate):
        """Set tracking rate in RA and Dec in arcseconds per second.

        If both RA and Dec are 0.0 then tracking will be (re)set to the siderial rate.
        """
        if ra_rate == 0 and dec_rate == 0:
            command = self.commands['SET_TRACKMODE'].format(1, 0, 0, 0)
        else:
            command = self.commands['SET_TRACKMODE'].format(1, 1, float(ra_rate), float(dec_rate))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def set_blinky_mode(self, activate):
        """Activate or deactivate "blinky" (manual) mode,cutting power to the motors."""
        if activate:
            command = self.commands['BLINKY_ON']
        else:
            command = self.commands['BLINKY_OFF']
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def offset(self, direction, distance):
        """Set offset in the given direction by the given distance (in arcsec)."""
        if direction.upper() not in ['N', 'E', 'S', 'W']:
            raise ValueError('Invalid direction "{}" (should be [N,E,S,W])'.format(direction))
        command = self.commands['OFFSET'].format(direction.upper(), distance)
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message
