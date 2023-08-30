#!/usr/bin/env python3
"""Daemon to monitor environmental conditions."""

import json
import os
import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.common.style import boldtxt, gtxt, rtxt, ytxt
from gtecs.control import params
from gtecs.control.astronomy import get_sunalt
from gtecs.control.conditions.clouds import get_satellite_clouds
from gtecs.control.conditions.external import get_aat, get_ing, get_robodimm, get_tng
from gtecs.control.conditions.internal import get_domealert_daemon
from gtecs.control.conditions.local import get_vaisala_daemon, get_rain_daemon, get_rain_domealert
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
        self.check_period = params.WEATHER_INTERVAL

        self.info_flag_names = ['clouds',
                                'dark',
                                'dust',
                                ]
        self.normal_flag_names = ['rain',
                                  'windspeed',
                                  'windgust',
                                  'humidity',
                                  'temperature',
                                  'dew_point',
                                  ]
        self.critical_flag_names = ['ups',
                                    'link',
                                    'diskspace',
                                    'internal',
                                    'ice',
                                    'override',
                                    ]
        self.flag_names = self.info_flag_names + self.normal_flag_names + self.critical_flag_names

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
        self.check_period = params.DAEMON_CHECK_PERIOD
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
        try:
            weather = {}

            # Get the weather values from local and external stations
            weather_sources = [params.VAISALA_URI_PRIMARY]
            if params.VAISALA_URI_SECONDARY != 'none':
                weather_sources.append(params.VAISALA_URI_SECONDARY)
            for source in params.EXTERNAL_WEATHER_SOURCES:
                if source != 'none' and source not in weather_sources:
                    weather_sources.append(source.lower())
            for source in weather_sources:
                try:
                    if source.startswith('PYRO:'):
                        uri = source
                        source = uri[5:].split('_')[0]
                        weather_dict = get_vaisala_daemon(uri)
                    elif source == 'ing':
                        weather_dict = get_ing()
                    elif source == 'aat':
                        weather_dict = get_aat()
                    else:
                        raise ValueError('Unknown weather source')
                except Exception:
                    if params.FAKE_CONDITIONS:
                        weather_dict = {'temperature': 10,
                                        'pressure': 800,
                                        'windspeed': 5,
                                        'winddir': 0,
                                        'windgust': 10,
                                        'humidity': 50,
                                        'rain': False,
                                        'dew_point': 10,
                                        'update_time': Time.now().iso,
                                        'dt': 0,
                                        }
                    else:
                        self.log.error('Error getting weather from "{}"'.format(source))
                        self.log.debug('', exc_info=True)
                        weather_dict = {'temperature': -999,
                                        'pressure': -999,
                                        'windspeed': -999,
                                        'winddir': -999,
                                        'windgust': -999,
                                        'humidity': -999,
                                        'rain': -999,
                                        'dew_point': -999,
                                        'update_time': -999,
                                        'dt': -999,
                                        }

                # Format source key
                source = source.lower()

                try:
                    # Save a history of windgusts so we can log the maximum
                    if (self.info and source in self.info['weather'] and
                            'windgust_history' in self.info['weather'][source] and
                            self.info['weather'][source]['windgust_history'] != -999):
                        windgust_history = self.info['weather'][source]['windgust_history']
                    else:
                        windgust_history = []
                    # remove old readings (limit to params.WINDGUST_PERIOD) and any invalid values
                    windgust_history = [hist for hist in windgust_history
                                        if (hist[0] > self.loop_time - params.WINDGUST_PERIOD and
                                            hist[1] != -999)]
                    if weather_dict['windgust'] != -999:
                        # add the latest value
                        windgust_history.append((self.loop_time, weather_dict['windgust']))
                    # save the history
                    weather_dict['windgust_history'] = windgust_history
                    # store maximum (windmax)
                    if len(windgust_history) > 1:
                        weather_dict['windmax'] = max(hist[1] for hist in windgust_history)
                    else:
                        weather_dict['windmax'] = -999
                except Exception:
                    self.log.error('Error getting windmax for "{}"'.format(source))
                    self.log.debug('', exc_info=True)
                    weather_dict['windmax'] = -999

                # Store the dict
                weather[source] = weather_dict

            temp_info['weather'] = {}
            for source in weather:
                source_info = weather[source].copy()

                # check if the weather timeout has been exceeded
                dt = source_info['dt']
                if dt >= params.WEATHER_TIMEOUT or dt == -999:
                    self.log.error('Timeout exceeded for source "{}" ({:.1f} > {:.1f})'.format(
                        source, dt, params.WEATHER_TIMEOUT))
                    source_info = {key: -999 for key in source_info}

                # check if the weather hasn't changed for a certain time
                source_info['changed_time'] = self.loop_time
                if self.info and self.info['weather'][source]:
                    changed_time = self.info['weather'][source]['changed_time']
                    unchanged = [source_info[key] == self.info['weather'][source][key]
                                 for key in source_info]
                    dt = self.loop_time - changed_time
                    if all(unchanged) and dt > params.WEATHER_STATIC:
                        self.log.error('Values unchanged for source "{}" ({:.1f} > {:.1f})'.format(
                            source, dt, params.WEATHER_STATIC))
                        source_info = {key: -999 for key in source_info}
                        source_info['changed_time'] = changed_time

                temp_info['weather'][source] = source_info
        except Exception:
            self.log.error('Failed to get weather info')
            self.log.debug('', exc_info=True)
            temp_info['weather'] = None

        # Get the internal conditions from Paul's DomeAlert
        try:
            internal_dict = get_domealert_daemon(params.DOMEALERT_URI)
        except Exception:
            if params.FAKE_CONDITIONS:
                internal_dict = {'temperature': 10,
                                 'humidity': 25,
                                 'update_time': Time.now().iso,
                                 'dt': 0,
                                 }
            else:
                self.log.error('Failed to get internal info')
                self.log.debug('', exc_info=True)
                internal_dict = {'temperature': -999,
                                 'humidity': -999,
                                 'update_time': -999,
                                 'dt': -999,
                                 }
        temp_info['internal'] = internal_dict

        # Get rain board readings
        try:
            if params.RAINDAEMON_URI != 'none':
                if 'domealert' in params.RAINDAEMON_URI:
                    rain_dict = get_rain_domealert(params.RAINDAEMON_URI)
                else:
                    rain_dict = get_rain_daemon(params.RAINDAEMON_URI)
                # Remove any other readings
                if temp_info['weather'] is not None:
                    for source in temp_info['weather']:
                        if 'rain' in temp_info['weather'][source]:
                            temp_info['weather'][source]['rain'] = None
            elif temp_info['weather'] is not None:
                # Fallback to the weather readings
                # We don't usually trust the vaisala rain detectors,
                # and any external ones are too far away.
                # That's why we prefer the local boards.
                rain_dict = {'total': 0,
                             'unsafe': 0,
                             }
                for source in temp_info['weather']:
                    if ('rain' in temp_info['weather'][source] and
                            temp_info['weather'][source]['rain'] != -999):
                        rain_dict['total'] += 1
                        rain_dict['unsafe'] += int(temp_info['weather'][source]['rain'])
                rain_dict['rain'] = rain_dict['unsafe'] > 0
                if rain_dict['total'] == 0:
                    raise ValueError('No weather sources included rain readings')
            else:
                raise ValueError('No weather sources for rain readings')
        except Exception:
            if params.FAKE_CONDITIONS:
                rain_dict = {'total': 9,
                             'unsafe': 0,
                             'rain': False,
                             }
            else:
                self.log.error('Failed to get rain info')
                self.log.debug('', exc_info=True)
                rain_dict = {'total': -999,
                             'unsafe': -999,
                             'rain': -999,
                             }
        temp_info['rain'] = rain_dict

        # Get seeing and dust from the TNG webpage (La Palma only)
        try:
            if params.SITE_NAME == 'La Palma':
                tng_dict = get_tng()
                # check if the timeouts have been exceeded
                if tng_dict['seeing_dt'] >= params.SEEING_TIMEOUT or tng_dict['seeing_dt'] == -999:
                    tng_dict['seeing'] = -999
                if tng_dict['dust_dt'] >= params.DUSTLEVEL_TIMEOUT or tng_dict['dust_dt'] == -999:
                    tng_dict['dust'] = -999
            else:
                tng_dict = {'seeing': -999,
                            'seeing_dt': -999,
                            'dust': -999,
                            'dust_dt': -999,
                            }
        except Exception:
            if params.FAKE_CONDITIONS:
                tng_dict = {'seeing': 1.2,
                            'seeing_dt': 0,
                            'dust': 0,
                            'dust_dt': 0,
                            }
            else:
                self.log.error('Failed to get TNG info')
                self.log.debug('', exc_info=True)
                tng_dict = {'seeing': -999,
                            'seeing_dt': -999,
                            'dust': -999,
                            'dust_dt': -999,
                            }
        temp_info['tng'] = tng_dict

        # Get seeing from the ING RoboDIMM (La Palma only)
        try:
            if params.SITE_NAME == 'La Palma':
                dimm_dict = get_robodimm()
                # check if the timeout has been exceeded
                if dimm_dict['dt'] >= params.SEEING_TIMEOUT or dimm_dict['dt'] == -999:
                    dimm_dict['seeing'] = -999
            else:
                dimm_dict = {'seeing': -999,
                             'dt': -999,
                             }
        except Exception:
            if params.FAKE_CONDITIONS:
                dimm_dict = {'seeing': 1.2,
                             'dt': 0,
                             }
            else:
                self.log.error('Failed to get DIMM info')
                self.log.debug('', exc_info=True)
                dimm_dict = {'seeing': -999,
                             'dt': -999,
                             }
        temp_info['robodimm'] = dimm_dict

        # Get info from the UPSs
        try:
            ups_percent, ups_status = get_ups()
            temp_info['ups_percent'] = ups_percent
            temp_info['ups_status'] = ups_status
        except Exception:
            if params.FAKE_CONDITIONS:
                temp_info['ups_percent'] = [100, 100]
                temp_info['ups_status'] = [True, True]
            else:
                self.log.error('Failed to get UPS info')
                self.log.debug('', exc_info=True)
                temp_info['ups_percent'] = -999
                temp_info['ups_status'] = -999

        # Get info from the link ping check
        try:
            pings = [check_ping(url) for url in params.LINK_URLS]
            temp_info['pings'] = pings
        except Exception:
            if params.FAKE_CONDITIONS:
                temp_info['pings'] = [True, True]
            else:
                self.log.error('Failed to get link info')
                self.log.debug('', exc_info=True)
                temp_info['pings'] = -999

        # Get info from the disk usage check
        try:
            free_diskspace = get_diskspace_remaining(params.IMAGE_PATH) * 100.
            temp_info['free_diskspace'] = free_diskspace
        except Exception:
            if params.FAKE_CONDITIONS:
                temp_info['free_diskspace'] = 90
            else:
                self.log.error('Failed to get diskspace info')
                self.log.debug('', exc_info=True)
                temp_info['free_diskspace'] = -999

        # Get info from the satellite IR cloud image
        try:
            clouds = get_satellite_clouds(site=params.SITE_NAME) * 100
            temp_info['clouds'] = clouds
        except Exception:
            if params.FAKE_CONDITIONS:
                temp_info['clouds'] = 0
            else:
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
        temp_info['critical_flags'] = sorted(self.critical_flag_names)
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
        rain = np.array(self.info['rain']['rain'])
        rain = rain[rain != -999]

        # Internal
        int_temperature = np.array(self.info['internal']['temperature'])
        int_humidity = np.array(self.info['internal']['humidity'])

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
        valid = {flag: False for flag in self.flag_names}
        good_delay = {flag: 0 for flag in self.flag_names}
        bad_delay = {flag: 0 for flag in self.flag_names}
        error_delay = 60

        # windspeed flag (based on instantaneous windgust)
        good['windspeed'] = np.all(windgust < params.MAX_WINDSPEED)
        valid['windspeed'] = len(windgust) >= 1
        good_delay['windspeed'] = params.WINDSPEED_GOODDELAY
        bad_delay['windspeed'] = params.WINDSPEED_BADDELAY

        # windgust flag (based on historic windgust maximum)
        good['windgust'] = np.all(windmax < params.MAX_WINDGUST)
        valid['windgust'] = len(windmax) >= 1
        good_delay['windgust'] = params.WINDGUST_GOODDELAY
        bad_delay['windgust'] = params.WINDGUST_BADDELAY

        # temperature flag
        good['temperature'] = (np.all(ext_temperature > params.MIN_TEMPERATURE) and
                               np.all(ext_temperature < params.MAX_TEMPERATURE) and
                               np.all(int_temperature > params.MIN_INTERNAL_TEMPERATURE) and
                               np.all(int_temperature < params.MAX_INTERNAL_TEMPERATURE))
        valid['temperature'] = len(ext_temperature) >= 1 and len(int_temperature) >= 1
        good_delay['temperature'] = params.TEMPERATURE_GOODDELAY
        bad_delay['temperature'] = params.TEMPERATURE_BADDELAY

        # ice flag
        good['ice'] = np.all(ext_temperature > 0)
        valid['ice'] = len(ext_temperature) >= 1
        good_delay['ice'] = params.ICE_GOODDELAY
        bad_delay['ice'] = params.ICE_BADDELAY

        # humidity flag
        good['humidity'] = (np.all(ext_humidity < params.MAX_HUMIDITY) and
                            np.all(int_humidity < params.MAX_INTERNAL_HUMIDITY))
        valid['humidity'] = len(ext_humidity) >= 1 and len(int_humidity) >= 1
        good_delay['humidity'] = params.HUMIDITY_GOODDELAY
        bad_delay['humidity'] = params.HUMIDITY_BADDELAY

        # dew_point flag
        good['dew_point'] = np.all(dew_point > params.MIN_DEWPOINT)
        valid['dew_point'] = len(dew_point) >= 1
        good_delay['dew_point'] = params.DEWPOINT_GOODDELAY
        bad_delay['dew_point'] = params.DEWPOINT_BADDELAY

        # rain flag
        good['rain'] = np.all(rain == 0)
        valid['rain'] = len(rain) >= 1
        good_delay['rain'] = params.RAIN_GOODDELAY
        bad_delay['rain'] = params.RAIN_BADDELAY

        # internal flag
        good['internal'] = (np.all(int_humidity < params.CRITICAL_INTERNAL_HUMIDITY) and
                            np.all(int_temperature > params.CRITICAL_INTERNAL_TEMPERATURE))
        valid['internal'] = len(int_humidity) >= 1 and len(int_temperature) >= 1
        good_delay['internal'] = params.INTERNAL_GOODDELAY
        bad_delay['internal'] = params.INTERNAL_BADDELAY

        # dust flag
        good['dust'] = np.all(dust < params.MAX_DUSTLEVEL)
        valid['dust'] = len(dust) >= 1
        good_delay['dust'] = params.DUSTLEVEL_GOODDELAY
        bad_delay['dust'] = params.DUSTLEVEL_BADDELAY

        # ups flag
        good['ups'] = (np.all(ups_percent > params.MIN_UPSBATTERY) and
                       np.all(ups_status == 1))
        valid['ups'] = len(ups_percent) >= 1 and len(ups_status) >= 1
        good_delay['ups'] = params.UPS_GOODDELAY
        bad_delay['ups'] = params.UPS_BADDELAY

        # link flag
        good['link'] = np.all(pings == 1)
        valid['link'] = len(pings) >= 1
        good_delay['link'] = params.LINK_GOODDELAY
        bad_delay['link'] = params.LINK_BADDELAY

        # diskspace flag
        good['diskspace'] = np.all(free_diskspace > params.MIN_DISKSPACE)
        valid['diskspace'] = len(free_diskspace) >= 1
        good_delay['diskspace'] = 0
        bad_delay['diskspace'] = 0

        # clouds flag
        good['clouds'] = np.all(clouds < params.MAX_SATCLOUDS)
        valid['clouds'] = len(clouds) >= 1
        good_delay['clouds'] = params.SATCLOUDS_GOODDELAY
        bad_delay['clouds'] = params.SATCLOUDS_BADDELAY

        # dark flag
        good['dark'] = np.all(sunalt < params.SUN_ELEVATION_LIMIT)
        valid['dark'] = len(sunalt) >= 1
        good_delay['dark'] = 0
        bad_delay['dark'] = 0

        # override flag
        good['override'] = not self.manual_override
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

            # check if bad
            if valid[flag] and not good[flag] and self.flags[flag] != 1:
                dt = current_time - self.update_times[flag]
                if dt > bad_delay[flag] or self.flags[flag] == 2:
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
        data['critical_flags'] = sorted(self.critical_flag_names)
        data['ignored_flags'] = sorted(self.ignored_flags)
        with open(self.flags_file, 'w') as f:
            json.dump(data, f)

        # ~~~~~~~~~~~~~~
        # Trigger Slack alerts for critical flags
        for flag in self.critical_flag_names:
            if flag in self.ignored_flags:
                # If we're ignoring the flag then don't send an alert
                continue
            if old_flags[flag] == 0 and self.flags[flag] == 1:
                # The flag has been set to bad
                self.log.warning('Critical flag {} set to bad'.format(flag))
                send_slack_msg('Conditions reports {} flag has been set to bad'.format(flag))
            elif old_flags[flag] == 0 and self.flags[flag] == 2:
                # The flag has been set to ERROR
                self.log.warning('Critical flag {} set to ERROR'.format(flag))
                send_slack_msg('Conditions reports {} flag has been set to ERROR'.format(flag))
            elif old_flags[flag] in [1, 2] and self.flags[flag] == 0:
                # The flag has been set to good
                self.log.warning('Critical flag {} set to good'.format(flag))
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

    # Info function
    def get_info_string(self, force_update=False):
        """Get a string for printing status info."""
        info = self.get_info(force_update)

        if info is None or info['flags'] is None or info['weather'] is None:
            msg = 'CONDITIONS:\n'
            msg += '  None yet, try again'
            return msg

        msg = 'FLAGS ({}):\n'.format(info['timestamp'])
        flags = info['flags']
        normal_flags = sorted(info['normal_flags'])
        critical_flags = sorted(info['critical_flags'])
        ignored_flags = sorted(info['ignored_flags'])
        for i in range(max(len(normal_flags), len(critical_flags))):
            # Print normal flags on the left, and critical flags on the right
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
                msg += '  {: >12} : {: <16} ({})'.format(flag, status, flags[flag])
            else:
                msg += '                          '

            if len(critical_flags) >= i + 1:
                flag = critical_flags[i]
                if flag in ignored_flags:
                    status = '----' + '\u200c' * 11
                elif flags[flag] == 0:
                    status = gtxt('Good')
                elif flags[flag] == 1:
                    status = rtxt('Bad')
                else:
                    status = rtxt('ERROR')
                msg += '  {: >12} : {: <16} ({})\n'.format(flag, status, flags[flag])
            else:
                msg += ''

        msg += 'WEATHER:          temp   humid    dewpt  wind (gust, max)       rain\n'
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

        temperature = info['internal']['temperature']
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

        humidity = info['internal']['humidity']
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

        msg += '  {: <10}\t'.format('dome_int')
        weather_str = '{}°C  {}%                                         dt={}\n'.format(
            temperature_str, humidity_str, dt_str)
        msg += weather_str

        rain_unsafe = info['rain']['unsafe']
        rain_total = info['rain']['total']
        if info['rain']['rain'] == -999:
            rain_str = rtxt('  ERR')
        elif rain_unsafe > 0:
            rain_str = rtxt('  True') + '   ({}/{})'.format(rain_unsafe, rain_total)
        else:
            rain_str = gtxt(' False') + '   ({}/{})'.format(rain_unsafe, rain_total)

        msg += '  {: <10}\t'.format('rain')
        msg += rain_str

        msg += 'CONDITIONS:\n'

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

        clouds = info['clouds']
        if clouds == -999:
            clouds_str = rtxt('  ERR')
        elif clouds < params.MAX_SATCLOUDS:
            clouds_str = ytxt('{:>5.1f}'.format(clouds))
            if clouds < params.MAX_SATCLOUDS - 5:
                clouds_str = gtxt('{:>5.1f}'.format(clouds))
        else:
            clouds_str = rtxt('{:>5.1f}'.format(clouds))
        msg += '  {: <10}   {}%\n'.format('sat_clouds', clouds_str)

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
        if info is None or info['weather'] is None:
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
            min_temp = params.MIN_TEMPERATURE
            max_temp = params.MAX_TEMPERATURE

            temperature = weather[source]['temperature']
            if temperature == -999:
                status = rtxt('ERROR')
                temperature_str = rtxt(' ERR')
            elif (temperature < max_temp and temperature > min_temp):
                status = gtxt('Good')
                temperature_str = ytxt('{:>4.1f}'.format(temperature))
                if temperature < max_temp - 1 and temperature > min_temp + 1:
                    temperature_str = gtxt('{:>4.1f}'.format(temperature))
            else:
                status = rtxt('Bad')
                temperature_str = rtxt('{:>4.1f}'.format(temperature))

            msg += ' {}°C       (min={:.1f}°C max={:.1f}°C) \t : {}\n'.format(
                temperature_str, min_temp, max_temp, status)

        # internal sensors
        msg += '  {: <10}\t'.format('dome_int')
        min_temp = params.MIN_INTERNAL_TEMPERATURE
        max_temp = params.MAX_INTERNAL_TEMPERATURE

        temperature = internal['temperature']
        if temperature == -999:
            status = rtxt('ERROR')
            temperature_str = rtxt(' ERR')
        elif (temperature < max_temp and temperature > min_temp):
            status = gtxt('Good')
            temperature_str = ytxt('{:>4.1f}'.format(temperature))
            if temperature < max_temp - 1 and temperature > min_temp + 1:
                temperature_str = gtxt('{:>4.1f}'.format(temperature))
        else:
            status = rtxt('Bad')
            temperature_str = rtxt('{:>4.1f}'.format(temperature))

        msg += ' {}°C       (min={:.1f}°C max={:.1f}°C) \t : {}\n'.format(
            temperature_str, min_temp, max_temp, status)

        msg += 'HUMIDITY:\n'
        for source in weather:
            if 'humidity' not in weather[source]:
                continue

            msg += '  {: <10}\t'.format(source)
            max_hum = params.MAX_HUMIDITY

            humidity = weather[source]['humidity']
            if humidity == -999:
                status = rtxt('ERROR')
                humidity_str = rtxt('  ERR')
            elif (humidity < max_hum):
                status = gtxt('Good')
                humidity_str = ytxt('{:>5.1f}'.format(humidity))
                if (humidity < max_hum - 5):
                    humidity_str = gtxt('{:>5.1f}'.format(humidity))
            else:
                status = rtxt('Bad')
                humidity_str = rtxt('{:>5.1f}'.format(humidity))

            msg += '{}%        (max={:.1f}%)            \t : {}\n'.format(
                humidity_str, max_hum, status)

        # internal sensors
        msg += '  {: <10}\t'.format(source)
        max_hum = params.MAX_INTERNAL_HUMIDITY

        humidity = weather[source]['humidity']
        if humidity == -999:
            status = rtxt('ERROR')
            humidity_str = rtxt('  ERR')
        elif (humidity < max_hum):
            status = gtxt('Good')
            humidity_str = ytxt('{:>5.1f}'.format(humidity))
            if (humidity < max_hum - 5):
                humidity_str = gtxt('{:>5.1f}'.format(humidity))
        else:
            status = rtxt('Bad')
            humidity_str = rtxt('{:>5.1f}'.format(humidity))

        msg += '{}%        (max={:.1f}%)            \t : {}\n'.format(
            humidity_str, max_hum, status)

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

        msg += 'WIND GUST ({:.0f} min maximum):\n'.format(params.WINDGUST_PERIOD / 60)
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

        msg += 'INTERNAL (critical limits):\n'
        msg += '  {: <10}\t'.format('temperature')

        temperature = internal['temperature']
        if temperature == -999:
            status = rtxt('ERROR')
            temperature_str = rtxt(' ERR')
        elif (temperature > params.CRITICAL_INTERNAL_TEMPERATURE):
            status = gtxt('Good')
            temperature_str = ytxt('{:>4.1f}'.format(temperature))
            if (temperature > params.CRITICAL_INTERNAL_TEMPERATURE + 1):
                temperature_str = gtxt('{:>4.1f}'.format(temperature))
        else:
            status = rtxt('Bad')
            temperature_str = rtxt('{:>4.1f}'.format(temperature))

        msg += ' {}°C       (min={:.1f}°C          \t : {}\n'.format(
            temperature_str, params.CRITICAL_INTERNAL_TEMPERATURE, status)

        msg += '  {: <10}\t'.format('humidity')

        humidity = internal['humidity']
        if humidity == -999:
            status = rtxt('ERROR')
            humidity_str = rtxt('  ERR')
        elif (humidity < params.CRITICAL_INTERNAL_HUMIDITY):
            status = gtxt('Good')
            humidity_str = ytxt('{:>5.1f}'.format(humidity))
            if (humidity < params.CRITICAL_INTERNAL_HUMIDITY - 5):
                humidity_str = gtxt('{:>5.1f}'.format(humidity))
        else:
            status = rtxt('Bad')
            humidity_str = rtxt('{:>5.1f}'.format(humidity))

        msg += '{}%        (max={:.1f}%)           \t : {}\n'.format(
            humidity_str, params.CRITICAL_INTERNAL_HUMIDITY, status)

        msg += 'OTHER:\n'

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

        return msg.rstrip()


if __name__ == '__main__':
    daemon = ConditionsDaemon()
    with make_pid_file(daemon.daemon_id):
        host = params.DAEMONS[daemon.daemon_id]['HOST']
        port = params.DAEMONS[daemon.daemon_id]['PORT']
        pinglife = params.DAEMONS[daemon.daemon_id]['PINGLIFE']
        daemon._run(host, port, pinglife, timeout=params.PYRO_TIMEOUT)
