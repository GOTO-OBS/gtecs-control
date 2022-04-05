"""Classes to control ASA mounts using ASCOM Alpaca."""

import json
import logging
import random
import time

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

import requests

from ...astronomy import apparent_to_j2000, j2000_to_apparent


class DDM500:
    """ASA mount control class using ASCOM Alpaca.

    Parameters
    ----------
    address : str
        Mount server IP
    port : int
        Mount server port

    api_version : int, optional
        Alpaca API version number
        default = 1
    device_number : int, optional
        Alpaca device number
        default = 0

    log : logger, optional
        logger to log to
        default = None
    log_debug : bool, optional
        log debug strings?
        default = False

    """

    def __init__(self, address, port, api_version=1, device_number=0, log=None, log_debug=False):
        self.address = address
        self.port = port
        self.api_version = api_version
        self.device_number = device_number
        self.base_url = f'http://{address}:{port}/api/v{api_version}/telescope/{device_number}/'
        self.client_id = random.randint(0, 2**32)
        self.transaction_count = 0

        self._status_update_time = 0

        # Create a logger if one isn't given
        if log is None:
            logging.basicConfig(level=logging.INFO)
            log = logging.getLogger('mount')
            log.setLevel(level=logging.DEBUG)
        self.log = log
        self.log_debug = log_debug

        # Connect to the mount
        self.connect()

        # Update status when starting
        self._update_status()

    def _http_request(self, cmd, command_str, data=None):
        """Send a request to the device, then parse and return the reply."""
        try:
            if data is None:
                data = {}

            # Add recommended IDs
            data['ClientID'] = self.client_id
            count = self.transaction_count + 1
            data['ClientTransactionID'] = count
            self.transaction_count = count

            url = self.base_url + command_str
            if self.log and self.log_debug:
                self.log.debug(f'{cmd}:"{url}":{data}')

            if cmd == 'GET':
                # GET commands require params in the URL (no body)
                r = requests.get(url, params=data)
            elif cmd == 'PUT':
                # PUT commands require params in the message body
                r = requests.put(url, data=data)

            reply_str = r.content.decode(r.encoding)
            if self.log and self.log_debug:
                self.log.debug(f'RCV:"{reply_str}"')

            if r.status_code != 200:
                raise ValueError(f'HTTP error {r.status_code}: {reply_str}')
            if ('ClientTransactionID' in reply_str and
                    json.loads(reply_str)['ClientTransactionID'] != count):
                raise ValueError(f'Transaction ID ({count}) mismatch: {reply_str}')

            return self._parse_http_reply(reply_str)
        except Exception:
            self.log.error('Failed to communicate with mount')
            self.log.debug('', exc_info=True)
            raise

    def _http_get(self, command_str, params=None):
        """Send a GET command to the device."""
        return self._http_request('GET', command_str, params)

    def _http_put(self, command_str, params=None):
        """Send a PUT command to the device."""
        return self._http_request('PUT', command_str, params)

    def _parse_http_reply(self, reply_str):
        """Parse the return string from an Alpaca command."""
        try:
            reply = json.loads(reply_str)
        except json.JSONDecodeError:
            raise ValueError('Invalid reply: {}'.format(reply_str))

        # Check for errors
        if 'ErrorNumber' not in reply or 'ErrorMessage' not in reply:
            raise ValueError('Invalid reply: {}'.format(reply_str))
        if reply['ErrorNumber'] != 0:
            raise ValueError(reply['ErrorMessage'])

        if 'Value' in reply:
            # This was a GET command, return value
            return reply['Value']
        else:
            # This was a PUT command, nothing to return
            return True

    def connect(self):
        """Connect to the mount device."""
        self._http_put('connected', {'Connected': True})

    @property
    def connected(self):
        """Check connection to the mount device."""
        return self._http_get('connected')

    def disconnect(self):
        """Disconnect from the mount device."""
        self._http_put('connected', {'Connected': False})

    def _update_status(self):
        """Read and store status values."""
        # Only update if we need to, to save sending multiple commands
        if (time.time() - self._status_update_time) > 0.5:
            self._utc = Time(self._http_get('utcdate'))
            self._jd = self._utc.jd

            self._ra_jnow = self._http_get('rightascension')
            self._dec_jnow = self._http_get('declination')
            ra_j2000, dec_j2000 = apparent_to_j2000(self._ra_jnow * 360 / 24,
                                                    self._dec_jnow,
                                                    self._jd)
            self._ra = ra_j2000 * 24 / 360
            if self._ra >= 24:
                self._ra -= 24
            self._dec = dec_j2000

            # self._targ_ra_jnow = self._http_get('targetrightascension')
            # self._targ_dec_jnow = self._http_get('targetdeclination')
            # targ_ra_j2000, targ_dec_j2000 = apparent_to_j2000(self._targ_ra_jnow * 360 / 24,
            #                                                   self._targ_dec_jnow,
            #                                                   self._jd)
            # self._targ_ra = targ_ra_j2000 * 24 / 360
            # if self._targ_ra >= 24:
            #     self._targ_ra -= 24
            # self._targ_dec = targ_dec_j2000

            self._az = self._http_get('azimuth')
            self._alt = self._http_get('altitude')

            self._slewing = self._http_get('slewing')
            self._tracking = self._http_get('tracking')
            self._guiding = self._http_get('ispulseguiding')
            self._parked = self._http_get('atpark')
            self._motors_on = self._http_put('commandstring',
                                             {'Command': 'MotStat', 'Raw': False}) == 'true'

            # Most of these are not yet implemented
            self._position_error = {'ra': -999,
                                    'dec': -999}
            self._tracking_error = {'ra': -999,
                                    'dec': -999}
            self._velocity = {'ra': -999,
                              'dec': -999}
            self._acceleration = {'ra': -999,
                                  'dec': -999}
            self._current = {'ra': -999,
                             'dec': -999}
            self._tracking_rate = {'ra': self._http_get('rightascensionrate'),
                                   'dec': self._http_get('declinationrate')}

            # store update time
            self._status_update_time = time.time()

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

    @property
    def guiding(self):
        """Return if the mount is currently pulse guiding."""
        self._update_status()
        return self._guiding

    @property
    def parked(self):
        """Return if the mount is currently parked."""
        self._update_status()
        return self._parked

    @property
    def motors_on(self):
        """Return if the mount motors are currently on."""
        self._update_status()
        return self._motors_on

    @property
    def status(self):
        """Return the current mount status."""
        self._update_status()
        if not self.connected:
            status = 'CONNECTION ERROR'
        elif not self.motors_on:
            status = 'MOTORS OFF'
        elif self.parked:
            status = 'Parked'
        elif self.guiding:
            status = 'Guiding'
        elif self.slewing:
            status = 'Slewing'
        elif self.tracking:
            status = 'Tracking'
        else:
            status = 'Stopped'
        return status

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

    def slew_to_radec(self, ra, dec):
        """Slew to given RA and Dec coordinates (J2000)."""
        # first need to "cook" the coordinates into apparent
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log and self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        data_dict = {'RightAscension': ra_jnow, 'Declination': dec_jnow}
        return self._http_put('slewtocoordinatesasync', data_dict)

    def slew_to_altaz(self, alt, az):
        """Slew mount to given Alt/Az."""
        data_dict = {'Azimuth': az, 'Altitude': alt}
        return self._http_put('slewtoaltazasync', data_dict)

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

        data_dict = {'RightAscension': ra_jnow, 'Declination': dec_jnow}
        return self._http_put('synctocoordinates', data_dict)

    def track(self):
        """Start tracking at the siderial rate."""
        return self._http_put('tracking', {'Tracking': True})

    def park(self):
        """Move mount to park position."""
        return self._http_put('park')

    def unpark(self):
        """Unpark the mount so it can accept slew commands."""
        return self._http_put('unpark')

    def halt(self):
        """Abort slew (if slewing) and stop tracking (if tracking)."""
        if self.status == 'Slewing':
            return self._http_put('abortslew')
        elif self.status == 'Tracking':
            return self._http_put('tracking', {'Tracking': False})

    def start_motors(self):
        """Start the mount motors."""
        return self._http_put('action', {'Action': 'MotStat', 'Parameters': 'on'})

    def stop_motors(self):
        """Stop the mount motors."""
        return self._http_put('action', {'Action': 'MotStat', 'Parameters': 'off'})

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
        self.slew_to_radec(new_coord.ra.hourangle, new_coord.dec.deg)

    def error_check(self):
        """Check for any errors raised by the mount."""
        error_raised = self._http_put('action',
                                      {'Action': 'telescope:errorstring', 'Parameters': ''})
        if error_raised:
            return error_raised

    def clear_error(self):
        """Clear for any errors raised by the mount."""
        return self._http_put('action', {'Action': 'telescope:clearerror', 'Parameters': ''})

    # def warning_check(self):
    #     """Check for any warnings raised by the mount."""
    #     warning_raised = self.mount.warning_raised()
    #     if warning_raised:
    #         return warning_raised
