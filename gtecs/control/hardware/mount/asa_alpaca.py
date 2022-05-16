"""Classes to control ASA mounts using ASCOM Alpaca."""

import json
import logging
import random
import threading
import time

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

import requests

from ...astronomy import altaz_from_radec, get_lst, radec_from_altaz
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

        # Get mount info (this shouldn't change, so just get once when starting)
        self.info = self._get_info()

        # Update status
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

    def _get_info(self):
        """Get basic mount properties."""
        info = {}
        # Mount params
        info['equatorialsystem'] = self._http_get('equatorialsystem')
        info['doesrefraction'] = self._http_get('doesrefraction')
        info['trackingrates'] = self._http_get('trackingrates')
        # ASCOM params
        info['name'] = self._http_get('name')
        info['description'] = self._http_get('description')
        info['driverinfo'] = self._http_get('driverinfo')
        info['driverversion'] = self._http_get('driverversion')
        info['interfaceversion'] = self._http_get('interfaceversion')
        info['sideofpier'] = self._http_get('sideofpier')  # Should stay fixed
        # AutoSlew params
        info['mounttype'] = self._http_put('action',
                                           {'Action': 'telescope:reportmounttype',
                                            'Parameters': ''})
        info['maxspeed'] = self._http_put('commandstring',
                                          {'Command': 'MaxSpeed',
                                           'Raw': False})
        info['corrections'] = self._http_put('commandstring',
                                             {'Command': 'GetCorrections',
                                              'Raw': False})
        info['mountname'] = self._http_put('commandstring',
                                           {'Command': 'GetMountName',
                                            'Raw': False})
        info['autoslewversion'] = self._http_put('commandstring',
                                                 {'Command': 'GetVersion',
                                                  'Raw': False})
        return info

    def _update_status(self):
        """Read and store status values."""
        # Only update if we need to, to save sending multiple commands
        if (time.time() - self._status_update_time) > 0.5:
            self._utc = Time(self._http_get('utcdate'))
            self._jd = self._utc.jd
            self._sidereal_time = self._http_get('siderealtime')

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
            self._guide_rate = {'ra': self._http_get('guideraterightascension'),
                                'dec': self._http_get('guideratedeclination')}

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
        """Return the current tracking rate (arcsec/sec)."""
        self._update_status()
        return self._tracking_rate

    @property
    def guide_rate(self):
        """Return the current pulse guiding rate (degrees/sec)."""
        self._update_status()
        return self._guide_rate

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

        # Force pier side to not change
        self._http_put('action', {'Action': 'forcenextpierside',
                                  'Parameters': self.info['sideofpier']})

        data_dict = {'RightAscension': ra_jnow, 'Declination': dec_jnow}
        self._http_put('slewtocoordinatesasync', data_dict)

    def slew_to_altaz(self, alt, az):
        """Slew mount to given Alt/Az."""
        # Force pier side to not change
        self._http_put('action', {'Action': 'forcenextpierside',
                                  'Parameters': self.info['sideofpier']})

        data_dict = {'Azimuth': az, 'Altitude': alt}
        self._http_put('slewtoaltazasync', data_dict)

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
        self._http_put('synctocoordinates', data_dict)

    def track(self):
        """Start tracking at the siderial rate."""
        self._http_put('tracking', {'Tracking': True})

    def park(self):
        """Move mount to park position."""
        self._http_put('park')

    def unpark(self):
        """Unpark the mount so it can accept slew commands."""
        self._http_put('unpark')

    def halt(self):
        """Abort slew (if slewing) and stop tracking (if tracking)."""
        self._http_put('abortslew')
        self._http_put('tracking', {'Tracking': False})

    def start_motors(self):
        """Start the mount motors."""
        self._http_put('action', {'Action': 'MotStat', 'Parameters': 'on'})

    def stop_motors(self):
        """Stop the mount motors."""
        self._http_put('action', {'Action': 'MotStat', 'Parameters': 'off'})

    def set_motor_power(self, activate):
        """Turn the mount motors on or off."""
        if activate:
            self.start_motors()
        else:
            self.stop_motors()

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

    def pulse_guide(self, direction, duration):
        """Move the scope in the given direction for the given duration (in ms)."""
        if direction.upper() not in ['N', 'S', 'E', 'W']:
            raise ValueError('Invalid direction "{}" (should be [N,E,S,W])'.format(direction))
        if not self.tracking:
            raise ValueError('Can only pulse guide when tracking')

        direction = ['N', 'S', 'E', 'W'].index(direction.upper())
        data_dict = {'Direction': direction, 'Duration': int(duration)}
        self._http_put('pulseguide', data_dict)

    def error_check(self):
        """Check for any errors raised by the mount."""
        error_raised = self._http_put('action',
                                      {'Action': 'telescope:errorstring', 'Parameters': ''})
        if error_raised:
            return error_raised

    def clear_error(self):
        """Clear for any errors raised by the mount."""
        self._http_put('action', {'Action': 'telescope:clearerror', 'Parameters': ''})

    def warning_check(self):
        """Check for any warnings raised by the mount."""
        return None
        # Not implemented yet
        # warning_raised = self.mount.warning_raised()
        # if warning_raised:
        #     return warning_raised


class FakeDDM500:
    """Fake ASA mount control class, for testing.

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

        self._status_update_time = 0
        self.connected = True
        self.info = self._get_info()

        # Create a logger if one isn't given
        if log is None:
            logging.basicConfig(level=logging.INFO)
            log = logging.getLogger('mount')
            log.setLevel(level=logging.DEBUG)
        self.log = log
        self.log_debug = log_debug

        # Fake position and statuses (starting from parked position)
        self._park_alt, self._park_az = 70, 0
        self._ra, self._dec = radec_from_altaz(self._park_alt, self._park_az)
        self.tracking = False
        self.slewing = False
        self.guiding = False
        self.parked = True
        self.motors_on = False
        self.position_error = {'ra': 0, 'dec': 0}
        self.tracking_error = {'ra': 0, 'dec': 0}
        self.velocity = {'ra': 0, 'dec': 0}
        self.acceleration = {'ra': 0, 'dec': 0}
        self.motor_current = {'ra': 0, 'dec': 0}
        self.tracking_rate = {'ra': 0, 'dec': 0}
        self.guide_rate = {'ra': 0, 'dec': 0}

        self._slewing_thread_running = False
        self._slew_speed = 10  # deg/sec

    def connect(self):
        """Connect to the mount device."""
        self.connected = True

    def disconnect(self):
        """Disconnect from the mount device."""
        self.connected = False

    def _get_info(self):
        """Get basic mount properties."""
        info = {}
        # Mount params
        info['equatorialsystem'] = 1
        info['doesrefraction'] = True
        info['trackingrates'] = self.connected = True
        # ASCOM params
        info['name'] = 'Fake ASA'
        info['description'] = 'Fake ASA mount class'
        info['driverinfo'] = 'Fake class'
        info['driverversion'] = '0.0'
        info['interfaceversion'] = 0
        info['sideofpier'] = 1
        # AutoSlew params
        info['mounttype'] = '2'
        info['maxspeed'] = '12'
        info['corrections'] = '0#0'
        info['mountname'] = 'Fake mount'
        info['autoslewversion'] = '0.0'
        return info

    @property
    def jd(self):
        """Return current Julian Date."""
        return Time.now().jd

    @property
    def sidereal_time(self):
        """Return the current sidereal time."""
        return get_lst()

    @property
    def nonsidereal(self):
        """Return if the mount has a non-sidereal tracking rate set."""
        return self.tracking_rate['ra'] != 0 or self.tracking_rate['dec'] != 0

    @property
    def status(self):
        """Return the current mount status."""
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
        return self._ra * 24 / 360  # In hours

    @property
    def dec(self):
        """Return the current pointing Dec."""
        return self._dec

    @property
    def alt(self):
        """Return the current altitude."""
        alt, _ = altaz_from_radec(self._ra, self._dec)
        return alt

    @property
    def az(self):
        """Return the current azimuth."""
        _, az = altaz_from_radec(self._ra, self._dec)
        return az

    def _slewing_thread(self, target_ra, target_dec, parking=False):
        """Simulate slewing from one position to another (very basic!)."""
        self.tracking = False
        self.parked = False
        self.guiding = False
        self.slewing = True

        while self.slewing:
            if self.log and self.log_debug:
                self.log.debug('Slewing: {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                    self._ra, self._dec, target_ra, target_dec))

            # Check if we're close enough to finish
            if abs(self._ra - target_ra) < self._slew_speed / 10:
                self._ra = target_ra
            if abs(self._dec - target_dec) < self._slew_speed / 10:
                self._dec = target_dec
            if self._ra == target_ra and self._dec == target_dec:
                break

            # Update the current position
            if self._ra < target_ra:
                self._ra += self._slew_speed / 10
            elif self._ra > target_ra:
                self._ra -= self._slew_speed / 10
            if self._dec < target_dec:
                self._dec += self._slew_speed / 10
            elif self._dec > target_dec:
                self._dec -= self._slew_speed / 10

            time.sleep(0.1)

        self.slewing = False
        if not parking:
            self.tracking = True
        else:
            self.parked = True

    def slew_to_radec(self, ra, dec):
        """Slew to given RA and Dec coordinates (J2000)."""
        t = threading.Thread(target=self._slewing_thread, args=[ra * 360 / 24, dec])
        t.daemon = True
        t.start()
        return

    def slew_to_altaz(self, alt, az):
        """Slew mount to given Alt/Az."""
        ra, dec = radec_from_altaz(alt, az)
        self.slew_to_radec(ra, dec)

    def sync_radec(self, ra, dec):
        """Set current pointing to given RA and Dec coordinates (in J2000)."""
        self._ra = ra * 360 / 24
        self._dec = dec

    def track(self):
        """Start tracking at the siderial rate."""
        self.tracking = True

    def park(self):
        """Move mount to park position."""
        ra, dec = radec_from_altaz(self._park_alt, self._park_az)
        t = threading.Thread(target=self._slewing_thread, args=[ra, dec, True])
        t.daemon = True
        t.start()
        return

    def unpark(self):
        """Unpark the mount so it can accept slew commands."""
        self.parked = False

    def halt(self):
        """Abort slew (if slewing) and stop tracking (if tracking)."""
        self.slewing = False

    def start_motors(self):
        """Start the mount motors."""
        self.motors_on = True

    def stop_motors(self):
        """Stop the mount motors."""
        self.motors_on = False

    def set_motor_power(self, activate):
        """Turn the mount motors on or off."""
        if activate:
            self.start_motors()
        else:
            self.stop_motors()

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

    def _guiding_thread(self, direction, duration):
        """Simulate guiding in some direction (very basic!)."""
        self.tracking = False
        self.parked = False
        self.slewing = False
        self.guiding = True

        guide_start_time = time.time()
        while self.guiding:
            if self.log and self.log_debug:
                self.log.debug('Guiding: {:.6f}/{:.6f} for {:.1f}/{:.1f}'.format(
                    self._ra, self._dec, time.time() - guide_start_time, duration))

            # Check if it's been long enough to finish
            if (time.time() - guide_start_time) > duration:
                break

            # Update the current position
            if direction == 'E':
                self._ra += self._slew_speed / 10
            elif direction == 'W':
                self._ra -= self._slew_speed / 10
            elif direction == 'N':
                self.dec += self._slew_speed / 10
            elif direction == 'S':
                self.dec -= self._slew_speed / 10
            else:
                raise ValueError('Invalid direction "{}" (should be [N,E,S,W])'.format(direction))

            time.sleep(0.1)

        self.guiding = False
        self.tracking = True

    def pulse_guide(self, direction, duration):
        """Move the scope in the given direction for the given duration (in ms)."""
        if direction.upper() not in ['N', 'S', 'E', 'W']:
            raise ValueError('Invalid direction "{}" (should be [N,E,S,W])'.format(direction))
        if not self.tracking:
            raise ValueError('Can only pulse guide when tracking')

        t = threading.Thread(target=self._guiding_thread, args=[direction.upper(), duration * 1000])
        t.daemon = True
        t.start()
        return

    def error_check(self):
        """Check for any errors raised by the mount."""
        return None

    def clear_error(self):
        """Clear for any errors raised by the mount."""
        return

    def warning_check(self):
        """Check for any warnings raised by the mount."""
        return None
