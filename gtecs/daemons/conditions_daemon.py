#!/usr/bin/env python
"""Daemon to monitor environmental conditions."""

import json
import threading
import time

from astropy.time import Time

from gtecs import conditions
from gtecs import misc
from gtecs import params
from gtecs.astronomy import get_sunalt
from gtecs.daemons import BaseDaemon

import numpy as np


class ConditionsDaemon(BaseDaemon):
    """Conditions monitor daemon class."""

    def __init__(self):
        super().__init__('conditions')

        # conditions variables
        self.check_period = params.WEATHER_INTERVAL

        self.flag_names = ['dark',
                           'rain',
                           'windspeed',
                           'humidity',
                           'temperature',
                           'ups',
                           'link',
                           'hatch',
                           'diskspace',
                           'low_battery',
                           'internal',
                           'ice',
                           ]

        self.flags = {flag: 2 for flag in self.flag_names}
        self.update_time = {flag: 0 for flag in self.flag_names}

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

        while(self.running):
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time
                self.force_check_flag = False

                # Nothing to connect to, just get the info
                self._get_info()

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the heardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from the weather masts
        try:
            temp_info['weather'] = {}
            weather = conditions.get_weather()
            for source in weather:
                source_info = weather[source].copy()

                # check if the weather timeout has been exceded
                dt = source_info['dt']
                if dt >= params.WEATHER_TIMEOUT or dt == -999:
                    source_info = {key: -999 for key in weather[source]}

                # check if the weather hasn't changed for a certain time
                source_info['changed_time'] = self.loop_time
                if self.info and self.info['weather'][source]:
                    changed_time = self.info['weather'][source]['changed_time']
                    unchanged = [source_info[key] == self.info['weather'][source][key]
                                 for key in source_info]
                    if all(unchanged) and (self.loop_time - changed_time) > params.WEATHER_STATIC:
                        source_info = {key: -999 for key in weather[source]}
                        source_info['changed_time'] = changed_time

                temp_info['weather'][source] = source_info
        except Exception:
            self.log.error('Failed to get weather info')
            self.log.debug('', exc_info=True)
            temp_info['weather'] = None

        # Get info from the UPSs
        try:
            ups_percent, ups_status = conditions.get_ups()
            temp_info['ups_percent'] = ups_percent
            temp_info['ups_status'] = ups_status
        except Exception:
            self.log.error('Failed to get UPS info')
            self.log.debug('', exc_info=True)
            temp_info['ups_percent'] = None
            temp_info['ups_status'] = None

        # Get info from the dome hatch
        try:
            hatch_closed = conditions.hatch_closed()
            temp_info['hatch_closed'] = hatch_closed
        except Exception:
            self.log.error('Failed to get hatch info')
            self.log.debug('', exc_info=True)
            temp_info['hatch_closed'] = None

        # Get info from the link ping check
        try:
            pings = [conditions.check_ping(url) for url in params.LINK_URLS]
            temp_info['pings'] = pings
        except Exception:
            self.log.error('Failed to get link info')
            self.log.debug('', exc_info=True)
            temp_info['pings'] = None

        # Get info from the disk usage check
        try:
            free_diskspace = conditions.get_diskspace_remaining(params.IMAGE_PATH) * 100.
            temp_info['free_diskspace'] = free_diskspace
        except Exception:
            self.log.error('Failed to get diskspace info')
            self.log.debug('', exc_info=True)
            temp_info['free_diskspace'] = None

        # Get current sun alt
        temp_info['sunalt'] = get_sunalt(Time(self.loop_time, format='unix'))

        # Set the conditions flags
        try:
            self._set_flags(temp_info)
        except Exception:
            self.log.error('Failed to set conditions flags')
            self.log.debug('', exc_info=True)
            self.flags = {flag: 2 for flag in self.flag_names}

        # Get internal info
        temp_info['flags'] = self.flags

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    def _set_flags(self, info):
        """Set the conditions flags based on the conditions info."""
        # Get the conditions values and filter by validity if needed

        # Weather
        weather = info['weather']
        rain = np.array([weather[source]['rain'] for source in weather
                         if 'rain' in weather[source]])
        windspeed = np.array([weather[source]['windspeed'] for source in weather
                              if 'windspeed' in weather[source]])
        temp = np.array([weather[source]['temperature'] for source in weather
                         if 'temperature' in weather[source]])
        humidity = np.array([weather[source]['humidity'] for source in weather
                             if 'humidity' in weather[source]])
        int_temp = np.array([weather[source]['int_temperature'] for source in weather
                             if 'int_temperature' in weather[source]])
        int_humidity = np.array([weather[source]['int_humidity'] for source in weather
                                 if 'int_humidity' in weather[source]])

        rain = rain[rain != -999]
        windspeed = windspeed[windspeed != -999]
        temp = temp[temp != -999]
        humidity = humidity[humidity != -999]
        int_temp = int_temp[int_temp != -999]
        int_humidity = int_humidity[int_humidity != -999]

        # UPSs
        ups_percent = np.array(info['ups_percent'])
        ups_status = np.array(info['ups_status'])

        ups_percent = ups_percent[ups_percent != -999]
        ups_status = ups_status[ups_status != -999]

        # Hatch
        hatch_closed = info['hatch_closed']

        # Link
        pings = np.array(info['pings'])

        # Diskspace
        disckspace_low = info['free_diskspace'] > params.MIN_DISKSPACE

        # Sunalt
        sun_up = info['sunalt'] < params.SUN_ELEVATION_LIMIT

        # ~~~~~~~~~~~~~~
        # Calcualte the flags and if they are valid.
        # At least two of the external sources and one of the internal sources need to be valid,
        # except for rain and windspeed because we only have two sources (no SuperWASP),
        # so only need at least one.
        good = {flag: False for flag in self.flag_names}
        valid = {flag: False for flag in self.flag_names}
        good_delay = {flag: 0 for flag in self.flag_names}
        bad_delay = {flag: 0 for flag in self.flag_names}

        # rain flag
        good['rain'] = np.all(rain == 0)
        valid['rain'] = len(rain) >= 1
        good_delay['rain'] = params.RAIN_GOODDELAY
        bad_delay['rain'] = params.RAIN_BADDELAY

        # windspeed flag
        good['windspeed'] = np.all(windspeed < params.MAX_WINDSPEED)
        valid['windspeed'] = len(windspeed) >= 1
        good_delay['windspeed'] = params.WINDSPEED_GOODDELAY
        bad_delay['windspeed'] = params.WINDSPEED_BADDELAY

        # temperature flag
        good['temperature'] = (np.all(temp > params.MIN_TEMPERATURE) and
                               np.all(temp < params.MAX_TEMPERATURE) and
                               np.all(int_temp > params.MIN_INTERNAL_TEMPERATURE) and
                               np.all(int_temp < params.MAX_INTERNAL_TEMPERATURE))
        valid['temperature'] = len(temp) >= 1 and len(int_temp) >= 1
        good_delay['temperature'] = params.TEMPERATURE_GOODDELAY
        bad_delay['temperature'] = params.TEMPERATURE_BADDELAY

        # ice flag
        good['ice'] = np.all(temp > 0)
        valid['ice'] = len(temp) >= 1
        good_delay['ice'] = params.ICE_GOODDELAY
        bad_delay['ice'] = params.HUMIDITY_BADDELAY

        # humidity flag
        good['humidity'] = (np.all(humidity < params.MAX_HUMIDITY) and
                            np.all(int_humidity < params.MAX_INTERNAL_HUMIDITY))
        valid['humidity'] = len(humidity) >= 1 and len(int_humidity) >= 1
        good_delay['humidity'] = params.HUMIDITY_GOODDELAY
        bad_delay['humidity'] = params.HUMIDITY_BADDELAY

        # internal flag
        good['internal'] = (np.all(int_humidity < params.CRITICAL_INTERNAL_HUMIDITY) and
                            np.all(int_temp > params.CRITICAL_INTERNAL_TEMPERATURE))
        valid['internal'] = len(int_humidity) >= 1 and len(int_temp) >= 1
        good_delay['internal'] = params.INTERNAL_GOODDELAY
        bad_delay['internal'] = params.INTERNAL_BADDELAY

        # ups flag
        good['ups'] = (np.all(ups_percent > params.MIN_UPSBATTERY) and
                       np.all(ups_status == 1))
        valid['ups'] = len(ups_percent) >= 1 and len(ups_status) >= 1
        good_delay['ups'] = params.UPS_GOODDELAY
        bad_delay['ups'] = params.UPS_BADDELAY

        # low_battery flag
        good['low_battery'] = np.all(ups_percent > params.CRITICAL_UPSBATTERY)
        valid['low_battery'] = len(ups_percent) >= 1
        good_delay['low_battery'] = 0
        bad_delay['low_battery'] = 0

        # hatch flag
        good['hatch'] = hatch_closed
        valid['hatch'] = True
        good_delay['hatch'] = params.HATCH_GOODDELAY
        bad_delay['hatch'] = params.HATCH_BADDELAY

        # link flag
        good['link'] = np.all(pings == 1)
        valid['link'] = len(pings) >= 1

        # diskspace flag
        good['diskspace'] = disckspace_low
        valid['diskspace'] = True
        good_delay['diskspace'] = 0
        bad_delay['diskspace'] = 0

        # dark flag
        good['dark'] = sun_up
        valid['dark'] = True
        good_delay['dark'] = 0
        bad_delay['dark'] = 0

        # ~~~~~~~~~~~~~~
        # Set each flag
        update_time = info['time']
        for flag in self.flag_names:
            # check if invalid
            if not valid[flag] and self.flags[flag] != 2:
                self.log.info('Setting {} to ERROR (2)'.format(flag))
                self.flags[flag] = 2
                self.update_time[flag] = update_time
                continue

            # check if good
            if good[flag] and self.flags[flag] != 0:
                dt = update_time - self.update_time[flag]
                if dt > good_delay[flag]:
                    self.log.info('Setting {} to good (0)'.format(flag))
                    self.flags[flag] = 0
                    self.update_time[flag] = update_time
                else:
                    frac = '{:.0f}/{:.0f}'.format(dt, good_delay[flag])
                    self.log.info('{} is good but delay is {}'.format(flag, frac))
                continue

            # check if bad
            if not good[flag] and self.flags[flag] != 1:
                dt = update_time - self.update_time[flag]
                if dt > bad_delay[flag]:
                    self.log.info('Setting {} to bad (1)'.format(flag))
                    self.flags[flag] = 1
                    self.update_time[flag] = update_time
                else:
                    frac = '{:.0f}/{:.0f}'.format(dt, bad_delay[flag])
                    self.log.info('{} is bad but delay is {}'.format(flag, frac))
                continue

            # otherwise everything is normal
            self.update_time[flag] = update_time

        # ~~~~~~~~~~~~~~
        # Write data to the conditions flags file
        data = self.flags.copy()
        data['update_time'] = Time(update_time, format='unix').iso
        flags_file = params.CONFIG_PATH + 'conditions_flags'
        with open(flags_file, 'w') as f:
            json.dump(data, f)

        # log current flags
        logline = ''
        for key in sorted(self.flags):
            logline += '{}: {} '.format(key, self.flags[key])
        self.log.info(logline)


if __name__ == "__main__":
    daemon_id = 'conditions'
    with misc.make_pid_file(daemon_id):
        ConditionsDaemon()._run()
