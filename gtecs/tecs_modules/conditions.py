#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                            conditions.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                  G-TeCS conditions monitor functions                 #
#                     Martin Dyer, Sheffield, 2017                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

import os
import subprocess
import json
import time

from astropy.time import Time

# TeCS modules
from . import params
from . import misc
from .astronomy import sun_alt


def curl_data_from_url(url, outfile, encoding=None):
    '''Fetch data from a URL, store it in a file and return the contents'''

    curl_command = 'curl -s -m 10 -o {} {}'.format(outfile, url)
    try:
        subprocess.Popen(curl_command, shell=True, close_fds=True)
    except:
        print('Error fetching URL "{}"'.format(url))
    time.sleep(0.2)
    if encoding:
        with open(outfile, 'r', encoding=encoding) as f:
            data = f.read()
    else:
        with open(outfile, 'r') as f:
            data = f.read()

    return data


def get_roomalert():
    '''Get internal dome temperature and humidity from GOTO RoomAlert system'''

    url = '10.2.6.5/getData.json'
    outfile = params.CONFIG_PATH + 'roomalert.json'
    indata = curl_data_from_url(url, outfile)
    data = json.loads(indata)

    internal_dict = {'int_update_time': -999,
                     'int_dt': -999,
                     'int_temperature': -999,
                     'int_humidity': -999,
                     }

    try:
        update_date = data['date'].split()[0]
        update_date = '20'+'-'.join(update_date.split('/')[::-1])
        update_time = data['date'].split()[1]
        update = '{} {}'.format(update_date, update_time)
        internal_dict['int_update_time'] = Time(update, precision=0).iso
        dt = Time.now() - Time(update)
        internal_dict['int_dt'] = int(dt.to('second').value)

        dome_data = data['sensor'][0]
        dome_temperature = float(dome_data['tc'])
        dome_humidity = float(dome_data['h'])

        pier_data = data['sensor'][1]
        pier_temperature = float(pier_data['tc'])
        pier_humidity = float(pier_data['h'])

        # to be safe, take the higher of the two sensor values
        int_temperature = max([dome_temperature, pier_temperature])
        int_humidity = max([dome_humidity, pier_humidity])

        internal_dict['int_temperature'] = int_temperature
        internal_dict['int_humidity'] = int_humidity

    except:
        print('Error parsing RoomAlert page')

    return internal_dict


def get_local_weather(source):
    '''Get the current weather from the Warwick stations'''

    source = source.lower()
    sources = ['goto', 'onemetre', 'superwasp']
    if source not in ['goto', 'onemetre', 'superwasp']:
        raise ValueError('Invalid weather source "{}", must be in {}'.format(source, sources))

    url = 'http://10.2.6.100/data/raw/'

    if source == 'goto':
        json_file = 'goto-vaisala.json'
        vaisala = True
    elif source == 'onemetre':
        json_file = 'onemetre-vaisala.json'
        vaisala = True
    elif source == 'superwasp':
        json_file = 'superwasp-log.json'
        vaisala = False

    data = json.loads(curl_data_from_url(url + json_file,
                                         params.CONFIG_PATH + json_file))

    weather_dict = {'update_time': -999,
                    'dt': -999,
                    'rain': -999,
                    'temperature': -999,
                    'pressure': -999,
                    'winddir': -999,
                    'windspeed': -999,
                    'humidity': -999,
                    'skytemp': -999,
                    }

    try:
        if vaisala:
            weather_dict['temperature'] = float(data['temperature'])
        else:
            weather_dict['temperature'] = float(data['ext_temperature'])
    except:
        print('Error fetching temperature')

    try:
        weather_dict['pressure'] = float(data['pressure'])
    except:
        print('Error fetching pressure')

    try:
        weather_dict['windspeed'] = float(data['wind_speed']) / 3.6
    except:
        print('Error fetching wind speed')

    try:
        weather_dict['winddir'] = float(data['wind_direction'])
    except:
        print('Error fetching wind direction')

    try:
        if vaisala:
            weather_dict['humidity'] = float(data['relative_humidity'])
        else:
            weather_dict['humidity'] = float(data['ext_humidity'])
    except:
        print('Error fetching humidity')

    try:
        if vaisala:
            if float(data['rain_intensity']) > 0:
                weather_dict['rain'] = True
            else:
                weather_dict['rain'] = False
        else:
            del weather_dict['rain']
    except:
        print('Error fetching rain')

    try:
        if vaisala:
            del weather_dict['skytemp']
        else:
            weather_dict['skytemp'] = float(data['sky_temp'])
    except:
        print('Error fetching sky temp')

    try:
        weather_dict['update_time'] = Time(data['date'], precision=0).iso
        dt = Time.now() - Time(data['date'])
        weather_dict['dt'] = int(dt.to('second').value)
    except:
        print('Error parsing update time')

    return weather_dict


def get_ing_weather():
    '''Get the current weather from the ING weather page (JKT mast)'''

    url = 'http://catserver.ing.iac.es/weather/'
    outfile = params.CONFIG_PATH + 'weather.html'
    indata = curl_data_from_url(url, outfile, encoding='ISO-8859-1')

    weather_dict = {'update_time': -999,
                    'dt': -999,
                    'rain': -999,
                    'temperature': -999,
                    'pressure': -999,
                    'winddir': -999,
                    'windspeed': -999,
                    'windgust': -999,
                    'humidity': -999,
                    }

    try:
       for line in indata.split('\n'):
            columns = misc.remove_html_tags(line).replace(':',' ').split()
            if not columns:
                continue

            if columns[0] == 'Temperature':
                try:
                    weather_dict['temperature'] = float(columns[1])
                except:
                    print('Error parsing temperature:', columns[1])

            elif columns[0] == 'Pressure':
                try:
                    weather_dict['pressure'] = float(columns[1])
                except:
                    print('Error parsing pressure:', columns[1])

            elif columns[0] == 'Wind' and columns[1] == 'Speed':
                try:
                    weather_dict['windspeed'] = float(columns[2]) / 3.6 # km/h to m/s
                except:
                    print('Error parsing wind speed:', columns[2])

            elif columns[0] == 'Wind' and columns[1] == 'Direction':
                try:
                    weather_dict['winddir'] = str(columns[2])
                except:
                    print('Error parsing wind direction:', columns[2])

            elif columns[0] == 'Wind' and columns[1] == 'Gust':
                try:
                    weather_dict['windgust'] = float(columns[2]) / 3.6
                except:
                    print('Error parsing wind gust:', columns[2])

            elif columns[0] == 'Humidity':
                try:
                    weather_dict['humidity'] = float(columns[1])
                except:
                    print('Error parsing humidity:', columns[1])

            elif columns[0] == 'Rain':
                try:
                    if columns[1] == 'DRY':
                        weather_dict['rain'] = False
                    elif columns[1] == 'WET':
                        weather_dict['rain'] = True
                except:
                    print('Error parsing rain:', columns[1])

            elif len(columns) == 4 and columns[3] == 'UT':
                try:
                    update_date = columns[0].replace('/', '-')
                    update_time = '{}:{}'.format(columns[1],columns[2])
                    update = '{} {}'.format(update_date, update_time)
                    weather_dict['update_time'] = Time(update, precision=0).iso
                    dt = Time.now() - Time(update)
                    weather_dict['dt'] = int(dt.to('second').value)
                except:
                    print('Error parsing update time:', *columns)

    except:
        print('Error parsing weather page')

    return weather_dict


def get_ing_internal_weather(weather_source):
    '''Get the current weather from the internal ING xml weather file'''

    if weather_source == 'wht':
        url = "http://whtmetsystem.ing.iac.es/WeatherXMLData/LocalData.xml"
    elif weather_source == 'int':
        url = "http://intmetsystem.ing.iac.es/WeatherXMLData/LocalData.xml"
    elif weather_source == 'jkt':
        url = "http://intmetsystem.ing.iac.es/WeatherXMLData/MainData.xml"

    outfile = params.CONFIG_PATH + 'weather.xml'
    indata = curl_data_from_url(url, outfile)

    weather_dict = {'update_time': -999,
                    'dt': -999,
                    'rain': -999,
                    'temperature': -999,
                    'pressure': -999,
                    'winddir': -999,
                    'windspeed': -999,
                    'windgust': -999,
                    'humidity': -999,
                    }

    try:
        for line in indata.split('\n'):
            columns = line.split()
            try:
                label = columns[1].split("\"")[1].split(".")[2]
                value = columns[2].split("\"")[1]
            except:
                continue

            if label == 'date':
                try:
                    update = float(value)
                    weather_dict['update_time'] = Time(update, precision=0).iso
                    dt = Time.now() - Time(update)
                    weather_dict['dt'] = int(dt.to('second').value)
                except:
                    print('Error parsing update time:', value)

            elif label == 'LocalMastAirTemp' or label == 'MainMastAirTemp':
                try:
                    weather_dict['temperature'] = float(value)
                except:
                    print('Error parsing temperature:', value)

            elif label == 'LocalMastPressure' or label == 'MainMastPressure':
                try:
                    weather_dict['pressure'] = float(value)
                except:
                    print('Error parsing pressure:', value)

            elif label == 'LocalMastWindSpeed' or label == 'MainMastWindSpeed':
                try:
                    weather_dict['windspeed'] = float(value) / 3.6
                except:
                    print('Error parsing wind speed:', value)

            elif label == 'LocalMastWindDirection' or label == 'MainMastWindDirection':
                try:
                    weather_dict['winddir'] = str(value)
                except:
                    print('Error parsing wind direction:', value)

            elif label == 'LocalMastGust' or label == 'MainMastGust':
                try:
                    weather_dict['windgust'] = float(value) / 3.6
                except:
                    print('Error parsing wind gust:', value)

            elif label == 'LocalMastHumidity' or label == 'MainMastHumidity':
                try:
                    weather_dict['humidity'] = float(value)
                except:
                    print('Error parsing humidity:', value)

            elif label == 'LocalMastWetness' or label == 'MainMastWetness':
                try:
                    if float(value) <= 0:
                        weather_dict['rain'] = False
                    elif float(value) >= 1:
                        weather_dict['rain'] = True
                except:
                    print('Error parsing rain:', value)

    except:
        print('Error parsing weather page')

    return weather_dict


def get_weather():
    '''Get the current weather conditions'''

    primary_source = params.WEATHER_SOURCE
    backup_source = params.BACKUP_WEATHER_SOURCE

    # Get the weather from the external source
    if primary_source == 'html':
        weather = get_ing_weather()
    else:
        weather = get_ing_internal_weather(primary_source)
    source_used = primary_source

    # Check for errors, if there were then use the backup source
    if weather['dt'] > params.WEATHER_TIMEOUT or -999 in weather.values():
        if backup_source != 'html':
            weather = get_ing_internal_weather(backup_source)
        else:
            weather = get_ing_weather()
        source_used = backup_source

    # Get the internal conditions from the RoomAlert
    internal_dict = get_roomalert()
    weather.update(internal_dict)

    # Add the altitude of the Sun at the current time
    weather['sunalt'] = sun_alt(Time.now())

    return weather, source_used


def check_external_connection():
    '''Check the connection between the GOTO dome and gotohead in Warwick'''
    try:
        url = 'ngtshead.warwick.ac.uk'
        ping_command = 'ping -c 3 {} | grep "ttl="'.format(url)
        link = os.popen(ping_command).read()
        if "ttl=" in link:
            return True
        else:
            return False
    except:
        return False
