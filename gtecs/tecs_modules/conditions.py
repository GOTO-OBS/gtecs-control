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
from ..controls.power_control import APCUPS, FakeUPS


def curl_data_from_url(url, outfile, encoding=None):
    '''Fetch data from a URL, store it in a file and return the contents'''

    curl_command = 'curl -s -m 10 -o {} {}'.format(outfile, url)
    try:
        p = subprocess.Popen(curl_command, shell=True, close_fds=True)
        p.wait()
    except:
        print('Error fetching URL "{}"'.format(url))

    if encoding:
        with open(outfile, 'r', encoding=encoding) as f:
            data = f.read()
    else:
        with open(outfile, 'r') as f:
            data = f.read()

    return data


def get_ups():
    '''Get battery status from GOTO UPSs'''
    percent = []
    for unit_name in params.POWER_UNITS:
        unit_class = params.POWER_UNITS[unit_name]['CLASS']
        if 'UPS' not in unit_class:
            continue
        else:
            try:
                unit_ip = params.POWER_UNITS[unit_name]['IP']
                if unit_class == 'APCUPS':
                    ups = APCUPS(unit_ip)
                elif unit_class == 'FakeUPS':
                    ups = FakeUPS(unit_ip)
                remaining = ups.percent_remaining()

                # Check status too
                status = ups.status()
                if status != 'Normal':
                    remaining = -999

                percent.append(remaining)
            except:
                percent.append[-999]
    return percent


def hatch_closed():
    '''Get hatch status from GOTO Dome Arduino'''

    url = params.ARDUINO_LOCATION
    outfile = params.CONFIG_PATH + 'arduino.json'

    try:
        indata = curl_data_from_url(url, outfile)
        data = json.loads(indata)
    except:
        print('Error fetching hatch data')
        return False

    try:
        hatch_closed = data['switch_d']
        if hatch_closed:
            return True
        else:
            return False
    except:
        print('Error parsing hatch status')
        return False


def get_roomalert(source):
    '''Get internal dome temperature and humidity from GOTO RoomAlert system'''

    sources = ['dome', 'pier']
    if source not in sources:
        raise ValueError('Invalid weather source "{}", must be in {}'.format(source, sources))

    url = '10.2.6.5/getData.json'
    outfile = params.CONFIG_PATH + 'roomalert.json'

    weather_dict = {'update_time': -999,
                    'dt': -999,
                    'int_temperature': -999,
                    'int_humidity': -999,
                    }

    try:
        indata = curl_data_from_url(url, outfile)
        if len(indata) < 3:
            time.sleep(0.2)
            indata = curl_data_from_url(url, outfile)
        data = json.loads(indata)
    except:
        print('Error fetching RoomAlert data')
        return weather_dict

    try:
        update_date = data['date'].split()[0].split('/')
        update_date = '20{}-{}-{}'.format(update_date[2],
                                          update_date[0],
                                          update_date[1])
        update_time = data['date'].split()[1]
        update = '{} {}'.format(update_date, update_time)
        weather_dict['update_time'] = Time(update, precision=0).iso
        dt = Time.now() - Time(update)
        weather_dict['dt'] = int(dt.to('second').value)

        if source == 'dome':
            sensor_data = data['sensor'][0]
        elif source == 'pier':
            sensor_data = data['sensor'][1]

        int_temperature = float(sensor_data['tc'])
        int_humidity = float(sensor_data['h'])

        weather_dict['int_temperature'] = int_temperature
        weather_dict['int_humidity'] = int_humidity

    except:
        print('Error parsing RoomAlert page')

    return weather_dict


def get_local_weather(source):
    '''Get the current weather from the Warwick stations'''

    source = source.lower()
    sources = ['goto', 'onemetre', 'superwasp']
    if source not in sources:
        raise ValueError('Invalid weather source "{}", must be in {}'.format(source, sources))

    base_url = 'http://10.2.6.100/data/raw/'

    if source == 'goto':
        json_file = 'goto-vaisala'
        vaisala = True
    elif source == 'onemetre':
        json_file = 'onemetre-vaisala'
        vaisala = True
    elif source == 'superwasp':
        json_file = 'superwasp-log'
        vaisala = False

    url = base_url + json_file
    outfile = params.CONFIG_PATH + json_file + '.json'

    try:
        indata = curl_data_from_url(url, outfile)
        if len(indata) < 2:
            raise IOError
    except:
        time.sleep(0.2)
        try:
            indata = curl_data_from_url(url, outfile)
        except:
            print('Error fetching JSON for {}'.format(source))

    try:
        data = json.loads(indata)
    except:
        print('Error reading data for {}'.format(source))

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
        print('Error parsing temperature for {}'.format(source))

    try:
        weather_dict['pressure'] = float(data['pressure'])
    except:
        print('Error parsing pressure for {}'.format(source))

    try:
        weather_dict['windspeed'] = float(data['wind_speed'])
    except:
        print('Error parsing wind speed for {}'.format(source))

    try:
        weather_dict['winddir'] = float(data['wind_direction'])
    except:
        print('Error parsing wind direction for {}'.format(source))

    try:
        if vaisala:
            weather_dict['humidity'] = float(data['relative_humidity'])
        else:
            weather_dict['humidity'] = float(data['ext_humidity'])
    except:
        print('Error parsing humidity for {}'.format(source))

    try:
        if vaisala:
            if float(data['rain_intensity']) > 0:
                weather_dict['rain'] = True
            else:
                weather_dict['rain'] = False
        else:
            del weather_dict['rain']
    except:
        print('Error parsing rain for {}'.format(source))

    try:
        if vaisala:
            del weather_dict['skytemp']
        else:
            weather_dict['skytemp'] = float(data['sky_temp'])
    except:
        print('Error parsing sky temp for {}'.format(source))

    try:
        weather_dict['update_time'] = Time(data['date'], precision=0).iso
        dt = Time.now() - Time(data['date'])
        weather_dict['dt'] = int(dt.to('second').value)
    except:
        print('Error parsing update time for {}'.format(source))

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
                    print('Error parsing temperature for ing:', columns[1])

            elif columns[0] == 'Pressure':
                try:
                    weather_dict['pressure'] = float(columns[1])
                except:
                    print('Error parsing pressure for ing:', columns[1])

            elif columns[0] == 'Wind' and columns[1] == 'Speed':
                try:
                    weather_dict['windspeed'] = float(columns[2])
                except:
                    print('Error parsing wind speed for ing:', columns[2])

            elif columns[0] == 'Wind' and columns[1] == 'Direction':
                try:
                    weather_dict['winddir'] = str(columns[2])
                except:
                    print('Error parsing wind direction for ing:', columns[2])

            elif columns[0] == 'Wind' and columns[1] == 'Gust':
                try:
                    weather_dict['windgust'] = float(columns[2])
                except:
                    print('Error parsing wind gust for ing:', columns[2])

            elif columns[0] == 'Humidity':
                try:
                    weather_dict['humidity'] = float(columns[1])
                except:
                    print('Error parsing humidity for ing:', columns[1])

            elif columns[0] == 'Rain':
                try:
                    if columns[1] == 'DRY':
                        weather_dict['rain'] = False
                    elif columns[1] == 'WET':
                        weather_dict['rain'] = True
                except:
                    print('Error parsing rain for ing:', columns[1])

            elif len(columns) == 4 and columns[3] == 'UT':
                try:
                    update_date = columns[0].replace('/', '-')
                    update_time = '{}:{}'.format(columns[1],columns[2])
                    update = '{} {}'.format(update_date, update_time)
                    weather_dict['update_time'] = Time(update, precision=0).iso
                    dt = Time.now() - Time(update)
                    weather_dict['dt'] = int(dt.to('second').value)
                except:
                    print('Error parsing update time for ing:', *columns)

    except:
        print('Error parsing ing weather page')

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
                    weather_dict['windspeed'] = float(value)
                except:
                    print('Error parsing wind speed:', value)

            elif label == 'LocalMastWindDirection' or label == 'MainMastWindDirection':
                try:
                    weather_dict['winddir'] = str(value)
                except:
                    print('Error parsing wind direction:', value)

            elif label == 'LocalMastGust' or label == 'MainMastGust':
                try:
                    weather_dict['windgust'] = float(value)
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

    weather = {}

    # Get the weather from the local stations
    local_sources = ['goto', 'onemetre', 'superwasp']
    for source in local_sources:
        try:
            weather[source] = get_local_weather(source)
        except:
            print('Error getting weather from "{}"'.format(source))

    # Get the weather fron the ING webpage as a backup
    if params.USE_ING_WEATHER:
        try:
            weather['ing'] = get_ing_weather()
        except:
            print('Error getting weather from "ing"')

    # Get the internal conditions from the RoomAlert
    internal_sources = ['dome', 'pier']
    for source in internal_sources:
        weather[source] = get_roomalert(source)

    return weather


def check_ping(url, count=3, timeout=10):
    '''Ping a url, and check it responds'''
    try:
        ping_command = 'ping -c {} {}'.format(count, url)
        out = subprocess.check_output(ping_command.split(),
                                      stderr=subprocess.STDOUT,
                                      timeout=timeout)
        if "ttl=" in str(out):
            return True
        else:
            return False
    except:
        return False
