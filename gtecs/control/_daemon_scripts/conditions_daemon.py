#!/usr/bin/env python3
"""Daemon to monitor environmental conditions."""

import json
import os
import threading
import time

from astropy.time import Time

from gtecs.common.style import boldtxt, gtxt, rtxt, ytxt
from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.astronomy import get_sunalt
from gtecs.control.conditions.clouds import get_satellite_clouds
from gtecs.control.conditions.external import get_aat, get_ing, get_robodimm, get_tng
from gtecs.control.conditions.internal import get_internal_daemon, get_arduino_readout
from gtecs.control.conditions.local import (get_cloudwatcher_daemon, get_rain_daemon,
                                            get_rain_domealert, get_vaisala_daemon)
from gtecs.control.conditions.misc import check_ping, get_diskspace_remaining, get_ups
from gtecs.control.daemons import BaseDaemon
from gtecs.control.flags import ModeError, Status
from gtecs.control.slack import send_slack_msg

import numpy as np


class ConditionsDaemon(BaseDaemon):
    """Conditions monitor daemon class."""

    def __init__(self):
        super().__init__('conditions')

        # conditions variables
        self.history = {'windgust': {}}
        self.windgust_period = params.WINDGUST_PERIOD

        self.info_flag_names = ['clouds',
                                'dark',
                                ]
        if params.SITE_NAME == 'La Palma':
            self.info_flag_names.append('dust')
        self.normal_flag_names = ['rain',
                                  'windspeed',
                                  'windgust',
                                  'humidity',
                                  'temperature',
                                  'dew_point',
                                  'sky_temp',
                                  ]
        self.alert_flag_names = ['ups',
                                 'link',
                                 'diskspace',
                                 'internal',
                                 'ice',
                                 'override',
                                 ]
        self.flag_names = self.info_flag_names + self.normal_flag_names + self.alert_flag_names

        self.flags_file = os.path.join(params.FILE_PATH, 'conditions_flags.json')
        try:
            with open(self.flags_file, 'r') as f:
                data = json.load(f)
            self.flags = {flag: data[flag] for flag in self.flag_names}
            self.update_times = {flag: float(Time(data[flag + '_update_time']).unix)
                                 for flag in self.flag_names}
            if 'ignored_flags' in data:
                self.ignored_flags = data['ignored_flags']
            else:
                self.ignored_flags = []
        except Exception:
            self.flags = {flag: 2 for flag in self.flag_names}
            self.update_times = {flag: 0 for flag in self.flag_names}
            self.ignored_flags = []

        if 'override' in self.flags and self.flags['override'] == 1:
            self.manual_override = True
        else:
            self.manual_override = False

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')
        self.check_period = params.WEATHER_INTERVAL  # NB we loop less often than other daemons
        self.check_time = 0
        self.force_check_flag = True

        while self.running:
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time
                self.force_check_flag = False

                # Nothing to connect to, just get the info
                self._get_info()

                # Update the conditions flags
                try:
                    self._set_flags()
                except Exception:
                    self.log.error('Failed to set conditions flags')
                    self.log.debug('', exc_info=True)
                    self.flags = {flag: 2 for flag in self.flag_names}

                # Set ignored flags in some circumstances
                status = Status()
                if status.mode == 'robotic':
                    # Can't ignore flags in robotic mode
                    self.ignored_flags = []

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from the weather masts
        temp_info['weather'] = {}

        weather_sources = [params.VAISALA_URI]
        for source in [source for source in params.BACKUP_VAISALA_URIS if source != 'none']:
            weather_sources.append(source)
        # Add fallback sources just in case
        if params.SITE_NAME == 'La Palma':
            weather_sources.append('ing')
        elif params.SITE_NAME == 'Siding Spring':
            weather_sources.append('aat')

        for source in weather_sources:
            try:
                if source.startswith('PYRO:'):
                    uri = source
                    source = uri[5:].split('_')[0].lower()  # Extract source name from URI
                    if params.FAKE_CONDITIONS:
                        weather_dict = {
                            'temperature': 10,
                            'pressure': 800,
                            'windspeed': 5,
                            'winddir': 0,
                            'windgust': 10,
                            'windmax': 15,
                            'humidity': 50,
                            'rain': False,
                            'dew_point': 10,
                            'update_time': Time.now().iso,
                            'dt': 0,
                        }
                    else:
                        weather_dict = get_vaisala_daemon(uri)
                else:
                    # As long as we have a single local source then we don't need the backups.
                    # We should have done the local masts already, so we can check if they all
                    # failed by looking at the temp dict.
                    if all(temp_info['weather'][source]['dt'] == -999
                           for source in temp_info['weather']):
                        self.log.warning('All local weather sources failed!')
                        if source == 'ing':
                            weather_dict = get_ing()
                        elif source == 'aat':
                            weather_dict = get_aat()
                        else:
                            raise ValueError('Unknown weather source "{}"'.format(source))
                    else:
                        continue

                # Save a history of windgust readings internally
                if 'windgust' in weather_dict:
                    # Add the latest value to the history
                    new_hist = (self.loop_time, weather_dict['windgust'])
                    if source in self.history['windgust']:
                        self.history['windgust'][source].append(new_hist)
                    else:
                        self.history['windgust'][source] = [new_hist]
                    # Remove old readings (limit to history period) and any invalid values
                    self.history['windgust'][source] = [
                        h for h in self.history['windgust'][source]
                        if h[0] > self.loop_time - self.windgust_period and h[1] != -999
                    ]
                    # Add the maximum windgust value to the info dict
                    if len(self.history['windgust'][source]) > 1:
                        windmax = max([h[1] for h in self.history['windgust'][source]])
                        weather_dict['windmax'] = windmax
                    else:
                        weather_dict['windmax'] = -999

                # Check if the timeout has been exceeded
                if weather_dict['dt'] >= params.WEATHER_TIMEOUT or weather_dict['dt'] == -999:
                    raise ValueError('Timeout exceeded ({:.1f} > {:.1f})'.format(
                        weather_dict['dt'], params.WEATHER_TIMEOUT))

                # Also check if the weather hasn't changed for a given time
                weather_dict['changed_time'] = self.loop_time
                if (self.info and
                        source in self.info['weather'] and
                        self.info['weather'][source] and
                        'changed_time' in self.info['weather'][source]):
                    changed_time = self.info['weather'][source]['changed_time']
                    unchanged = [
                        weather_dict[key] == self.info['weather'][source][key]
                        for key in weather_dict
                        if key in self.info['weather'][source]
                    ]
                    dt = self.loop_time - changed_time
                    if all(unchanged) and dt > params.WEATHER_STATIC:
                        raise ValueError('Weather values unchanged ({:.1f} > {:.1f})'.format(
                            dt, params.WEATHER_STATIC))

            except Exception:
                self.log.error('Error getting weather from "{}"'.format(source))
                self.log.debug('', exc_info=True)
                weather_dict = {
                    'temperature': -999,
                    'pressure': -999,
                    'windspeed': -999,
                    'winddir': -999,
                    'windgust': -999,
                    'windmax': -999,
                    'humidity': -999,
                    'rain': -999,
                    'dew_point': -999,
                    'update_time': -999,
                    'dt': -999,
                }

            temp_info['weather'][source] = weather_dict

        # Get the internal conditions from internal sensors
        try:
            if params.FAKE_CONDITIONS:
                internal_dict = {
                    'temperature': 10,
                    'humidity': 25,
                    'update_time': Time.now().iso,
                    'dt': 0,
                }
            # First try getting the fallback Arduino if a filepath is given
            elif params.ARDUINO_FILE != 'none':
                internal_dict = get_arduino_readout(params.ARDUINO_FILE)
            # Otherwise try any given internal daemon URI
            elif params.INTERNAL_URI != 'none':
                internal_dict = get_internal_daemon(params.INTERNAL_URI)
            # If not, then then try getting readings from the DomeAlert
            elif params.DOMEALERT_URI != 'none':
                internal_dict = get_internal_daemon(params.DOMEALERT_URI)
            else:
                raise ValueError('No valid internal source specified')

            # Most internal sources only have a single sensor, but sometimes we have two
            # e.g. the DomeAlert has east/west. We want to keep the same dict structure.
            # Other places (e.g. the dome daemon or FITS headers) use the max of the dict values.
            if not isinstance(internal_dict['temperature'], dict):
                internal_dict['temperature'] = {'dome': internal_dict['temperature']}
            if not isinstance(internal_dict['humidity'], dict):
                internal_dict['humidity'] = {'dome': internal_dict['humidity']}
        except Exception:
            self.log.error('Failed to get internal info')
            self.log.debug('', exc_info=True)
            internal_dict = {
                'temperature': -999,
                'humidity': -999,
                'update_time': -999,
                'dt': -999,
            }
        temp_info['internal'] = internal_dict

        # Get rain board readings
        try:
            if params.FAKE_CONDITIONS:
                rain_dict = {
                    'total': 9,
                    'unsafe': 0,
                    'dt': 0,
                }
            else:
                rain_dict = {'total': 0, 'unsafe': 0, 'dt': 0}

                # Get readings from any standalone boards, connected to
                # the dome alert or with their own daemon
                if params.RAINDAEMON_URI != 'none':
                    try:
                        if 'domealert' in params.RAINDAEMON_URI:
                            rain_daemon_dict = get_rain_domealert(params.RAINDAEMON_URI)
                        else:
                            rain_daemon_dict = get_rain_daemon(params.RAINDAEMON_URI)
                        rain_dict['total'] += rain_daemon_dict['total']
                        rain_dict['unsafe'] += rain_daemon_dict['unsafe']
                        rain_dict['dt'] = rain_daemon_dict['dt']
                    except Exception:
                        self.log.error('Failed to get rain daemon info')

                # We've attached rain boards to some of the Vaisalas,
                # so include them in the count too
                for source in temp_info['weather']:
                    if (any('rainboard_' in key for key in temp_info['weather'][source]) and
                            temp_info['weather'][source]['rainboard_rain'] != -999):
                        rain_dict['total'] += temp_info['weather'][source]['rainboard_total']
                        rain_dict['unsafe'] += temp_info['weather'][source]['rainboard_unsafe']
                        # Use the longer update time I guess??
                        rain_dict['dt'] = max(rain_dict['dt'], temp_info['weather'][source]['dt'])

                if rain_dict['total'] > 0:
                    # If we have any rain boards then remove rain readings from other sources
                    for source in temp_info['weather']:
                        if 'rain' in temp_info['weather'][source]:
                            temp_info['weather'][source]['rain'] = None
                else:
                    # If we have no other option then we'll use the readings from the stations
                    for source in temp_info['weather']:
                        if ('rain' in temp_info['weather'][source] and
                                temp_info['weather'][source]['rain'] != -999):
                            rain_dict['total'] += 1
                            rain_dict['unsafe'] += int(temp_info['weather'][source]['rain'])
                            rain_dict['dt'] = max(rain_dict['dt'],
                                                  temp_info['weather'][source]['dt'])

                # Now if we still have no readings then we have a problem...
                if rain_dict['total'] == 0:
                    raise ValueError('No weather sources for rain readings')

        except Exception:
            self.log.error('Failed to get rain info')
            self.log.debug('', exc_info=True)
            rain_dict = {
                'total': -999,
                'unsafe': -999,
                'dt': -999,
            }
        temp_info['rain'] = rain_dict

        # Get seeing and dust from the TNG webpage (La Palma only)
        try:
            if params.FAKE_CONDITIONS:
                tng_dict = {
                    'seeing': 1.2,
                    'seeing_dt': 0,
                    'dust': 0,
                    'dust_dt': 0,
                }
            elif params.SITE_NAME == 'La Palma':
                tng_dict = get_tng()
                # check if the timeouts have been exceeded
                if tng_dict['seeing_dt'] >= params.SEEING_TIMEOUT or tng_dict['seeing_dt'] == -999:
                    tng_dict['seeing'] = -999
                if tng_dict['dust_dt'] >= params.DUSTLEVEL_TIMEOUT or tng_dict['dust_dt'] == -999:
                    tng_dict['dust'] = -999
            else:
                tng_dict = {
                    'seeing': -999,
                    'seeing_dt': -999,
                    'dust': -999,
                    'dust_dt': -999,
                }
        except Exception:
            self.log.error('Failed to get TNG info')
            self.log.debug('', exc_info=True)
            tng_dict = {
                'seeing': -999,
                'seeing_dt': -999,
                'dust': -999,
                'dust_dt': -999,
            }
        temp_info['tng'] = tng_dict

        # Get seeing from the ING RoboDIMM (La Palma only)
        try:
            if params.FAKE_CONDITIONS:
                dimm_dict = {
                    'seeing': 1.2,
                    'dt': 0,
                }
            elif params.SITE_NAME == 'La Palma':
                dimm_dict = get_robodimm()
                # check if the timeout has been exceeded
                if dimm_dict['dt'] >= params.SEEING_TIMEOUT or dimm_dict['dt'] == -999:
                    dimm_dict['seeing'] = -999
            else:
                dimm_dict = {
                    'seeing': -999,
                    'dt': -999,
                }
        except Exception:
            self.log.error('Failed to get DIMM info')
            self.log.debug('', exc_info=True)
            dimm_dict = {
                'seeing': -999,
                'dt': -999,
            }
        temp_info['robodimm'] = dimm_dict

        # Get sky temperature from the CloudWatcher or the AAT (Siding Spring only)
        try:
            if params.FAKE_CONDITIONS:
                skytemp_dict = {
                    'sky_temp': -20,
                    'dt': 0,
                }
            elif params.CLOUDWATCHER_URI != 'none':
                skytemp_dict = get_cloudwatcher_daemon(params.CLOUDWATCHER_URI)
            elif params.SITE_NAME == 'Siding Spring':
                aat_dict = get_aat()
                # Simplify to values of interest
                skytemp_dict = {'sky_temp': aat_dict['sky_temp'],
                                'update_time': aat_dict['update_time'],
                                'dt': aat_dict['dt'],
                                }
            else:
                raise ValueError('No weather sources for sky temperature readings')
        except Exception:
            self.log.error('Failed to get sky temperature info')
            self.log.debug('', exc_info=True)
            skytemp_dict = {
                'sky_temp': -999,
                'dt': -999,
            }
        temp_info['sky_temp'] = skytemp_dict

        # Get info from the UPSs
        try:
            if params.FAKE_CONDITIONS:
                temp_info['ups_percent'] = [100, 100]
                temp_info['ups_status'] = [True, True]
            else:
                ups_percent, ups_status = get_ups()
                temp_info['ups_percent'] = ups_percent
                temp_info['ups_status'] = ups_status
        except Exception:
            self.log.error('Failed to get UPS info')
            self.log.debug('', exc_info=True)
            temp_info['ups_percent'] = -999
            temp_info['ups_status'] = -999

        # Get info from the link ping check
        try:
            if params.FAKE_CONDITIONS:
                temp_info['pings'] = [True, True]
            else:
                pings = [check_ping(url) for url in params.LINK_URLS]
                temp_info['pings'] = pings
        except Exception:
            self.log.error('Failed to get link info')
            self.log.debug('', exc_info=True)
            temp_info['pings'] = -999

        # Get info from the disk usage check
        try:
            if params.FAKE_CONDITIONS:
                temp_info['free_diskspace'] = 90
            else:
                free_diskspace = get_diskspace_remaining(params.IMAGE_PATH) * 100.
                temp_info['free_diskspace'] = free_diskspace
        except Exception:
            self.log.error('Failed to get diskspace info')
            self.log.debug('', exc_info=True)
            temp_info['free_diskspace'] = -999

        # Get info from the satellite IR cloud image
        # Note if if fails (which is common) we only log the start and end
        try:
            if params.FAKE_CONDITIONS:
                temp_info['clouds'] = 0
            else:
                clouds = get_satellite_clouds(site=params.SITE_NAME) * 100
                temp_info['clouds'] = clouds
                if self.info and self.info['clouds'] == -999:
                    self.log.info('Satellite clouds info restored')
        except Exception:
            if not self.info or (self.info and self.info['clouds'] != -999):
                self.log.error('Failed to get satellite clouds info')
                self.log.debug('', exc_info=True)
            temp_info['clouds'] = -999

        # Get current sun alt
        try:
            sunalt = get_sunalt(Time(self.loop_time, format='unix'))
            temp_info['sunalt'] = sunalt
        except Exception:
            self.log.error('Failed to get sunalt info')
            self.log.debug('', exc_info=True)
            temp_info['sunalt'] = -999

        # Get internal info
        temp_info['flags'] = self.flags.copy()
        temp_info['info_flags'] = sorted(self.info_flag_names)
        temp_info['normal_flags'] = sorted(self.normal_flag_names)
        temp_info['alert_flags'] = sorted(self.alert_flag_names)
        temp_info['ignored_flags'] = sorted(self.ignored_flags)
        temp_info['manual_override'] = self.manual_override

        # Write debug log line
        try:
            now_strs = ['{}:{}'.format(key, temp_info['flags'][key])
                        for key in sorted(self.flag_names)]
            now_str = ' '.join(now_strs)
            if not self.info:
                self.log.debug('Conditions flags: {}'.format(now_str))
            else:
                old_strs = ['{}:{}'.format(key, self.info['flags'][key])
                            for key in sorted(self.flag_names)]
                old_str = ' '.join(old_strs)
                if now_str != old_str:
                    self.log.debug('Conditions flags: {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

    def _set_flags(self):
        """Set the conditions flags based on the conditions info."""
        # Get the conditions values and filter by validity if needed

        # Weather
        weather = self.info['weather']
        windspeed = np.array([weather[source]['windspeed'] for source in weather
                              if 'windspeed' in weather[source]])
        windgust = np.array([weather[source]['windgust'] for source in weather
                             if 'windgust' in weather[source]])
        windmax = np.array([weather[source]['windmax'] for source in weather
                            if 'windmax' in weather[source]])
        ext_temperature = np.array([weather[source]['temperature'] for source in weather
                                    if 'temperature' in weather[source]])
        ext_humidity = np.array([weather[source]['humidity'] for source in weather
                                if 'humidity' in weather[source]])
        dew_point = np.array([weather[source]['dew_point'] for source in weather
                             if 'dew_point' in weather[source]])

        windspeed = windspeed[windspeed != -999]
        windgust = windgust[windgust != -999]
        windmax = windmax[windmax != -999]
        ext_temperature = ext_temperature[ext_temperature != -999]
        ext_humidity = ext_humidity[ext_humidity != -999]
        dew_point = dew_point[dew_point != -999]

        # Rain
        rain = np.array(self.info['rain']['unsafe'])
        rain = rain[rain != -999]

        # Sky temperature
        sky_temp = np.array(self.info['sky_temp']['sky_temp'])
        sky_temp = sky_temp[sky_temp != -999]

        # Internal
        int_temperature = np.array([self.info['internal']['temperature'][source]
                                    for source in self.info['internal']['temperature']])
        int_humidity = np.array([self.info['internal']['humidity'][source]
                                 for source in self.info['internal']['humidity']])

        int_temperature = int_temperature[int_temperature != -999]
        int_humidity = int_humidity[int_humidity != -999]

        # UPSs
        ups_percent = np.array(self.info['ups_percent'])
        ups_status = np.array(self.info['ups_status'])

        ups_percent = ups_percent[ups_percent != -999]
        ups_status = ups_status[ups_status != -999]

        # Link
        pings = np.array(self.info['pings'])
        pings = pings[pings != -999]

        # Diskspace
        free_diskspace = np.array(self.info['free_diskspace'])
        free_diskspace = free_diskspace[free_diskspace != -999]

        # Clouds
        clouds = np.array(self.info['clouds'])
        clouds = clouds[clouds != -999]

        # Dust
        dust = np.array(self.info['tng']['dust'])
        dust = dust[dust != -999]

        # Sunalt
        sunalt = np.array(self.info['sunalt'])
        sunalt = sunalt[sunalt != -999]

        # ~~~~~~~~~~~~~~
        # Calculate the flags and if they are valid.
        # At least one of the sources need to be valid.
        good = {flag: False for flag in self.flag_names}
        critical = {flag: False for flag in self.flag_names}
        valid = {flag: False for flag in self.flag_names}
        good_delay = {flag: 0 for flag in self.flag_names}
        bad_delay = {flag: 0 for flag in self.flag_names}
        error_delay = 60

        # windspeed flag (based on instantaneous windgust)
        good['windspeed'] = np.all(windgust < params.MAX_WINDSPEED)
        critical['windspeed'] = False
        valid['windspeed'] = len(windgust) >= 1
        good_delay['windspeed'] = params.WINDSPEED_GOODDELAY
        bad_delay['windspeed'] = params.WINDSPEED_BADDELAY

        # windgust flag (based on historic windgust maximum)
        good['windgust'] = np.all(windmax < params.MAX_WINDGUST)
        critical['windgust'] = False
        valid['windgust'] = len(windmax) >= 1
        good_delay['windgust'] = params.WINDGUST_GOODDELAY
        bad_delay['windgust'] = params.WINDGUST_BADDELAY

        # temperature flag
        good['temperature'] = (np.all(ext_temperature > params.MIN_TEMPERATURE) and
                               np.all(ext_temperature < params.MAX_TEMPERATURE) and
                               np.all(int_temperature > params.MIN_INTERNAL_TEMPERATURE) and
                               np.all(int_temperature < params.MAX_INTERNAL_TEMPERATURE))
        critical['temperature'] = False
        valid['temperature'] = len(ext_temperature) >= 1 and len(int_temperature) >= 1
        good_delay['temperature'] = params.TEMPERATURE_GOODDELAY
        bad_delay['temperature'] = params.TEMPERATURE_BADDELAY

        # ice flag
        good['ice'] = np.all(ext_temperature > 0)
        critical['ice'] = False
        valid['ice'] = len(ext_temperature) >= 1
        good_delay['ice'] = params.ICE_GOODDELAY
        bad_delay['ice'] = params.ICE_BADDELAY

        # humidity flag
        good['humidity'] = (np.all(ext_humidity < params.MAX_HUMIDITY) and
                            np.all(int_humidity < params.MAX_INTERNAL_HUMIDITY))
        critical['humidity'] = (np.any(ext_humidity >= params.MAX_HUMIDITY_CRITICAL) or
                               np.any(int_humidity >= params.MAX_HUMIDITY_CRITICAL))
        valid['humidity'] = len(ext_humidity) >= 1 and len(int_humidity) >= 1
        good_delay['humidity'] = params.HUMIDITY_GOODDELAY
        bad_delay['humidity'] = params.HUMIDITY_BADDELAY

        # dew_point flag
        good['dew_point'] = np.all(dew_point > params.MIN_DEWPOINT)
        critical['dew_point'] = False
        valid['dew_point'] = len(dew_point) >= 1
        good_delay['dew_point'] = params.DEWPOINT_GOODDELAY
        bad_delay['dew_point'] = params.DEWPOINT_BADDELAY

        # rain flag
        good['rain'] = np.all(rain == 0)
        critical['rain'] = False
        valid['rain'] = len(rain) >= 1
        good_delay['rain'] = params.RAIN_GOODDELAY
        bad_delay['rain'] = params.RAIN_BADDELAY

        # sky_temp flag
        good['sky_temp'] = np.all(sky_temp < params.MAX_SKYTEMP)
        critical['sky_temp'] = False
        valid['sky_temp'] = len(sky_temp) >= 1
        good_delay['sky_temp'] = params.SKYTEMP_GOODDELAY
        bad_delay['sky_temp'] = params.SKYTEMP_BADDELAY

        # internal flag
        good['internal'] = (np.all(int_humidity < params.MAX_INTERNAL_HUMIDITY_ALERT) and
                            np.all(int_temperature > params.MIN_INTERNAL_TEMPERATURE_ALERT))
        critical['internal'] = False
        valid['internal'] = len(int_humidity) >= 1 and len(int_temperature) >= 1
        good_delay['internal'] = params.INTERNAL_GOODDELAY
        bad_delay['internal'] = params.INTERNAL_BADDELAY

        # dust flag
        good['dust'] = np.all(dust < params.MAX_DUSTLEVEL)
        critical['dust'] = False
        valid['dust'] = len(dust) >= 1
        good_delay['dust'] = params.DUSTLEVEL_GOODDELAY
        bad_delay['dust'] = params.DUSTLEVEL_BADDELAY

        # ups flag
        good['ups'] = (np.all(ups_percent > params.MIN_UPSBATTERY) and
                       np.all(ups_status == 1))
        critical['ups'] = False
        valid['ups'] = len(ups_percent) >= 1 and len(ups_status) >= 1
        good_delay['ups'] = params.UPS_GOODDELAY
        bad_delay['ups'] = params.UPS_BADDELAY

        # link flag
        good['link'] = np.all(pings == 1)
        critical['link'] = False
        valid['link'] = len(pings) >= 1
        good_delay['link'] = params.LINK_GOODDELAY
        bad_delay['link'] = params.LINK_BADDELAY

        # diskspace flag
        good['diskspace'] = np.all(free_diskspace > params.MIN_DISKSPACE)
        critical['diskspace'] = False
        valid['diskspace'] = len(free_diskspace) >= 1
        good_delay['diskspace'] = 0
        bad_delay['diskspace'] = 0

        # clouds flag
        good['clouds'] = np.all(clouds < params.MAX_SATCLOUDS)
        critical['clouds'] = False
        valid['clouds'] = len(clouds) >= 1
        good_delay['clouds'] = params.SATCLOUDS_GOODDELAY
        bad_delay['clouds'] = params.SATCLOUDS_BADDELAY

        # dark flag
        good['dark'] = np.all(sunalt < params.SUN_ELEVATION_LIMIT)
        critical['dark'] = False
        valid['dark'] = len(sunalt) >= 1
        good_delay['dark'] = 0
        bad_delay['dark'] = 0

        # override flag
        good['override'] = not self.manual_override
        critical['override'] = False
        valid['override'] = isinstance(self.manual_override, bool)
        good_delay['override'] = 0
        bad_delay['override'] = 0

        # ~~~~~~~~~~~~~~
        # Set each flag
        old_flags = self.flags.copy()
        current_time = self.info['time']
        for flag in self.flag_names:
            # check if invalid
            if not valid[flag] and self.flags[flag] != 2:
                dt = current_time - self.update_times[flag]
                if dt > error_delay:
                    self.log.info('Setting {} to ERROR (2)'.format(flag))
                    self.flags[flag] = 2
                    self.update_times[flag] = current_time
                else:
                    frac = '{:.0f}/{:.0f}'.format(dt, error_delay)
                    self.log.info('{} is ERROR but delay is {}'.format(flag, frac))
                continue

            # check if good
            if valid[flag] and good[flag] and self.flags[flag] != 0:
                dt = current_time - self.update_times[flag]
                if dt > good_delay[flag] or self.flags[flag] == 2:
                    self.log.info('Setting {} to good (0)'.format(flag))
                    self.flags[flag] = 0
                    self.update_times[flag] = current_time
                else:
                    frac = '{:.0f}/{:.0f}'.format(dt, good_delay[flag])
                    self.log.info('{} is good but delay is {}'.format(flag, frac))
                continue

            # check if bad/critical
            if valid[flag] and (not good[flag] or critical[flag]) and self.flags[flag] != 1:
                dt = current_time - self.update_times[flag]
                if dt > bad_delay[flag] or self.flags[flag] == 2 or critical[flag]:
                    if critical[flag]:
                        self.log.warning('{} is critical'.format(flag))
                    self.log.info('Setting {} to bad (1)'.format(flag))
                    self.flags[flag] = 1
                    self.update_times[flag] = current_time
                else:
                    frac = '{:.0f}/{:.0f}'.format(dt, bad_delay[flag])
                    self.log.info('{} is bad but delay is {}'.format(flag, frac))
                continue

            # otherwise everything is normal
            self.update_times[flag] = current_time

        # ~~~~~~~~~~~~~~
        # Write data to the conditions flags file
        data = self.flags.copy()
        for flag in self.update_times:
            data[flag + '_update_time'] = Time(self.update_times[flag], format='unix').iso
        data['current_time'] = Time(current_time, format='unix').iso
        data['info_flags'] = sorted(self.info_flag_names)
        data['normal_flags'] = sorted(self.normal_flag_names)
        data['alert_flags'] = sorted(self.alert_flag_names)
        data['ignored_flags'] = sorted(self.ignored_flags)
        with open(self.flags_file, 'w') as f:
            json.dump(data, f)

        # ~~~~~~~~~~~~~~
        # Trigger Slack alerts for alert flags
        for flag in self.alert_flag_names:
            if flag in self.ignored_flags:
                # If we're ignoring the flag then don't send an alert
                continue
            if old_flags[flag] == 0 and self.flags[flag] == 1:
                # The flag has been set to bad
                self.log.warning('Sending alert for flag {} (set to bad)'.format(flag))
                send_slack_msg('Conditions reports {} flag has been set to bad'.format(flag))
            elif old_flags[flag] == 0 and self.flags[flag] == 2:
                # The flag has been set to ERROR
                self.log.warning('Sending alert for flag {} (set to ERROR)'.format(flag))
                send_slack_msg('Conditions reports {} flag has been set to ERROR'.format(flag))
            elif old_flags[flag] in [1, 2] and self.flags[flag] == 0:
                # The flag has been set to good
                self.log.warning('Sending alert for flag {} (set to good)'.format(flag))
                send_slack_msg('Conditions reports {} flag has been set to good'.format(flag))

    # Control functions
    def update(self):
        """Force a conditions update."""
        self.force_check_flag = 1

    def ignore_flags(self, flags):
        """Add the given flags to the ignore list."""
        status = Status()
        if status.mode == 'robotic':
            raise ModeError('Can not ignore flags in robotic mode')
        if any(flag not in self.flags for flag in flags):
            bad_flags = [flag for flag in flags if flags not in self.flags]
            raise ValueError(f'Invalid flags: {bad_flags}')
        if 'override' in flags:
            raise ValueError('"override" flag can not be ignored')

        self.log.info(f'Adding flags to ignored list: {flags}')
        for flag in flags:
            self.ignored_flags.append(flag)

    def enable_flags(self, flags):
        """Remove the given flags from the ignore list."""
        status = Status()
        if status.mode == 'robotic':
            raise ModeError('All flags are enabled in robotic mode')
        if any(flag not in self.flags for flag in flags):
            bad_flags = [flag for flag in flags if flags not in self.flags]
            raise ValueError(f'Invalid flags: {bad_flags}')
        if 'override' in flags:
            raise ValueError('"override" flag can not be ignored')

        self.log.info(f'Removing flags from ignored list: {flags}')
        for flag in flags:
            self.ignored_flags.remove(flag)

    def set_override(self, command):
        """Activate or clear the manual override flag."""
        if command not in ['on', 'off']:
            raise ValueError("Command must be 'on' or 'off'")

        if command == 'on' and not self.manual_override:
            self.log.info('Enabling manual override')
            self.manual_override = True
        elif command == 'off' and self.manual_override:
            self.log.info('Disabling manual override')
            self.manual_override = False

    def dashboard_override(self, enable, dashboard_username):
        """Activate or deactivate the manual override flag from the web dashboard.

        This function is restricted to only the dashboard IP for specific outlets,
        and also has extra logging.
        See https://github.com/GOTO-OBS/g-tecs/issues/535 for details.
        """
        client_ip = self._get_client_ip()
        if client_ip != params.DASHBOARD_IP:
            return 1

        if enable:
            out_str = f'Web dashboard user {dashboard_username} turning on manual override'
        else:
            out_str = f'Web dashboard user {dashboard_username} turning off manual override'
        self.log.info(out_str)
        self.manual_override = enable

    def get_history(self):
        """Get the windgust history values for the header.

        This was previously part of the usual get_info() function, but the values made the
        dict too long so it was split out.
        """
        return self.history

    # Info function
    def get_info_string(self, force_update=False):
        """Get a string for printing status info."""
        info = self.get_info(force_update)

        if info is None:
            msg = 'CONDITIONS:\n'
            msg += '  None yet, try again'
            return msg

        msg = 'CONDITIONS ({}):\n'.format(info['timestamp'])

        flags = info['flags']
        normal_flags = sorted(info['normal_flags'])
        alert_flags = sorted(info['alert_flags'])
        info_flags = sorted(info['info_flags'])
        m_normal = max([len(flag) for flag in normal_flags])
        m_alert = max([len(flag) for flag in alert_flags])
        m_info = max([len(flag) for flag in info_flags])
        ignored_flags = sorted(info['ignored_flags'])
        msg += 'FLAGS:'
        msg += f'  {" "*(m_normal-6)}   normal   '
        msg += f'  {" "*m_alert}   alert   '
        msg += f'  {" "*m_info}   info\n'
        for i in range(max(len(normal_flags), len(alert_flags), len(info_flags))):
            # Print normal flags on the left, alert in the middle and info on the right
            if len(normal_flags) >= i + 1:
                flag = normal_flags[i]
                if flag in ignored_flags:
                    status = '----' + '\u200c' * 11
                elif flags[flag] == 0:
                    status = gtxt('Good')
                elif flags[flag] == 1:
                    status = rtxt('Bad')
                else:
                    status = rtxt('ERROR')
                msg += f'  {flag: >{m_normal}} : {status: <16} ({flags[flag]})'
            else:
                msg += '                          '

            if len(alert_flags) >= i + 1:
                flag = alert_flags[i]
                if flag in ignored_flags:
                    status = '----' + '\u200c' * 11
                elif flags[flag] == 0:
                    status = gtxt('Good')
                elif flags[flag] == 1:
                    status = rtxt('Bad')
                else:
                    status = rtxt('ERROR')
                msg += f'  {flag: >{m_alert}} : {status: <16} ({flags[flag]})'
            else:
                msg += '                          '

            if len(info_flags) >= i + 1:
                flag = info_flags[i]
                if flag in ignored_flags:
                    status = '----' + '\u200c' * 11
                elif flags[flag] == 0:
                    status = gtxt('Good')
                elif flags[flag] == 1:
                    status = ytxt('Bad')  # Info flags don't trigger close if bad
                else:
                    status = ytxt('ERROR')
                msg += f'  {flag: >{m_info}} : {status: <16} ({flags[flag]})\n'
            else:
                msg += '\n'

        msg += 'WEATHER:          temp   humid    dewpt  wind (gust, max)        rain\n'
        weather = info['weather']

        for source in weather:
            temperature = weather[source]['temperature']
            if temperature == -999:
                temperature_str = rtxt(' ERR')
            elif (temperature < params.MAX_TEMPERATURE and
                    temperature > params.MIN_TEMPERATURE):
                temperature_str = ytxt('{:>4.1f}'.format(temperature))
                if (temperature < params.MAX_TEMPERATURE - 1 and
                        temperature > params.MIN_TEMPERATURE + 1):
                    temperature_str = gtxt('{:>4.1f}'.format(temperature))
            else:
                temperature_str = rtxt('{:>4.1f}'.format(temperature))

            dewpoint = weather[source]['dew_point']
            if dewpoint == -999:
                dewpoint_str = rtxt('  ERR')
            elif (dewpoint > params.MIN_DEWPOINT):
                dewpoint_str = ytxt('{:>+5.1f}'.format(dewpoint))
                if (dewpoint > params.MIN_DEWPOINT + 1):
                    dewpoint_str = gtxt('{:>+5.1f}'.format(dewpoint))
            else:
                dewpoint_str = rtxt('{:>+5.1f}'.format(dewpoint))

            humidity = weather[source]['humidity']
            if humidity == -999:
                humidity_str = rtxt('  ERR')
            elif (humidity < params.MAX_HUMIDITY):
                humidity_str = ytxt('{:>5.1f}'.format(humidity))
                if (humidity < params.MAX_HUMIDITY - 5):
                    humidity_str = gtxt('{:>5.1f}'.format(humidity))
            else:
                humidity_str = rtxt('{:>5.1f}'.format(humidity))

            windspeed = weather[source]['windspeed']
            if windspeed == -999:
                windspeed_str = rtxt(' ERR')
            elif (windspeed < params.MAX_WINDSPEED):
                windspeed_str = ytxt('{:>4.1f}'.format(windspeed))
                if (windspeed < params.MAX_WINDSPEED - 5):
                    windspeed_str = gtxt('{:>4.1f}'.format(windspeed))
            else:
                windspeed_str = rtxt('{:>4.1f}'.format(windspeed))

            windgust = weather[source]['windgust']
            if windgust == -999:
                windgust_str = rtxt(' ERR')
            elif (windgust < params.MAX_WINDSPEED):
                windgust_str = ytxt('{:>4.1f}'.format(windgust))
                if (windgust < params.MAX_WINDSPEED - 5):
                    windgust_str = gtxt('{:>4.1f}'.format(windgust))
            else:
                windgust_str = rtxt('{:>4.1f}'.format(windgust))

            windmax = weather[source]['windmax']
            if windmax == -999:
                windmax_str = rtxt(' ERR')
            elif (windmax < params.MAX_WINDGUST):
                windmax_str = ytxt('{:>4.1f}'.format(windmax))
                if (windmax < params.MAX_WINDGUST - 5):
                    windmax_str = gtxt('{:>4.1f}'.format(windmax))
            else:
                windmax_str = rtxt('{:>4.1f}'.format(windmax))

            rain = weather[source]['rain'] if 'rain' in weather[source] else None
            if rain is None:
                rain_str = '  N/A'
            elif rain == -999:
                rain_str = rtxt('  ERR')
            elif rain:
                rain_str = rtxt(' True')
            else:
                rain_str = gtxt('False')

            dt = weather[source]['dt']
            if dt == -999:
                dt_str = rtxt('ERR')
            elif dt > params.WEATHER_TIMEOUT:
                dt_str = rtxt('{:.0f}'.format(dt))
            else:
                dt_str = gtxt('{:.0f}'.format(dt))

            msg += '  {: <10}\t'.format(source)
            weather_str = '{}°C  {}%  {}°C  {} ({},{}) km/h  {}  dt={}\n'.format(
                temperature_str,
                humidity_str,
                dewpoint_str,
                windspeed_str,
                windgust_str,
                windmax_str,
                rain_str,
                dt_str)
            msg += weather_str

        for source in info['internal']['temperature']:
            temperature = info['internal']['temperature'][source]
            if temperature == -999:
                temperature_str = rtxt(' ERR')
            elif (temperature < params.MAX_INTERNAL_TEMPERATURE and
                    temperature > params.MIN_INTERNAL_TEMPERATURE):
                temperature_str = ytxt('{:>4.1f}'.format(temperature))
                if (temperature < params.MAX_INTERNAL_TEMPERATURE - 1 and
                        temperature > params.MIN_INTERNAL_TEMPERATURE + 1):
                    temperature_str = gtxt('{:>4.1f}'.format(temperature))
            else:
                temperature_str = rtxt('{:>4.1f}'.format(temperature))

            humidity = info['internal']['humidity'][source]
            if humidity == -999:
                humidity_str = rtxt('  ERR')
            elif (humidity < params.MAX_INTERNAL_HUMIDITY):
                humidity_str = ytxt('{:>5.1f}'.format(humidity))
                if (humidity < params.MAX_INTERNAL_HUMIDITY - 5):
                    humidity_str = gtxt('{:>5.1f}'.format(humidity))
            else:
                humidity_str = rtxt('{:>5.1f}'.format(humidity))

            dt = info['internal']['dt']
            if dt == -999:
                dt_str = rtxt('ERR')
            elif dt > params.WEATHER_TIMEOUT:
                dt_str = rtxt('{:.0f}'.format(dt))
            else:
                dt_str = gtxt('{:.0f}'.format(dt))

            msg += '  {: <10}\t'.format('{}_int'.format(source))
            weather_str = '{}°C  {}%                                         dt={}\n'.format(
                temperature_str, humidity_str, dt_str)
            msg += weather_str

        msg += 'ENVIRONMENT:\n'

        rain_unsafe = info['rain']['unsafe']
        rain_total = info['rain']['total']
        if rain_unsafe == -999:
            rain_str = rtxt('  ERR') + '      '
        elif rain_unsafe > 0:
            rain_str = rtxt('  Bad') + ' ({}/{})'.format(rain_unsafe, rain_total)
        else:
            rain_str = gtxt(' Good') + ' ({}/{})'.format(rain_unsafe, rain_total)
        dt = info['rain']['dt']
        if dt == -999:
            dt_str = rtxt('ERR')
        elif dt > params.WEATHER_TIMEOUT:
            dt_str = rtxt('{:.0f}'.format(dt))
        else:
            dt_str = gtxt('{:.0f}'.format(dt))
        msg += '  rain_sensors {}      dt={}\n'.format(rain_str, dt_str)

        sky_temp = info['sky_temp']['sky_temp']
        if sky_temp == -999:
            sky_temp_str = rtxt('  ERR')
        elif sky_temp < params.MAX_SKYTEMP:
            sky_temp_str = ytxt('{:>5.1f}'.format(sky_temp))
            if sky_temp < params.MAX_SKYTEMP - 5:
                sky_temp_str = gtxt('{:>5.1f}'.format(sky_temp))
        else:
            sky_temp_str = rtxt('{:>5.1f}'.format(sky_temp))
        dt = info['sky_temp']['dt']
        if dt == -999:
            dt_str = rtxt('ERR')
        elif dt > params.WEATHER_TIMEOUT:
            dt_str = rtxt('{:.0f}'.format(dt))
        else:
            dt_str = gtxt('{:.0f}'.format(dt))
        msg += '  sky_temp     {}°C          dt={}\n'.format(sky_temp_str, dt_str)

        clouds = info['clouds']
        if clouds == -999:
            clouds_str = rtxt('  ERR')
        elif clouds < params.MAX_SATCLOUDS:
            clouds_str = ytxt('{:>5.1f}'.format(clouds))
            if clouds < params.MAX_SATCLOUDS - 5:
                clouds_str = gtxt('{:>5.1f}'.format(clouds))
        else:
            clouds_str = rtxt('{:>5.1f}'.format(clouds))
        dt_str = 'N/A'  # TODO: we don't get the image time for clouds
        msg += '  sat_clouds   {}%           dt={}\n'.format(clouds_str, dt_str)

        if params.SITE_NAME == 'La Palma':
            dust = info['tng']['dust']
            if dust == -999:
                dust_str = rtxt('  ERR')
            elif dust < params.MAX_DUSTLEVEL:
                dust_str = ytxt('{:>5.1f}'.format(dust))
                if dust < params.MAX_DUSTLEVEL - 10:
                    dust_str = gtxt('{:>5.1f}'.format(dust))
            else:
                dust_str = rtxt('{:>5.1f}'.format(dust))
            dt = info['tng']['dust_dt']
            if dt == -999:
                dt_str = rtxt('ERR')
            elif dt > params.DUSTLEVEL_TIMEOUT:
                dt_str = rtxt('{:.0f}'.format(dt))
            else:
                dt_str = gtxt('{:.0f}'.format(dt))
            msg += '  dust (tng)   {} μg/m³      dt={}\n'.format(dust_str, dt_str)

        if params.SITE_NAME == 'La Palma':
            seeing = info['tng']['seeing']
            dt = info['tng']['seeing_dt']
            if seeing == -999:
                seeing_str = rtxt('ERR')
            else:
                seeing_str = boldtxt('{:>3.1f}'.format(seeing))
            if dt == -999:
                dt_str = rtxt('ERR')
            elif dt > params.SEEING_TIMEOUT:
                dt_str = rtxt('{:.0f}'.format(dt))
            else:
                dt_str = gtxt('{:.0f}'.format(dt))
            msg += '  seeing (tng)   {}"           dt={}\n'.format(seeing_str, dt_str)

        if params.SITE_NAME == 'La Palma':
            seeing = info['robodimm']['seeing']
            dt = info['robodimm']['dt']
            if seeing == -999:
                seeing_str = rtxt('ERR')
            else:
                seeing_str = boldtxt('{:>3.1f}'.format(seeing))
            if dt == -999:
                dt_str = rtxt('ERR')
            elif dt > params.SEEING_TIMEOUT:
                dt_str = rtxt('{:.0f}'.format(dt))
            else:
                dt_str = gtxt('{:.0f}'.format(dt))
            msg += '  seeing (ing)   {}"           dt={}\n'.format(seeing_str, dt_str)

        sunalt = info['sunalt']
        if sunalt < 0:
            sunalt_str = ytxt('{:>+5.1f}'.format(sunalt))
            if sunalt < params.SUN_ELEVATION_LIMIT:
                sunalt_str = gtxt('{:>+5.1f}'.format(sunalt))
        else:
            sunalt_str = rtxt('{:>+5.1f}'.format(sunalt))
        msg += '  {: <10}   {}°\n'.format('sunalt', sunalt_str)

        msg += 'OTHER:\n'
        ups_percents = info['ups_percent']
        ups_strings = []
        for ups_percent in ups_percents:
            if ups_percent < params.MIN_UPSBATTERY:
                ups_strings.append(rtxt('{:>5.1f}'.format(ups_percent)))
            else:
                ups_strings.append(gtxt('{:>5.1f}'.format(ups_percent)))
        msg += '  {: <10}   {}%\n'.format('ups', '%  '.join(ups_strings))

        free_diskspace = info['free_diskspace']
        if free_diskspace < (params.MIN_DISKSPACE * 2):
            diskspace_str = ytxt('{:>5.1f}'.format(free_diskspace))
            if free_diskspace < params.MIN_DISKSPACE:
                diskspace_str = rtxt('{:>5.1f}'.format(free_diskspace))
        else:
            diskspace_str = gtxt('{:>5.1f}'.format(free_diskspace))
        msg += '  {: <10}   {}%\n'.format('diskspace', diskspace_str)

        return msg.rstrip()

    def get_limits_string(self, force_update=False):
        """Get a string for printing weather values and limits."""
        info = self.get_info(force_update)
        if info is None:
            msg = 'WEATHER:\n'
            msg += '  None yet, try again'
            return msg

        weather = info['weather']
        internal = info['internal']

        msg = 'TEMPERATURE:\n'
        for source in weather:
            if 'temperature' not in weather[source]:
                continue
            msg += '  {: <10}\t'.format(source)
            temperature = weather[source]['temperature']
            if temperature == -999:
                status = rtxt('ERROR')
                temperature_str = rtxt(' ERR')
            elif (temperature < params.MAX_TEMPERATURE and
                    temperature > params.MIN_TEMPERATURE):
                status = gtxt('Good')
                temperature_str = ytxt('{:>4.1f}'.format(temperature))
                if (temperature < params.MAX_TEMPERATURE - 1 and
                        temperature > params.MIN_TEMPERATURE + 1):
                    temperature_str = gtxt('{:>4.1f}'.format(temperature))
            else:
                status = rtxt('Bad')
                temperature_str = rtxt('{:>4.1f}'.format(temperature))
            msg += ' {}°C       (min={:.1f}°C max={:.1f}°C) \t : {}\n'.format(
                temperature_str, params.MIN_TEMPERATURE, params.MAX_TEMPERATURE, status)

        # internal sensors
        for source in internal['temperature']:
            msg += '  {: <10}\t'.format('{}_int'.format(source))
            temperature = internal['temperature'][source]
            if temperature == -999:
                status = rtxt('ERROR')
                temperature_str = rtxt(' ERR')
            elif (temperature < params.MAX_INTERNAL_TEMPERATURE and
                  temperature > params.MIN_INTERNAL_TEMPERATURE):
                status = gtxt('Good')
                temperature_str = ytxt('{:>4.1f}'.format(temperature))
                if (temperature < params.MAX_INTERNAL_TEMPERATURE - 1 and
                        temperature > params.MIN_INTERNAL_TEMPERATURE + 1):
                    temperature_str = gtxt('{:>4.1f}'.format(temperature))
            else:
                status = rtxt('Bad')
                temperature_str = rtxt('{:>4.1f}'.format(temperature))
            msg += ' {}°C       (min={:.1f}°C max={:.1f}°C) \t : {}\n'.format(
                temperature_str,
                params.MIN_INTERNAL_TEMPERATURE,
                params.MAX_INTERNAL_TEMPERATURE,
                status,
            )

        msg += 'HUMIDITY:\n'
        for source in weather:
            if 'humidity' not in weather[source]:
                continue
            msg += '  {: <10}\t'.format(source)
            humidity = weather[source]['humidity']
            if humidity == -999:
                status = rtxt('ERROR')
                humidity_str = rtxt('  ERR')
            elif (humidity < params.MAX_HUMIDITY):
                status = gtxt('Good')
                humidity_str = ytxt('{:>5.1f}'.format(humidity))
                if (humidity < params.MAX_HUMIDITY - 5):
                    humidity_str = gtxt('{:>5.1f}'.format(humidity))
            else:
                status = rtxt('Bad')
                humidity_str = rtxt('{:>5.1f}'.format(humidity))
            msg += '{}%        (max={:.1f}%)            \t : {}\n'.format(
                humidity_str, params.MAX_HUMIDITY, status)

        # internal sensors
        for source in internal['humidity']:
            msg += '  {: <10}\t'.format('{}_int'.format(source))
            humidity = internal['humidity'][source]
            if humidity == -999:
                status = rtxt('ERROR')
                humidity_str = rtxt('  ERR')
            elif (humidity < params.MAX_INTERNAL_HUMIDITY):
                status = gtxt('Good')
                humidity_str = ytxt('{:>5.1f}'.format(humidity))
                if (humidity < params.MAX_INTERNAL_HUMIDITY - 5):
                    humidity_str = gtxt('{:>5.1f}'.format(humidity))
            else:
                status = rtxt('Bad')
                humidity_str = rtxt('{:>5.1f}'.format(humidity))
            msg += '{}%        (max={:.1f}%)            \t : {}\n'.format(
                humidity_str, params.MAX_INTERNAL_HUMIDITY, status)

        msg += 'DEW POINT:\n'
        for source in weather:
            if 'dew_point' not in weather[source]:
                continue
            msg += '  {: <10}\t'.format(source)
            dewpoint = weather[source]['dew_point']
            if dewpoint == -999:
                status = rtxt('ERROR')
                dewpoint_str = rtxt('  ERR')
            elif (dewpoint > params.MIN_DEWPOINT):
                status = gtxt('Good')
                dewpoint_str = ytxt('{:>+5.1f}'.format(dewpoint))
                if (dewpoint > params.MIN_DEWPOINT + 1):
                    dewpoint_str = gtxt('{:>+5.1f}'.format(dewpoint))
            else:
                status = rtxt('Bad')
                dewpoint_str = rtxt('{:>+5.1f}'.format(dewpoint))
            msg += '{}°C       (min={:+.1f}°C)           \t : {}\n'.format(
                dewpoint_str, params.MIN_DEWPOINT, status)

        msg += 'WIND SPEED:\n'
        for source in weather:
            if 'windgust' not in weather[source]:
                continue
            msg += '  {: <10}\t'.format(source)
            windgust = weather[source]['windgust']
            if windgust == -999:
                status = rtxt('ERROR')
                windgust_str = rtxt(' ERR')
            elif (windgust < params.MAX_WINDSPEED):
                status = gtxt('Good')
                windgust_str = ytxt('{:>4.1f}'.format(windgust))
                if (windgust < params.MAX_WINDSPEED - 5):
                    windgust_str = gtxt('{:>4.1f}'.format(windgust))
            else:
                status = rtxt('Bad')
                windgust_str = rtxt('{:>4.1f}'.format(windgust))
            msg += ' {} km/h    (max={:.1f} km/h)        \t : {}\n'.format(
                windgust_str, params.MAX_WINDSPEED, status)

        msg += 'WIND GUST ({:.0f} min maximum):\n'.format(self.windgust_period / 60)
        for source in weather:
            if 'windmax' not in weather[source]:
                continue
            msg += '  {: <10}\t'.format(source)
            windmax = weather[source]['windmax']
            if windmax == -999:
                status = rtxt('ERROR')
                windmax_str = rtxt(' ERR')
            elif (windmax < params.MAX_WINDGUST):
                status = gtxt('Good')
                windmax_str = ytxt('{:>4.1f}'.format(windmax))
                if (windmax < params.MAX_WINDGUST - 5):
                    windmax_str = gtxt('{:>4.1f}'.format(windmax))
            else:
                status = rtxt('Bad')
                windmax_str = rtxt('{:>4.1f}'.format(windmax))
            msg += ' {} km/h    (max={:.1f} km/h)        \t : {}\n'.format(
                windmax_str, params.MAX_WINDGUST, status)

        msg += 'INTERNAL (alert limits):\n'

        for source in internal['temperature']:
            msg += '  {: <10}\t'.format('{}_int'.format(source))
            temperature = internal['temperature'][source]
            if temperature == -999:
                status = rtxt('ERROR')
                temperature_str = rtxt(' ERR')
            elif (temperature > params.MIN_INTERNAL_TEMPERATURE_ALERT):
                status = gtxt('Good')
                temperature_str = ytxt('{:>4.1f}'.format(temperature))
                if (temperature > params.MIN_INTERNAL_TEMPERATURE_ALERT + 1):
                    temperature_str = gtxt('{:>4.1f}'.format(temperature))
            else:
                status = rtxt('Bad')
                temperature_str = rtxt('{:>4.1f}'.format(temperature))
            msg += ' {}°C       (min={:.1f}°C          \t : {}\n'.format(
                temperature_str, params.MIN_INTERNAL_TEMPERATURE_ALERT, status)

        for source in internal['humidity']:
            msg += '  {: <10}\t'.format('{}_int'.format(source))
            humidity = internal['humidity'][source]
            if humidity == -999:
                status = rtxt('ERROR')
                humidity_str = rtxt('  ERR')
            elif (humidity < params.MAX_INTERNAL_HUMIDITY_ALERT):
                status = gtxt('Good')
                humidity_str = ytxt('{:>5.1f}'.format(humidity))
                if (humidity < params.MAX_INTERNAL_HUMIDITY_ALERT - 5):
                    humidity_str = gtxt('{:>5.1f}'.format(humidity))
            else:
                status = rtxt('Bad')
                humidity_str = rtxt('{:>5.1f}'.format(humidity))
            msg += '{}%        (max={:.1f}%)           \t : {}\n'.format(
                humidity_str, params.MAX_INTERNAL_HUMIDITY_ALERT, status)

        msg += 'ENVIRONMENT:\n'

        if params.SITE_NAME == 'La Palma':
            msg += '  {: <10}\t'.format('dust_level')
            dust = info['tng']['dust']
            if dust == -999:
                status = rtxt('ERROR')
                dust_str = rtxt('  ERR')
            elif dust < params.MAX_DUSTLEVEL:
                status = gtxt('Good')
                dust_str = ytxt('{:>5.1f}'.format(dust))
                if dust < params.MAX_DUSTLEVEL - 10:
                    dust_str = gtxt('{:>5.1f}'.format(dust))
            else:
                status = rtxt('Bad')
                dust_str = rtxt('{:>5.1f}'.format(dust))
            msg += '{} μg/m³   (max={:.1f} μg/m³)      \t : {}\n'.format(
                dust_str, params.MAX_DUSTLEVEL, status)

        msg += '  {: <10}\t'.format('sky_temp')
        sky_temp = info['sky_temp']['sky_temp']
        if sky_temp == -999:
            status = rtxt('ERROR')
            sky_temp_str = rtxt('  ERR')
        elif sky_temp < params.MAX_SKYTEMP:
            status = gtxt('Good')
            sky_temp_str = ytxt('{:>5.1f}'.format(sky_temp))
            if sky_temp < params.MAX_SKYTEMP - 5:
                sky_temp_str = gtxt('{:>5.1f}'.format(sky_temp))
        else:
            status = rtxt('Bad')
            sky_temp_str = rtxt('{:>5.1f}'.format(sky_temp))
        msg += '{}°C       (max={:.1f}°C)            \t : {}\n'.format(
            sky_temp_str, params.MAX_SKYTEMP, status)

        msg += '  {: <10}\t'.format('sat_clouds')
        clouds = info['clouds']
        if clouds == -999:
            status = rtxt('ERROR')
            clouds_str = rtxt('  ERR')
        elif clouds < params.MAX_SATCLOUDS:
            status = gtxt('Good')
            clouds_str = ytxt('{:>5.1f}'.format(clouds))
            if clouds < params.MAX_SATCLOUDS - 5:
                clouds_str = gtxt('{:>5.1f}'.format(clouds))
        else:
            status = rtxt('Bad')
            clouds_str = rtxt('{:>5.1f}'.format(clouds))
        msg += '{}%        (max={:.1f}%)            \t : {}\n'.format(
            clouds_str, params.MAX_SATCLOUDS, status)

        msg += '  {: <10}\t'.format('sunalt')
        sunalt = info['sunalt']
        if sunalt < 0:
            status = rtxt('Bad')
            sunalt_str = ytxt('{:>+5.1f}'.format(sunalt))
            if sunalt < params.SUN_ELEVATION_LIMIT:
                status = gtxt('Good')
                sunalt_str = gtxt('{:>+5.1f}'.format(sunalt))
        else:
            status = rtxt('Bad')
            sunalt_str = rtxt('{:>+5.1f}'.format(sunalt))
        msg += '{}°        (max={:.1f}°)           \t : {}\n'.format(
            sunalt_str, params.SUN_ELEVATION_LIMIT, status)

        msg += 'OTHER:\n'

        for i, ups_percent in enumerate(info['ups_percent']):
            msg += '  {: <10}\t'.format(f'ups{i+1}')
            if ups_percent == -999:
                status = rtxt('ERROR')
                ups_str = rtxt('  ERR')
            elif ups_percent > params.MIN_UPSBATTERY:
                status = gtxt('Good')
                ups_str = ytxt('{:>5.1f}'.format(ups_percent))
                if ups_percent > 99.99:
                    ups_str = gtxt('{:>5.1f}'.format(ups_percent))
            else:
                status = rtxt('Bad')
                ups_str = rtxt('{:>5.1f}'.format(ups_percent))
            msg += '{}%        (min={:.1f}%)            \t : {}\n'.format(
                ups_str, params.MIN_UPSBATTERY, status)

        msg += '  {: <10}\t'.format('diskspace')
        free_diskspace = info['free_diskspace']
        if free_diskspace == -999:
            status = rtxt('ERROR')
            diskspace_str = rtxt('  ERR')
        elif free_diskspace > params.MIN_DISKSPACE:
            status = gtxt('Good')
            diskspace_str = ytxt('{:>5.1f}'.format(free_diskspace))
            if free_diskspace > (params.MIN_DISKSPACE * 2):
                diskspace_str = gtxt('{:>5.1f}'.format(free_diskspace))
        else:
            status = rtxt('Bad')
            diskspace_str = rtxt('{:>5.1f}'.format(free_diskspace))
        msg += '{}%        (min={:.1f}%)            \t : {}\n'.format(
            diskspace_str, params.MIN_DISKSPACE, status)

        return msg.rstrip()


if __name__ == '__main__':
    daemon = ConditionsDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
