#!/usr/bin/env python
"""
Daemon to monitor environmental conditions
"""

import os
import sys
import time
import datetime
from math import *
import Pyro4
import threading
import subprocess
import json

import numpy as np

from astropy.time import Time

from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs import conditions
from gtecs.astronomy import sun_alt
from gtecs.observing import check_dome_closed
from gtecs.daemons import HardwareDaemon


DAEMON_ID = 'conditions'
DAEMON_HOST = params.DAEMONS[DAEMON_ID]['HOST']
DAEMON_PORT = params.DAEMONS[DAEMON_ID]['PORT']


class ConditionsDaemon(HardwareDaemon):
    """Conditions monitor daemon class"""

    def __init__(self):
        ### initiate daemon
        self.daemon_id = DAEMON_ID
        HardwareDaemon.__init__(self, self.daemon_id)

        ### command flags
        self.get_info_flag = 0

        ### conditions variables
        self.save_logs = True

        self.conditions_check_time = 0

        self.weather = None
        self.weather_changed_time = 0

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
                           'ice',
                           ]

        self.good = dict.fromkeys(self.flag_names, False)
        self.valid = dict.fromkeys(self.flag_names, False)

        self.change_time = dict.fromkeys(self.flag_names, 0)
        self.good_delay = {'dark': 0,
                           'rain': params.RAIN_GOODDELAY,
                           'windspeed': params.WINDSPEED_GOODDELAY,
                           'humidity': params.HUMIDITY_GOODDELAY,
                           'temperature': params.TEMPERATURE_GOODDELAY,
                           'ups': params.UPS_GOODDELAY,
                           'link': params.LINK_GOODDELAY,
                           'hatch': params.HATCH_GOODDELAY,
                           'diskspace': 0,
                           'low_battery': 0,
                           'ice': params.ICE_GOODDELAY,
                           }
        self.bad_delay = {'dark': 0,
                          'rain': params.RAIN_BADDELAY,
                          'windspeed': params.WINDSPEED_BADDELAY,
                          'humidity': params.HUMIDITY_BADDELAY,
                          'temperature': params.TEMPERATURE_BADDELAY,
                          'ups': params.UPS_BADDELAY,
                          'link': params.LINK_BADDELAY,
                          'hatch': params.HATCH_BADDELAY,
                          'diskspace': 0,
                          'low_battery': 0,
                          'ice': params.ICE_BADDELAY,
                          }


        self.flags = dict.fromkeys(self.flag_names, 2)

        self.data = None

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()


    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

        while(self.running):
            self.time_check = time.time()

            ### check the conditions
            if (self.time_check - self.conditions_check_time) > params.WEATHER_INTERVAL:

                self.conditions_check_time = time.time()

                # ~~~~~~~~~~~~~~
                # gather the external data

                # get the weather dict
                weather = conditions.get_weather()

                # check if the weather values are recently updated
                # if they're outdated, mark them all as errors
                for source in weather:
                    dt = weather[source]['dt']
                    if dt >= params.WEATHER_TIMEOUT or dt == -999:
                        weather[source] = dict.fromkeys(weather[source], -999)

                # get the current sun alt
                sunalt_now = sun_alt(Time.now())

                # get the current UPS battery percentage remaining
                ups_percent, ups_status = conditions.get_ups()

                # check the connection with Warwick
                ping_successful = []
                for url in params.LINK_URLS:
                    ping_successful.append(conditions.check_ping(url))

                # get the current hatch status
                hatch_closed = conditions.hatch_closed()

                # get the current disk usage on the image path
                free_diskspace = conditions.get_diskspace_remaining(params.IMAGE_PATH)*100.


                # ~~~~~~~~~~~~~~
                # check if current values are good/bad and valid
                # at least two of the external sources and one of the
                #    internal sources need to be valid

                # RAIN
                rain_array = np.array([weather[source]['rain']
                                      for source in weather
                                      if 'rain' in weather[source]])
                valid_rain = rain_array[rain_array != -999]

                self.good['rain'] = np.all(valid_rain == False)
                self.valid['rain'] = len(valid_rain) >= 1


                # WINDSPEED
                windspeed_array = np.array([weather[source]['windspeed']
                                           for source in weather
                                           if 'windspeed' in weather[source]])
                valid_windspeed = windspeed_array[windspeed_array != -999]

                self.good['windspeed'] = np.all(valid_windspeed <  params.MAX_WINDSPEED)
                self.valid['windspeed'] = len(valid_windspeed) >= 2


                # HUMIDITY
                humidity_array = np.array([weather[source]['humidity']
                                          for source in weather
                                          if 'humidity' in weather[source]])
                valid_humidity = humidity_array[humidity_array != -999]

                int_humidity_array = np.array([weather[source]['int_humidity']
                                              for source in weather
                                              if 'int_humidity' in weather[source]])
                valid_int_humidity = int_humidity_array[int_humidity_array != -999]

                self.good['humidity'] = (np.all(valid_humidity < params.MAX_HUMIDITY) and
                                         np.all(valid_int_humidity < params.MAX_INTERNAL_HUMIDITY))
                self.valid['humidity'] = (len(valid_humidity) >= 2 and
                                            len(valid_int_humidity) >= 1)


                # TEMPERATURE & ICE
                temp_array = np.array([weather[source]['temperature']
                                      for source in weather
                                      if 'temperature' in weather[source]])
                valid_temp = temp_array[temp_array != -999]

                self.good['temperature'] = (np.all(valid_temp > params.MIN_TEMPERATURE) and
                                            np.all(valid_temp < params.MAX_TEMPERATURE))
                self.valid['temperature'] = len(valid_temp) >= 2

                self.good['ice'] = np.all(valid_temp > 0)
                self.valid['ice'] = len(valid_temp) >= 2


                # DARK
                self.good['dark'] = sunalt_now < params.SUN_ELEVATION_LIMIT
                self.valid['dark'] = True


                # UPS and LOW_BATTERY
                ups_percent_array = np.array(ups_percent)
                ups_status_array = np.array(ups_status)
                valid_ups_percent = ups_percent_array[ups_percent_array != -999]
                valid_ups_status = ups_status_array[ups_status_array != -999]

                self.good['ups'] = (np.all(valid_ups_percent > params.MIN_UPSBATTERY) and
                                    np.all(valid_ups_status == True))
                self.valid['ups'] = (len(valid_ups_percent) >= 1 and
                                     len(valid_ups_status) >= 1)

                self.good['low_battery'] = np.all(valid_ups_percent > params.CRITICAL_UPSBATTERY)
                self.valid['low_battery'] = len(valid_ups_percent) >= 1


                # LINK
                link_array = np.array(ping_successful)
                self.good['link'] = np.all(link_array == True)
                self.valid['link'] = len(link_array) >= 1


                # HATCH
                self.good['hatch'] = hatch_closed
                self.valid['hatch'] = True


                # DISKSPACE
                self.good['diskspace'] = free_diskspace > params.MIN_DISKSPACE
                self.valid['diskspace'] = True


                # CHECK - if the weather hasn't changed for a certain time
                if weather != self.weather:
                    self.weather_changed_time = time.time()
                    self.weather = weather.copy()
                else:
                    time_since_update = time.time() - weather_changed_time
                    if time_since_update > params.WEATHER_STATIC:
                        self.good['rain'] = False
                        self.good['windspeed'] = False
                        self.good['humidity'] = False
                        self.good['temperature'] = False
                        self.good['ice'] = False


                # ~~~~~~~~~~~~~~
                # set the flags
                update_time = time.time()
                for name in self.flag_names:
                    if not self.valid[name]:
                        self.logfile.info('Setting {} to ERROR (2)'.format(name))
                        self.flags[name] = 2
                    elif self.good[name] and self.flags[name] != 0:
                        dt = update_time - self.change_time[name]
                        delay = self.good_delay[name]
                        if dt > delay:
                            self.change_time[name] = update_time
                            self.logfile.info('Setting {} to good (0)'.format(name))
                            self.flags[name] = 0
                        else:
                            self.logfile.info('{} is good but delay is {:.0f}/{:.0f}'.format(name, dt, delay))
                    elif not self.good[name] and self.flags[name] != 1:
                        dt = update_time - self.change_time[name]
                        delay = self.bad_delay[name]
                        if dt > delay:
                            self.change_time[name] = update_time
                            self.logfile.info('Setting {} to bad (1)'.format(name))
                            self.flags[name] = 1
                        else:
                            self.logfile.info('{} is bad but delay is {:.0f}/{:.0f}'.format(name, dt, delay))
                    else:
                        self.change_time[name] = update_time


                # ~~~~~~~~~~~~~~
                # add update time to output data
                self.data = {'update_time': str(Time.now().iso)}
                self.data.update(self.flags)

                # write data to the conditions flags file
                flags_file = params.CONFIG_PATH + 'conditions_flags'
                with open(flags_file, 'w') as f:
                    json.dump(self.data, f)

                # log current flags
                logline = ''
                for key in sorted(self.flags.keys()):
                    logline += '{}: {} '.format(key, self.flags[key])
                self.logfile.info(logline)

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return


    # Conditions functions
    def get_info(self):
        """Return current conditions flags and weather info"""
        return {'flags': self.data, 'weather': self.weather}


    def get_info_simple(self):
        """Return plain status dict, or None"""
        try:
            info = self.get_info()
        except:
            return None
        return info


if __name__ == "__main__":
    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(DAEMON_ID):
        sys.exit()

    # Create the daemon object
    daemon = ConditionsDaemon()

    # Start the daemon
    with Pyro4.Daemon(host=DAEMON_HOST, port=DAEMON_PORT) as pyro_daemon:
        uri = pyro_daemon.register(daemon, objectId=DAEMON_ID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=daemon.status_function)

    # Loop has closed
    daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)
