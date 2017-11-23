#!/usr/bin/env python

########################################################################
#                         conditions_daemon.py                         #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS daemon to monitor environmental conditions          #
#                     Martin Dyer, Sheffield, 2017                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import os, sys
from math import *
import time, datetime
import Pyro4
import threading
import subprocess
import json
import numpy as np
from astropy.time import Time
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.tecs_modules import conditions
from gtecs.tecs_modules.astronomy import sun_alt
from gtecs.tecs_modules.observing import check_dome_closed
from gtecs.tecs_modules.daemons import HardwareDaemon

########################################################################
# Conditions daemon class

class ConditionsDaemon(HardwareDaemon):
    """Conditions monitor daemon class"""

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'conditions')

        ### command flags
        self.get_info_flag = 0

        ### conditions variables
        self.save_logs = True

        self.conditions_check_time = 0

        self.weather = None
        self.weather_changed_time = 0

        self.successful_ping_time = 0

        # set default values of flags
        # 0=good, 1=bad, 2=error
        self.flags = {'dark': 2,
                      'rain': 2,
                      'windspeed': 2,
                      'humidity': 2,
                      'temperature': 2,
                      'link': 2,
                      }
        self.data = None

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

        while(self.running):
            self.time_check = time.time()

            ### check the conditions
            if (self.time_check - self.conditions_check_time) > params.WEATHER_INTERVAL:

                self.conditions_check_time = time.time()

                # get the weather dict
                weather = conditions.get_weather()

                # check if the weather values are recently updated
                # if they're outdated, mark them all as errors
                for source in weather:
                    dt = weather[source]['dt']
                    if dt >= params.WEATHER_TIMEOUT or dt == -999:
                        weather[source] = dict.fromkeys(weather[source], -999)

                # ~~~~~~~~~~~~~~
                # set weather flags
                # at least two of the external sources and one of the
                #    internal sources need to be valid
                # note the extra check if the flag is already set to 1,
                #    in which case a different (safer) limit is used to prevent
                #    repeated opening/closing of the dome if conditions are
                #    hovering near a limit

                # RAIN
                rain_array = np.array([weather[source]['rain']
                                      for source in weather
                                      if 'rain' in weather[source]])

                valid_rain_mask = rain_array != -999
                valid_rain = rain_array[valid_rain_mask]

                rain_good = np.all(valid_rain == False)

                if len(valid_rain) < 2:
                    self.flags['rain'] = 2
                elif rain_good and self.flags['rain'] != 0:
                    print('Setting rain to good')
                    self.flags['rain'] = 0
                elif not rain_good and self.flags['rain'] != 1:
                    print('Setting rain to bad')
                    self.flags['rain'] = 1


                # WINDSPEED
                windspeed_array = np.array([weather[source]['windspeed']
                                           for source in weather
                                           if 'windspeed' in weather[source]])

                valid_windspeed_mask = windspeed_array != -999
                valid_windspeed = windspeed_array[valid_windspeed_mask]

                windspeed_good = np.all(valid_windspeed <  params.MAX_WINDSPEED)

                if len(valid_windspeed) < 2:
                    self.flags['windspeed'] = 2
                elif windspeed_good and self.flags['windspeed'] != 0:
                    print('Setting windspeed to good')
                    self.flags['windspeed'] = 0
                elif not windspeed_good and self.flags['windspeed'] != 1:
                    print('Setting windspeed to bad')
                    self.flags['windspeed'] = 1


                # HUMIDITY
                humidity_array = np.array([weather[source]['humidity']
                                          for source in weather
                                          if 'humidity' in weather[source]])

                valid_humidity_mask = humidity_array != -999
                valid_humidity = humidity_array[valid_humidity_mask]

                int_humidity_array = np.array([weather[source]['int_humidity']
                                              for source in weather
                                              if 'int_humidity' in weather[source]])

                valid_int_humidity_mask = int_humidity_array != -999
                valid_int_humidity = int_humidity_array[valid_int_humidity_mask]

                humidity_good = (np.all(valid_humidity < params.MAX_HUMIDITY) and
                                 np.all(valid_int_humidity < params.MAX_INTERNAL_HUMIDITY))

                if len(valid_humidity) < 2 or len(valid_int_humidity) < 1:
                    self.flags['humidity'] = 2
                elif humidity_good and self.flags['humidity'] != 0:
                    print('Setting humidity to good')
                    self.flags['humidity'] = 0
                elif not humidity_good and self.flags['humidity'] != 1:
                    print('Setting humidity to bad')
                    self.flags['humidity'] = 1


                # TEMPERATURE
                temp_array = np.array([weather[source]['temperature']
                                      for source in weather
                                      if 'temperature' in weather[source]])

                valid_temp_mask = temp_array != -999
                valid_temp = temp_array[valid_temp_mask]

                temp_good = (np.all(valid_temp > params.MIN_TEMPERATURE) and
                             np.all(valid_temp < params.MAX_TEMPERATURE))

                if len(valid_temp) < 2:
                    self.flags['temperature'] = 2
                elif temp_good and self.flags['temperature'] != 0:
                    print('Setting temperature to good')
                    self.flags['temperature'] = 0
                elif not temp_good and self.flags['temperature'] != 1:
                    print('Setting temperature to bad')
                    self.flags['temperature'] = 1

                # CHECK - if the data hasn't changed for a certain time
                if weather != self.weather:
                    self.weather_changed_time = time.time()
                    self.weather = weather.copy()
                else:
                    time_since_update = time.time() - self.weather_changed_time
                    if time_since_update > params.WEATHER_STATIC:
                        self.flags['rain'] = 2
                        self.flags['windspeed'] = 2
                        self.flags['humidity'] = 2
                        self.flags['temperature'] = 2

                # ~~~~~~~~~~~~~~
                # get the current sun alt to set the dark flag
                sunalt_now = sun_alt(Time.now())
                dark_good = sunalt_now < params.SUN_ELEVATION_LIMIT

                if dark_good and self.flags['dark'] != 0:
                    print('Setting dark to good')
                    self.flags['dark'] = 0
                elif not dark_good and self.flags['dark'] != 1:
                    print('Setting dark to bad')
                    self.flags['dark'] = 1

                # ~~~~~~~~~~~~~~
                # check the connectivity with Warwick to set the link flag
                ping_home = conditions.check_external_connection()
                if ping_home:
                    self.successful_ping_time = time.time()
                dt = time.time() - self.successful_ping_time

                link_interval_closed = params.LINK_BADTIME
                link_interval_open = params.LINK_BADTIME*10

                self.flags['link'] = 0
                # disable the link flag for now
                #try:
                #    dome_closed = check_dome_closed()
                #    if dome_closed and dt < link_interval_closed:
                #        self.flags['link'] = 0
                #    elif not dome_closed and dt < link_interval_open:
                #        self.flags['link'] = 0
                #    else:
                #        self.flags['link'] = 1
                #except:
                #    self.flags['link'] = 2


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

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Conditions functions
    def get_info(self):
        """Return current conditions flags and weather info"""
        return {'flags': self.data, 'weather': self.weather}


########################################################################

def start():
    '''
    Create Pyro server, register the daemon and enter request loop
    '''
    host = params.DAEMONS['conditions']['HOST']
    port = params.DAEMONS['conditions']['PORT']

    # Check the daemon isn't already running
    if not misc.there_can_only_be_one('conditions'):
        sys.exit()

    # Start the daemon
    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        conditions_daemon = ConditionsDaemon()
        uri = pyro_daemon.register(conditions_daemon, objectId='conditions')
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        conditions_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=conditions_daemon.status_function)

    # Loop has closed
    conditions_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
