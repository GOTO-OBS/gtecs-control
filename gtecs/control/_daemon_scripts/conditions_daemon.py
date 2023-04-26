#!/usr/bin/env python3
"""Daemon to monitor environmental conditions."""

import json
import os
import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import conditions
from gtecs.control import params
from gtecs.control.astronomy import get_sunalt
from gtecs.control.daemons import BaseDaemon
from gtecs.control.flags import Status
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
        return

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

            # Get the weather from the local stations
            for source in params.WEATHER_SOURCES:
                try:
                    if source.lower() == 'aat':
                        weather_dict = conditions.get_AAT()
                    elif source.lower() == 'ing':
                        weather_dict = conditions.get_ing()
                    else:
                        weather_dict = conditions.get_vaisala(source)
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

            # Get the W1m rain boards reading
            if params.RAINDAEMON_URI != 'none':
                try:
                    rain = conditions.get_rain()['rain']
                    # Replace the local rain measurements
                    for source in weather:
                        if source == 'w1m':
                            weather[source]['rain'] = rain
                        elif 'rain' in weather[source]:
                            del weather[source]['rain']
                except Exception:
                    self.log.error('Error getting weather from "rain"')
                    self.log.debug('', exc_info=True)
                    self.log.warning('Using vaisala station rain measurements')

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
            internal_dict = conditions.get_domealert()
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

        # Get seeing and dust from the TNG webpage
        try:
            tng_dict = conditions.get_tng()
            # check if the timeouts have been exceeded
            if tng_dict['seeing_dt'] >= params.SEEING_TIMEOUT or tng_dict['seeing_dt'] == -999:
                tng_dict['seeing'] = -999
            if tng_dict['dust_dt'] >= params.DUSTLEVEL_TIMEOUT or tng_dict['dust_dt'] == -999:
                tng_dict['dust'] = -999
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

        # Get seeing from the ING RoboDIMM
        try:
            dimm_dict = conditions.get_robodimm()
            # check if the timeout has been exceeded
            if dimm_dict['dt'] >= params.SEEING_TIMEOUT or dimm_dict['dt'] == -999:
                dimm_dict['seeing'] = -999
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
            ups_percent, ups_status = conditions.get_ups()
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
            pings = [conditions.check_ping(url) for url in params.LINK_URLS]
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
            free_diskspace = conditions.get_diskspace_remaining(params.IMAGE_PATH) * 100.
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
            clouds = conditions.get_satellite_clouds(site='SSO') * 100
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

        # Finally check if we need to report an error
        self._check_errors()

    def _set_flags(self):
        """Set the conditions flags based on the conditions info."""
        # Get the conditions values and filter by validity if needed

        # Weather
        weather = self.info['weather']
        rain = np.array([weather[source]['rain'] for source in weather
                         if 'rain' in weather[source]])
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

        rain = rain[rain != -999]
        windspeed = windspeed[windspeed != -999]
        windgust = windgust[windgust != -999]
        windmax = windmax[windmax != -999]
        ext_temperature = ext_temperature[ext_temperature != -999]
        ext_humidity = ext_humidity[ext_humidity != -999]
        dew_point = dew_point[dew_point != -999]

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
        # At least two of the external sources and one of the internal sources need to be valid,
        # except for rain and wind* because we only have two sources (no SuperWASP),
        # so only need at least one.
        good = {flag: False for flag in self.flag_names}
        valid = {flag: False for flag in self.flag_names}
        good_delay = {flag: 0 for flag in self.flag_names}
        bad_delay = {flag: 0 for flag in self.flag_names}
        error_delay = 60

        # rain flag
        good['rain'] = np.all(rain == 0)
        valid['rain'] = len(rain) >= 1
        good_delay['rain'] = params.RAIN_GOODDELAY
        bad_delay['rain'] = params.RAIN_BADDELAY

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
        # Set flag
        self.force_check_flag = 1

        return 'Updating conditions'

    def ignore_flags(self, flags):
        """Add the given flags to the ignore list."""
        # Check current status
        status = Status()
        if status.mode == 'robotic':
            return 'Can not ignore flags in robotic mode'

        retstrs = []
        for flag in flags:
            # Check current status
            if flag not in self.flags:
                retstrs.append('"{}" is not a recognised flag'.format(flag))
                continue
            elif flag in self.ignored_flags:
                retstrs.append('"{}" flag is already in the ignored list'.format(flag))
                continue
            elif flag == 'override':
                retstrs.append('"{}" flag can not be ignored (use "override on|off")'.format(flag))
                continue

            # Set flag
            self.ignored_flags.append(flag)
            retstrs.append('"{}" flag added to the ignored list'.format(flag))

        # Format return string
        return '\n'.join(retstrs)

    def enable_flags(self, flags):
        """Remove the given flags from the ignore list."""
        # Check current status
        status = Status()
        if status.mode == 'robotic':
            return 'All flags are enabled in robotic mode'

        retstrs = []
        for flag in flags:
            # Check current status
            if flag not in self.flags:
                retstrs.append('"{}" is not a recognised flag'.format(flag))
                continue
            elif flag not in self.ignored_flags:
                retstrs.append('"{}" flag is not in the ignored list'.format(flag))
                continue
            elif flag == 'override':
                retstrs.append('"{}" flag can not be ignored (use "override on|off")'.format(flag))
                continue

            # Set flag
            self.ignored_flags.remove(flag)
            retstrs.append('"{}" flag removed from the ignored list'.format(flag))

        # Format return string
        return '\n'.join(retstrs)

    def set_override(self):
        """Activate the manual override flag."""
        # Check current status
        if self.manual_override:
            return 'Manual override is already enabled'

        # Set flag
        self.log.info('Enabling manual override')
        self.manual_override = True

        return 'Enabling conditions override flag'

    def clear_override(self):
        """Deactivate the manual override flag."""
        # Check current status
        if not self.manual_override:
            return 'Manual override is already disabled'

        # Set flag
        self.log.info('Disabling manual override')
        self.manual_override = False

        return 'Disabling conditions override flag'

    def dashboard_override(self, enable, dashboard_username):
        """Activate or deactivate the manual override flag from the web dashboard.

        This function is restricted to only the dashboard IP for specific outlets,
        and also has extra logging.
        See https://github.com/GOTO-OBS/g-tecs/issues/535 for details.
        """
        # Check IP
        client_ip = self._get_client_ip()
        if client_ip != params.DASHBOARD_IP:
            return False

        # Set flag
        if enable:
            self.manual_override = True
        else:
            self.manual_override = False

        logstr = 'Web dashboard user {} turning {} manual override'.format(
            dashboard_username, 'on' if enable else 'off')
        self.log.info(logstr)
        return logstr


if __name__ == '__main__':
    with make_pid_file('conditions'):
        ConditionsDaemon()._run()
