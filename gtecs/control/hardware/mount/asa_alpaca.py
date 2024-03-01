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
        default = None (one will be created by the class)
    log_debug : bool, optional
        log debug strings?
        default = False

    """

    def __init__(self, address, port, api_version=1, device_number=0,
                 report_extra=True, report_history_limit=60,
                 fake_parking=False, force_pier_side=-1,
                 log=None, log_debug=False):
        self.address = address
        self.port = port
        self.api_version = api_version
        self.device_number = device_number
        self.base_url = f'http://{address}:{port}/api/v{api_version}/telescope/{device_number}/'
        self.client_id = random.randint(0, 2**32)
        self.transaction_count = 0

        # These are to account for errors in AutoSlew which mean we can't use the park functions
        self._fake_parking = fake_parking
        self._fake_parked = False

        # For the GOTO mounts we want to force no pier flips
        # Note ASA use the opposite convention to ASCOM!
        #   For 0 the mount is on the *east* side of the pier,
        #     but the telescope is looking towards the *west*
        #   ASCOM calls 0 pierEast, but AutoSlew GUI reports PierSide West
        # We just want to make sure the mount stays on whatever side it's been setup for,
        # so it doesn't really matter which is which (basically we just have "up" and "down")
        # -1 is code for we don't care, let AutoSlew decide for each slew
        if force_pier_side not in [0, 1, -1]:
            raise ValueError('Invalid option for force_pier_side: {}'.format(force_pier_side))
        self._force_pier_side = force_pier_side

        self.report_extra = report_extra
        self.report_history_limit = report_history_limit
        self._report_ra = None
        self._report_dec = None
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

        # Update status and report for initial values
        self._update_status()
        self._get_report()

        # Set report thread running
        self.report_thread_running = False
        if self.report_extra:
            t = threading.Thread(target=self._report_thread)
            t.daemon = True
            t.start()

    def __del__(self):
        self.report_thread_running = False
        self.disconnect()

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
            if self.log_debug:
                self.log.debug(f'{cmd}:"{url}":{data}')

            if cmd == 'GET':
                # GET commands require params in the URL (no body)
                r = requests.get(url, params=data)
            elif cmd == 'PUT':
                # PUT commands require params in the message body
                r = requests.put(url, data=data)

            reply_str = r.content.decode(r.encoding)
            if self.log_debug:
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

            self._tracking_rate = {'ra': self._http_get('rightascensionrate'),
                                   'dec': self._http_get('declinationrate')}
            self._guide_rate = {'ra': self._http_get('guideraterightascension'),
                                'dec': self._http_get('guideratedeclination')}
            self._pier_side = self._http_get('sideofpier')

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
        if self._fake_parking:
            return self._fake_parked
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
    def tracking_rate(self):
        """Return the current tracking rate (arcsec/sec)."""
        self._update_status()
        return self._tracking_rate

    @property
    def guide_rate(self):
        """Return the current pulse guiding rate (degrees/sec)."""
        self._update_status()
        return self._guide_rate

    @property
    def pier_side(self):
        """Return which side of the pier the mount is currently on."""
        self._update_status()
        return self._pier_side

    def _get_report(self, disable_reporting_after=True):
        """Get extra info from the mount report."""
        # Make sure reporting is on
        self._http_put('action', {'Action': 'reporting', 'Parameters': 'on'})

        # Get report dicts
        report_ra = self._http_put('action', {'Action': 'report', 'Parameters': '1'})
        report_dec = self._http_put('action', {'Action': 'report', 'Parameters': '2'})
        if len(report_ra) == 0 or len(report_dec) == 0:
            raise ValueError('Invalid report string')
        self._report_ra = json.loads(report_ra)
        self._report_dec = json.loads(report_dec)

        # Store the latest values from the report
        self._position = {
            'ra': self._report_ra['EncPos'], 'dec': self._report_dec['EncPos']
        }
        self._position_error = {
            'ra': self._report_ra['PosErr'], 'dec': self._report_dec['PosErr']
        }
        self._tracking_error = {
            'ra': -999, 'dec': -999  # Not implemented
        }
        self._velocity = {
            'ra': self._report_ra['Velocity'], 'dec': self._report_dec['Velocity']
        }
        self._acceleration = {
            'ra': -999, 'dec': -999  # Not implemented
        }
        self._current = {
            'ra': self._report_ra['QCurr'], 'dec': self._report_dec['QCurr']
        }

        # Add to history, and remove old entries
        report_time = {
            'ra': Time(self._report_ra['LastTime'].split('+')[0]).unix,
            'dec': Time(self._report_dec['LastTime'].split('+')[0]).unix
        }
        if not hasattr(self, '_position_hist'):
            self._position_hist = {'ra': [], 'dec': []}
        if not hasattr(self, '_position_error_hist'):
            self._position_error_hist = {'ra': [], 'dec': []}
        if not hasattr(self, '_tracking_error_hist'):
            self._tracking_error_hist = {'ra': [], 'dec': []}
        if not hasattr(self, '_velocity_hist'):
            self._velocity_hist = {'ra': [], 'dec': []}
        if not hasattr(self, '_acceleration_hist'):
            self._acceleration_hist = {'ra': [], 'dec': []}
        if not hasattr(self, '_current_hist'):
            self._current_hist = {'ra': [], 'dec': []}
        for axis in ('ra', 'dec'):
            # Add new entries, if they have changed
            if (len(self._position_hist[axis]) == 0 or
                    self._position_hist[axis][-1][1] != self._position[axis]):
                self._position_hist[axis].append(
                    (report_time[axis], self._position[axis]))
            if (len(self._position_error_hist[axis]) == 0 or
                    self._position_error_hist[axis][-1][1] != self._position_error[axis]):
                self._position_error_hist[axis].append(
                    (report_time[axis], self._position_error[axis]))
            if (len(self._tracking_error_hist[axis]) == 0 or
                    self._tracking_error_hist[axis][-1][1] != self._tracking_error[axis]):
                self._tracking_error_hist[axis].append(
                    (report_time[axis], self._tracking_error[axis]))
            if (len(self._velocity_hist[axis]) == 0 or
                    self._velocity_hist[axis][-1][1] != self._velocity[axis]):
                self._velocity_hist[axis].append(
                    (report_time[axis], self._velocity[axis]))
            if (len(self._acceleration_hist[axis]) == 0 or
                    self._acceleration_hist[axis][-1][1] != self._acceleration[axis]):
                self._acceleration_hist[axis].append(
                    (report_time[axis], self._acceleration[axis]))
            if (len(self._current_hist[axis]) == 0 or
                    self._current_hist[axis][-1][1] != self._current[axis]):
                self._current_hist[axis].append(
                    (report_time[axis], self._current[axis]))

            # Remove old entries, as long as there's more than one
            # (we had issues with -999 readings being filtered out)
            time_limit = time.time() - self.report_history_limit
            if len(self._position_hist[axis]) > 1:
                self._position_hist[axis] = [
                    hist for hist in self._position_hist[axis] if hist[0] > time_limit
                ]
            if len(self._position_error_hist[axis]) > 1:
                self._position_error_hist[axis] = [
                    hist for hist in self._position_error_hist[axis] if hist[0] > time_limit
                ]
            if len(self._tracking_error_hist[axis]) > 1:
                self._tracking_error_hist[axis] = [
                    hist for hist in self._tracking_error_hist[axis] if hist[0] > time_limit
                ]
            if len(self._velocity_hist[axis]) > 1:
                self._velocity_hist[axis] = [
                    hist for hist in self._velocity_hist[axis] if hist[0] > time_limit
                ]
            if len(self._acceleration_hist[axis]) > 1:
                self._acceleration_hist[axis] = [
                    hist for hist in self._acceleration_hist[axis] if hist[0] > time_limit
                ]
            if len(self._current_hist[axis]) > 1:
                self._current_hist[axis] = [
                    hist for hist in self._current_hist[axis] if hist[0] > time_limit
                ]

        if disable_reporting_after:
            self._http_put('action', {'Action': 'reporting', 'Parameters': 'off'})

    def _report_thread(self):
        if self.report_thread_running:
            self.log.debug('status thread tried to start when already running')
            return

        self.log.debug('mount report thread started')
        self.report_thread_running = True

        while self.report_thread_running:
            try:
                self._get_report(disable_reporting_after=False)
                time.sleep(0.1)
            except Exception:
                self.log.error('Error in report thread')
                self.log.debug('', exc_info=True)
                self.report_thread_running = False

        # Turn off reporting when we're done
        self._http_put('action', {'Action': 'reporting', 'Parameters': 'off'})
        self.log.debug('report thread finished')

    @property
    def encoder_position(self):
        """Return the current encoder position in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._position

    @property
    def encoder_position_history(self):
        """Return the history of encoder positions in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._position_hist

    @property
    def position_error(self):
        """Return the current encoder position error in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._position_error

    @property
    def position_error_history(self):
        """Return the history of encoder position errors in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._position_error_hist

    @property
    def tracking_error(self):
        """Return the current tracking error in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._tracking_error

    @property
    def tracking_error_history(self):
        """Return the history of tracking errors in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._tracking_error_hist

    @property
    def velocity(self):
        """Return the current motor velocity in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._velocity

    @property
    def velocity_history(self):
        """Return the history of motor velocities in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._velocity_hist

    @property
    def acceleration(self):
        """Return the current motor acceleration in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._acceleration

    @property
    def acceleration_history(self):
        """Return the history of motor accelerations in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._acceleration_hist

    @property
    def motor_current(self):
        """Return the current motor current in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._current

    @property
    def motor_current_history(self):
        """Return the history of motor currents in both axes."""
        if not self.report_extra or not self.report_thread_running:
            raise ValueError('Mount report thread not running')
        return self._current_hist

    def within_ra_limits(self, ra_min=None, ra_max=None):
        """Return true if the mount is within the given RA limits."""
        if ra_min is not None and self.encoder_position['ra'] < ra_min:
            return False
        if ra_max is not None and self.encoder_position['ra'] > ra_max:
            return False
        return True

    def within_dec_limits(self, dec_min=None, dec_max=None):
        """Return true if the mount is within the given Dec limits."""
        if dec_min is not None and self.encoder_position['dec'] < dec_min:
            return False
        if dec_max is not None and self.encoder_position['dec'] > dec_max:
            return False
        return True

    def within_encoder_limits(self, ra_min=None, ra_max=None, dec_min=None, dec_max=None):
        """Return true if the mount is within the given encoder limits."""
        return self.within_ra_limits(ra_min, ra_max) and self.within_dec_limits(dec_min, dec_max)

    def slew_to_radec(self, ra, dec, force_pier_side=None):
        """Slew to given RA and Dec coordinates (J2000)."""
        # first need to "cook" the coordinates into apparent
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        # Force (ask) pier side to not change
        data_dict = {'Action': 'forcenextpierside'}
        if force_pier_side is None and self._force_pier_side != -1:
            data_dict['Parameters'] = self._force_pier_side
        elif force_pier_side in [0, 1]:
            data_dict['Parameters'] = force_pier_side
        else:
            # Using argument force_pier_side=-1 will override any saved on the class
            data_dict['Parameters'] = -1
        self._http_put('action', data_dict)

        # Although it's called "forcenextpierside", it will only try to enforce the request
        # It can still flip, which is bad for us and makes the command fairly useless
        # ASCOM has a built-in command to check the destination, so we'll use that
        data_dict = {'RightAscension': ra_jnow, 'Declination': dec_jnow}
        destination_side = self._http_get('destinationsideofpier', data_dict)
        if destination_side == -1:
            raise ValueError('Slew command is not within allowed limits')
        if ((self._force_pier_side != -1 and destination_side != self._force_pier_side) or
                force_pier_side in [0, 1] and destination_side != force_pier_side):
            raise ValueError('Mount wants to flip despite force_pier_side, will not slew')

        self._http_put('slewtocoordinatesasync', data_dict)

    def slew_to_altaz(self, alt, az, force_pier_side=None):
        """Slew mount to given Alt/Az."""
        # Force (ask) pier side to not change
        data_dict = {'Action': 'forcenextpierside'}
        if force_pier_side is None and self._force_pier_side != -1:
            data_dict['Parameters'] = self._force_pier_side
        elif force_pier_side in [0, 1]:
            data_dict['Parameters'] = force_pier_side
        else:
            # Using argument force_pier_side=-1 will override any saved on the class
            data_dict['Parameters'] = -1
        self._http_put('action', data_dict)

        # Unfortunately there's no easy equivalent to "destinationsideofpier" for Alt/Az
        # So we'll have to convert into RA/Dec and check that (we also have to uncook first)
        # It should be close enough...
        ra, dec = radec_from_altaz(alt, az)
        ra_jnow, dec_jnow = j2000_to_apparent(ra, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        data_dict = {'RightAscension': ra_jnow, 'Declination': dec_jnow}
        destination_side = self._http_get('destinationsideofpier', data_dict)
        if destination_side == -1:
            raise ValueError('Slew command is not within allowed limits')
        if ((self._force_pier_side != -1 and destination_side != self._force_pier_side) or
                force_pier_side in [0, 1] and destination_side != force_pier_side):
            raise ValueError('Mount wants to flip despite force_pier_side, will not slew')

        data_dict = {'Azimuth': az, 'Altitude': alt}
        self._http_put('slewtoaltazasync', data_dict)

    def sync_radec(self, ra, dec):
        """Set current pointing to given RA and Dec coordinates (in J2000)."""
        # first need to "cook" the coordinates into apparent
        ra_jnow, dec_jnow = j2000_to_apparent(ra * 360 / 24, dec, Time.now().jd)
        ra_jnow *= 24 / 360
        if ra_jnow >= 24:
            ra_jnow -= 24
        if self.log_debug:
            self.log.debug('Cooked {:.6f}/{:.6f} to {:.6f}/{:.6f}'.format(
                ra, dec, ra_jnow, dec_jnow))

        data_dict = {'RightAscension': ra_jnow, 'Declination': dec_jnow}
        self._http_put('synctocoordinates', data_dict)

    def track(self):
        """Start tracking at the siderial rate."""
        self._http_put('tracking', {'Tracking': True})

    def _stop_tracking(self):
        """Stop the mount tracking if we're faking parking."""
        time.sleep(2)  # So it should have started moving
        while True:
            if self.status == 'Tracking':
                self.halt()
                break
            time.sleep(0.5)
        time.sleep(2)  # So it's stopped
        self._fake_parked = True

    def park(self):
        """Move mount to park position."""
        if self._fake_parking:
            # Need to fake a move to a default park position
            self.slew_to_altaz(alt=40, az=0)
            t = threading.Thread(target=self._stop_tracking)
            t.start()
            return
        self._http_put('park')

    def unpark(self):
        """Unpark the mount so it can accept slew commands."""
        if self._fake_parking:
            self._fake_parked = False
            return
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
        default = None (one will be created by the class)
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
        self._connected = True
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
        self._tracking = False
        self._slewing = False
        self._guiding = False
        self._parked = True
        self._motors_on = False
        self._tracking_rate = {'ra': 0, 'dec': 0}
        self._guide_rate = {'ra': 0, 'dec': 0}
        self._pier_side = 1
        self._position = {'ra': 0, 'dec': 0}
        self._position_error = {'ra': 0, 'dec': 0}
        self._tracking_error = {'ra': 0, 'dec': 0}
        self._velocity = {'ra': 0, 'dec': 0}
        self._acceleration = {'ra': 0, 'dec': 0}
        self._current = {'ra': 0, 'dec': 0}
        self._position_hist = {'ra': [], 'dec': []}
        self._position_error_hist = {'ra': [], 'dec': []}
        self._tracking_error_hist = {'ra': [], 'dec': []}
        self._velocity_hist = {'ra': [], 'dec': []}
        self._acceleration_hist = {'ra': [], 'dec': []}
        self._current_hist = {'ra': [], 'dec': []}

        self._slewing_thread_running = False
        self._slew_speed = 10  # deg/sec

    def connect(self):
        """Connect to the mount device."""
        self._connected = True

    @property
    def connected(self):
        """Check connection to the mount device."""
        return self._connected

    def disconnect(self):
        """Disconnect from the mount device."""
        self._connected = False

    def _get_info(self):
        """Get basic mount properties."""
        info = {}
        # Mount params
        info['equatorialsystem'] = 1
        info['doesrefraction'] = True
        info['trackingrates'] = [0, 1, 2]
        # ASCOM params
        info['name'] = 'Fake ASA'
        info['description'] = 'Fake ASA mount class'
        info['driverinfo'] = 'Fake class'
        info['driverversion'] = '0.0'
        info['interfaceversion'] = 0
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
        return get_lst().hourangle

    @property
    def tracking(self):
        """Return if the mount is currently tracking."""
        return self._tracking

    @property
    def nonsidereal(self):
        """Return if the mount has a non-sidereal tracking rate set."""
        return self.tracking_rate['ra'] != 0 or self.tracking_rate['dec'] != 0

    @property
    def slewing(self):
        """Return if the mount is currently slewing."""
        return self._slewing

    @property
    def guiding(self):
        """Return if the mount is currently pulse guiding."""
        return self._guiding

    @property
    def parked(self):
        """Return if the mount is currently parked."""
        return self._parked

    @property
    def motors_on(self):
        """Return if the mount motors are currently on."""
        return self._motors_on

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

    @property
    def tracking_rate(self):
        """Return the current tracking rate (arcsec/sec)."""
        return self._tracking_rate

    @property
    def guide_rate(self):
        """Return the current pulse guiding rate (degrees/sec)."""
        return self._guide_rate

    @property
    def pier_side(self):
        """Return which side of the pier the mount is currently on."""
        return self._pier_side

    @property
    def encoder_position(self):
        """Return the current encoder position in both axes."""
        return self._position

    @property
    def encoder_position_history(self):
        """Return the history of encoder positions in both axes."""
        return self._position_hist

    @property
    def position_error(self):
        """Return the current encoder position error in both axes."""
        return self._position_error

    @property
    def position_error_history(self):
        """Return the history of encoder position errors in both axes."""
        return self._position_error_hist

    @property
    def tracking_error(self):
        """Return the current tracking error in both axes."""
        return self._tracking_error

    @property
    def tracking_error_history(self):
        """Return the history of tracking errors in both axes."""
        return self._tracking_error_hist

    @property
    def velocity(self):
        """Return the current motor velocity in both axes."""
        return self._velocity

    @property
    def velocity_history(self):
        """Return the history of motor velocities in both axes."""
        return self._velocity_hist

    @property
    def acceleration(self):
        """Return the current motor acceleration in both axes."""
        return self._acceleration

    @property
    def acceleration_history(self):
        """Return the history of motor accelerations in both axes."""
        return self._acceleration_hist

    @property
    def motor_current(self):
        """Return the current motor current in both axes."""
        return self._current

    @property
    def motor_current_history(self):
        """Return the history of motor currents in both axes."""
        return self._current_hist

    def within_ra_limits(self, ra_min=None, ra_max=None):
        """Return true if the mount is within the given RA limits."""
        if ra_min is not None and self.encoder_position['ra'] < ra_min:
            return False
        if ra_max is not None and self.encoder_position['ra'] > ra_max:
            return False
        return True

    def within_dec_limits(self, dec_min=None, dec_max=None):
        """Return true if the mount is within the given Dec limits."""
        if dec_min is not None and self.encoder_position['dec'] < dec_min:
            return False
        if dec_max is not None and self.encoder_position['dec'] > dec_max:
            return False
        return True

    def within_encoder_limits(self, ra_min=None, ra_max=None, dec_min=None, dec_max=None):
        """Return true if the mount is within the given encoder limits."""
        return self.within_ra_limits(ra_min, ra_max) and self.within_dec_limits(dec_min, dec_max)

    def _slewing_thread(self, target_ra, target_dec, parking=False):
        """Simulate slewing from one position to another (very basic!)."""
        self._tracking = False
        self._parked = False
        self._guiding = False
        self._slewing = True

        while self._slewing:
            if self.log_debug:
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

        self._slewing = False
        if not parking:
            self._tracking = True
        else:
            self._parked = True

    def slew_to_radec(self, ra, dec):
        """Slew to given RA and Dec coordinates (J2000)."""
        t = threading.Thread(target=self._slewing_thread, args=[ra * 360 / 24, dec])
        t.daemon = True
        t.start()
        return

    def slew_to_altaz(self, alt, az):
        """Slew mount to given Alt/Az."""
        ra, dec = radec_from_altaz(alt, az)
        self.slew_to_radec(ra * 24 / 360, dec)

    def sync_radec(self, ra, dec):
        """Set current pointing to given RA and Dec coordinates (in J2000)."""
        self._ra = ra * 360 / 24
        self._dec = dec

    def track(self):
        """Start tracking at the siderial rate."""
        self._tracking = True

    def park(self):
        """Move mount to park position."""
        ra, dec = radec_from_altaz(self._park_alt, self._park_az)
        t = threading.Thread(target=self._slewing_thread, args=[ra, dec, True])
        t.daemon = True
        t.start()
        return

    def unpark(self):
        """Unpark the mount so it can accept slew commands."""
        self._parked = False

    def halt(self):
        """Abort slew (if slewing) and stop tracking (if tracking)."""
        self._slewing = False

    def start_motors(self):
        """Start the mount motors."""
        self._motors_on = True

    def stop_motors(self):
        """Stop the mount motors."""
        self._motors_on = False

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
        self._tracking = False
        self._parked = False
        self._slewing = False
        self._guiding = True

        guide_start_time = time.time()
        while self.guiding:
            if self.log_debug:
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

        self._guiding = False
        self._tracking = True

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
