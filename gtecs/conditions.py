"""Conditions monitor functions."""

import json
import os
import subprocess
import time
import traceback
import urllib
import warnings

from astropy._erfa import ErfaWarning
from astropy.time import Time

import cv2

import numpy as np

import Pyro4

from . import misc
from . import params
from .flags import Status
from .hardware.power import APCUPS, FakeUPS


warnings.simplefilter('error', ErfaWarning)


def curl_data_from_url(url, outfile, encoding=None):
    """Fetch data from a URL, store it in a file and return the contents."""
    wait_time = int(params.CURL_WAIT_TIME)
    curl_command = 'curl -s -m {:.0f} -o {} {}'.format(wait_time, outfile, url)
    try:
        p = subprocess.Popen(curl_command, shell=True, close_fds=True)
        p.wait()
    except Exception:
        print('Error fetching URL "{}"'.format(url))

    if encoding:
        with open(outfile, 'r', encoding=encoding) as f:
            data = f.read()
    else:
        with open(outfile, 'r') as f:
            data = f.read()

    return data


def get_ups():
    """Get battery percent remaining and current status from GOTO UPSs."""
    percents = []
    statuses = []
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
                percents.append(remaining)

                # Check status too
                status = ups.status()
                if status != 'Normal':
                    normal = False
                else:
                    normal = True
                statuses.append(normal)
            except Exception:
                percents.append(-999)
                statuses.append(-999)
    return percents, statuses


def hatch_closed():
    """Get hatch status from GOTO Dome Arduino."""
    status = Status()
    url = params.ARDUINO_LOCATION
    outfile = os.path.join(params.FILE_PATH, 'arduino.json')

    try:
        indata = curl_data_from_url(url, outfile)
        data = json.loads(indata)
    except Exception:
        print('Error fetching hatch data')
        return False

    try:
        closed = data['switch_d']
        if closed:
            return True
        else:
            if params.IGNORE_HATCH:
                print('Hatch is open but IGNORE_HATCH is true')
                return True
            elif status.emergency_shutdown:
                print('Hatch is open during emergency shutdown!')
                return False
            elif status.mode != 'robotic':
                print('Hatch is open but not in robotic mode')
                return True
            else:
                return False
    except Exception:
        print('Error parsing hatch status')
        return False


def get_roomalert(source):
    """Get internal dome temperature and humidity from GOTO RoomAlert system."""
    sources = ['dome', 'pier']
    if source not in sources:
        raise ValueError('Invalid weather source "{}", must be in {}'.format(source, sources))

    url = '10.2.6.5/getData.json'
    outfile = os.path.join(params.FILE_PATH, 'roomalert.json')

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
    except Exception:
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

    except Exception:
        print('Error parsing RoomAlert page')
        traceback.print_exc()

    return weather_dict


def get_local_weather(source):
    """Get the current weather from the Warwick stations."""
    source = source.lower()
    sources = ['goto', 'w1m', 'superwasp']
    if source not in sources:
        raise ValueError('Invalid weather source "{}", must be in {}'.format(source, sources))

    base_url = 'http://10.2.6.100/data/raw/'

    if source == 'goto':
        json_file = 'goto-vaisala'
        vaisala = True
    elif source == 'w1m':
        json_file = 'w1m-vaisala'
        vaisala = True
    elif source == 'superwasp':
        json_file = 'superwasp-log'
        vaisala = False

    url = base_url + json_file
    filename = json_file + '.json'
    outfile = os.path.join(params.FILE_PATH, filename)

    try:
        indata = curl_data_from_url(url, outfile)
        if len(indata) < 2 or '500 Internal Server Error' in indata:
            raise IOError
    except Exception:
        time.sleep(0.2)
        try:
            indata = curl_data_from_url(url, outfile)
        except Exception:
            print('Error fetching JSON for {}'.format(source))

    try:
        data = json.loads(indata)
    except Exception:
        print('Error reading data for {}'.format(source))
        traceback.print_exc()
        print(indata)

    weather_dict = {'update_time': -999,
                    'dt': -999,
                    'rain': -999,
                    'temperature': -999,
                    'pressure': -999,
                    'winddir': -999,
                    'windspeed': -999,
                    'humidity': -999,
                    'skytemp': -999,
                    'dew_point': -999,
                    }

    try:
        if vaisala and data['temperature_valid']:
            weather_dict['temperature'] = float(data['temperature'])
        elif not vaisala:
            weather_dict['temperature'] = float(data['ext_temperature'])
    except Exception:
        print('Error parsing temperature for {}'.format(source))

    try:
        if (vaisala and data['pressure_valid']) or not vaisala:
            weather_dict['pressure'] = float(data['pressure'])
    except Exception:
        print('Error parsing pressure for {}'.format(source))

    try:
        if vaisala and data['wind_speed_valid']:
            weather_dict['windspeed'] = float(data['wind_speed'])
        elif not vaisala:
            # SuperWASP wind readings aren't trustworthy
            del weather_dict['windspeed']
    except Exception:
        print('Error parsing wind speed for {}'.format(source))

    try:
        if vaisala and data['wind_direction_valid']:
            weather_dict['winddir'] = float(data['wind_direction'])
        elif not vaisala:
            # SuperWASP wind readings aren't trustworthy
            del weather_dict['winddir']
    except Exception:
        print('Error parsing wind direction for {}'.format(source))

    try:
        if vaisala and data['relative_humidity_valid']:
            weather_dict['humidity'] = float(data['relative_humidity'])
        elif not vaisala:
            weather_dict['humidity'] = float(data['ext_humidity'])
    except Exception:
        print('Error parsing humidity for {}'.format(source))

    try:
        if vaisala and data['rain_intensity_valid']:
            if float(data['rain_intensity']) > 0:
                weather_dict['rain'] = True
            else:
                weather_dict['rain'] = False
        elif not vaisala:
            # SuperWASP doesn't have a rain sensor
            del weather_dict['rain']
    except Exception:
        print('Error parsing rain for {}'.format(source))

    try:
        if vaisala:
            del weather_dict['skytemp']
        else:
            weather_dict['skytemp'] = float(data['sky_temp'])
    except Exception:
        print('Error parsing sky temp for {}'.format(source))

    try:
        if vaisala and data['dew_point_delta_valid'] or not vaisala:
            weather_dict['dew_point'] = float(data['dew_point_delta'])
    except Exception:
        print('Error parsing dew point for {}'.format(source))

    try:
        weather_dict['update_time'] = Time(data['date'], precision=0).iso
        dt = Time.now() - Time(data['date'])
        weather_dict['dt'] = int(dt.to('second').value)
    except Exception:
        print('Error parsing update time for {}'.format(source))

    return weather_dict


def get_ing_weather():
    """Get the current weather from the ING weather page (JKT mast)."""
    url = 'http://catserver.ing.iac.es/weather/'
    outfile = os.path.join(params.FILE_PATH, 'weather.html')
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
            columns = misc.remove_html_tags(line).replace(':', ' ').split()
            if not columns:
                continue

            if columns[0] == 'Temperature':
                try:
                    weather_dict['temperature'] = float(columns[1])
                except Exception:
                    print('Error parsing temperature for ing:', columns[1])

            elif columns[0] == 'Pressure':
                try:
                    weather_dict['pressure'] = float(columns[1])
                except Exception:
                    print('Error parsing pressure for ing:', columns[1])

            elif columns[0] == 'Wind' and columns[1] == 'Speed':
                try:
                    weather_dict['windspeed'] = float(columns[2])
                except Exception:
                    print('Error parsing wind speed for ing:', columns[2])

            elif columns[0] == 'Wind' and columns[1] == 'Direction':
                try:
                    weather_dict['winddir'] = str(columns[2])
                except Exception:
                    print('Error parsing wind direction for ing:', columns[2])

            elif columns[0] == 'Wind' and columns[1] == 'Gust':
                try:
                    weather_dict['windgust'] = float(columns[2])
                except Exception:
                    print('Error parsing wind gust for ing:', columns[2])

            elif columns[0] == 'Humidity':
                try:
                    weather_dict['humidity'] = float(columns[1])
                except Exception:
                    print('Error parsing humidity for ing:', columns[1])

            elif columns[0] == 'Rain':
                try:
                    if columns[1] == 'DRY':
                        weather_dict['rain'] = False
                    elif columns[1] == 'WET':
                        weather_dict['rain'] = True
                except Exception:
                    print('Error parsing rain for ing:', columns[1])

            elif len(columns) == 4 and columns[3] == 'UT':
                try:
                    update_date = columns[0].replace('/', '-')
                    update_time = '{}:{}'.format(columns[1], columns[2])
                    update = '{} {}'.format(update_date, update_time)
                    weather_dict['update_time'] = Time(update, precision=0).iso
                    dt = Time.now() - Time(update)
                    weather_dict['dt'] = int(dt.to('second').value)
                except Exception:
                    print('Error parsing update time for ing:', *columns)

    except Exception:
        print('Error parsing ing weather page')
        traceback.print_exc()

    return weather_dict


def get_ing_internal_weather(weather_source):
    """Get the current weather from the internal ING xml weather file."""
    if weather_source == 'wht':
        url = 'http://whtmetsystem.ing.iac.es/WeatherXMLData/LocalData.xml'
    elif weather_source == 'int':
        url = 'http://intmetsystem.ing.iac.es/WeatherXMLData/LocalData.xml'
    elif weather_source == 'jkt':
        url = 'http://intmetsystem.ing.iac.es/WeatherXMLData/MainData.xml'

    outfile = os.path.join(params.FILE_PATH, 'weather.xml')
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
                label = columns[1].split('\"')[1].split('.')[2]
                value = columns[2].split('\"')[1]
            except Exception:
                continue

            if label == 'date':
                try:
                    update = float(value)
                    weather_dict['update_time'] = Time(update, precision=0).iso
                    dt = Time.now() - Time(update)
                    weather_dict['dt'] = int(dt.to('second').value)
                except Exception:
                    print('Error parsing update time:', value)

            elif label == 'LocalMastAirTemp' or label == 'MainMastAirTemp':
                try:
                    weather_dict['temperature'] = float(value)
                except Exception:
                    print('Error parsing temperature:', value)

            elif label == 'LocalMastPressure' or label == 'MainMastPressure':
                try:
                    weather_dict['pressure'] = float(value)
                except Exception:
                    print('Error parsing pressure:', value)

            elif label == 'LocalMastWindSpeed' or label == 'MainMastWindSpeed':
                try:
                    weather_dict['windspeed'] = float(value)
                except Exception:
                    print('Error parsing wind speed:', value)

            elif label == 'LocalMastWindDirection' or label == 'MainMastWindDirection':
                try:
                    weather_dict['winddir'] = str(value)
                except Exception:
                    print('Error parsing wind direction:', value)

            elif label == 'LocalMastGust' or label == 'MainMastGust':
                try:
                    weather_dict['windgust'] = float(value)
                except Exception:
                    print('Error parsing wind gust:', value)

            elif label == 'LocalMastHumidity' or label == 'MainMastHumidity':
                try:
                    weather_dict['humidity'] = float(value)
                except Exception:
                    print('Error parsing humidity:', value)

            elif label == 'LocalMastWetness' or label == 'MainMastWetness':
                try:
                    if float(value) <= 0:
                        weather_dict['rain'] = False
                    elif float(value) >= 1:
                        weather_dict['rain'] = True
                except Exception:
                    print('Error parsing rain:', value)

    except Exception:
        print('Error parsing weather page')
        traceback.print_exc()

    return weather_dict


def get_rain():
    """Get rain readings from the 1m boards."""
    rain_daemon_uri = 'PYRO:onemetre_rain_daemon@10.2.6.202:9017'

    rain_dict = {'update_time': -999,
                 'dt': -999,
                 'rain': -999,
                 }

    try:
        with Pyro4.Proxy(rain_daemon_uri) as rain_daemon:
            rain_daemon._pyroSerializer = 'serpent'
            info = rain_daemon.last_measurement()

        rain_dict['update_time'] = Time(info['date'])
        dt = Time.now() - rain_dict['update_time']
        rain_dict['dt'] = int(dt.to('second').value)

        if info['unsafe_boards'] > 0:
            rain_dict['rain'] = True
        else:
            rain_dict['rain'] = False

    except Exception:
        print('Error reading rain boards')
        traceback.print_exc()

    return rain_dict


def get_weather():
    """Get the current weather conditions."""
    weather = {}

    # Get the weather from the local stations
    local_sources = ['goto', 'w1m', 'superwasp']
    for source in local_sources:
        try:
            weather[source] = get_local_weather(source)
        except Exception:
            print('Error getting weather from "{}"'.format(source))
            traceback.print_exc()

    # Get the W1m rain boards reading
    if params.USE_W1M_RAINBOARDS:
        try:
            rain_info = get_rain()
            # Replace the local rain measurements
            weather['w1m']['rain'] = rain_info['rain']
            del weather['goto']['rain']
        except Exception:
            print('Error getting weather from "rain"')
            traceback.print_exc()

    # Get the weather fron the ING webpage as a backup
    if params.USE_ING_WEATHER:
        try:
            weather['ing'] = get_ing_weather()
        except Exception:
            print('Error getting weather from "ing"')
            traceback.print_exc()

    # Get the internal conditions from the RoomAlert
    internal_sources = ['pier']
    for source in internal_sources:
        try:
            weather[source] = get_roomalert(source)
        except Exception:
            print('Error getting weather from "{}"'.format(source))
            traceback.print_exc()

    return weather


def check_ping(url, count=3, timeout=10):
    """Ping a url, and check it responds."""
    try:
        ping_command = 'ping -c {} {}'.format(count, url)
        out = subprocess.check_output(ping_command.split(),
                                      stderr=subprocess.STDOUT,
                                      timeout=timeout)
        if 'ttl=' in str(out):
            return True
        else:
            return False
    except Exception:
        return False


def get_diskspace_remaining(path):
    """Get the percentage diskspace remaining from a given path."""
    statvfs = os.statvfs(path)

    available = statvfs.f_bsize * statvfs.f_bavail / 1024
    total = statvfs.f_bsize * statvfs.f_blocks / 1024

    return available / total


def get_satellite_clouds():
    """Download the Eumetsat IR image from sat24.com, and use it to judge clouds over La Palma.

    Returns a value between 0 and 1, representing the median pixel illumination.
    """
    # Download image
    image_url = 'https://en.sat24.com/image?type=infraPolair&region=ce'
    with urllib.request.urlopen(image_url, timeout=2) as url:
        arr = np.asarray(bytearray(url.read()), dtype='uint8')
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

    # Crop La Palma area
    img_crop = img[205:225, 310:330]

    # Get standard deviation between the channels to mask out the coastline
    std = np.std(cv2.split(img_crop), axis=0)
    mask = std < 20

    # Mask image and average across the colour channels
    img_masked = img_crop[mask]
    img_av = np.mean(img_masked, axis=1)

    # Measure the median pixel value, and scale by the pixel range (0-255)
    median = np.median(img_av) / 255
    return median


def get_tng_conditions():
    """Get the seeing and dust level from the TNG."""
    url = 'https://tngweb.tng.iac.es/api/meteo/weather'
    outfile = os.path.join(params.FILE_PATH, 'tng.json')

    try:
        indata = curl_data_from_url(url, outfile)
        if len(indata) < 2 or '500 Internal Server Error' in indata:
            raise IOError
    except Exception:
        time.sleep(0.2)
        try:
            indata = curl_data_from_url(url, outfile)
        except Exception:
            print('Error fetching JSON from TNG')

    try:
        data = json.loads(indata)
    except Exception:
        print('Error reading data from TNG')
        traceback.print_exc()
        print(indata)

    weather_dict = {'seeing': -999,
                    'seeing_error': -999,
                    'seeing_update_time': -999,
                    'seeing_dt': -999,
                    'dust': -999,
                    'dust_update_time': -999,
                    'dust_dt': -999,
                    }

    try:
        weather_dict['seeing'] = float(data['seeing']['median'])
        weather_dict['seeing_error'] = float(data['seeing']['stdev'])
        weather_dict['seeing_update_time'] = Time(data['seeing']['timestamp'], precision=0).iso
        dt = Time.now() - Time(weather_dict['seeing_update_time'])
        weather_dict['seeing_dt'] = int(dt.to('second').value)
    except Exception:
        print('Error parsing seeing from TNG')

    try:
        weather_dict['dust'] = float(data['dust']['value'])
        weather_dict['dust_update_time'] = Time(data['dust']['timestamp'], precision=0).iso
        dt = Time.now() - Time(weather_dict['dust_update_time'])
        weather_dict['dust_dt'] = int(dt.to('second').value)
    except Exception:
        print('Error parsing dust level from TNG')

    return weather_dict
