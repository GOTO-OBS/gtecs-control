"""Conditions monitor functions."""

import datetime
import json
import os
import ssl
import subprocess
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


def curl_data_from_url(url, outfile, timeout=5, encoding=None):
    """Fetch data from a URL, store it in a file and return the contents."""
    try:
        curl_command = 'curl -s -m {:.0f} -o {} {}'.format(timeout, outfile, url)
        p = subprocess.Popen(curl_command, shell=True, close_fds=True)
        p.wait()
    except Exception:
        pass

    with open(outfile, 'r', encoding=encoding) as f:
        data = f.read()

    return data


def download_data_from_url(url, outfile, timeout=5, encoding='utf-8', verify=True):
    """Fetch data from a URL, store it in a file and return the contents."""
    if not verify:
        context = ssl._create_unverified_context()
    else:
        context = None

    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as r:
            data = r.read().decode(encoding)
        with open(outfile, 'w', encoding=encoding) as f:
            f.write(data)
    except Exception:
        pass

    with open(outfile, 'r', encoding=encoding) as f:
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
                else:
                    raise ValueError('Unrecognised power class: "{}"'.format(unit_class))

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
    url = 'http://{}'.format(params.ARDUINO_LOCATION)
    outfile = os.path.join(params.FILE_PATH, 'arduino.json')

    indata = download_data_from_url(url, outfile)
    data = json.loads(indata)

    if data['switch_d'] == 1:
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


def get_roomalert(source):
    """Get internal dome temperature and humidity from GOTO RoomAlert system."""
    url = 'http://{}/getData.json'.format(params.ROOMALERT_LOCATION)
    outfile = os.path.join(params.FILE_PATH, 'roomalert.json')

    indata = download_data_from_url(url, outfile)
    try:
        data = json.loads(indata)
    except Exception:
        print('Error reading data for {}'.format(source))
        print(indata)
        raise

    sensors = [sensor_dict['lab'] for sensor_dict in data['sensor']]
    if source not in sensors:
        raise ValueError('Invalid weather source "{}", must be in {}'.format(source, sensors))
    sensor_data = [sensor_dict for sensor_dict in data['sensor'] if sensor_dict['lab'] == source][0]

    weather_dict = {}

    # temperature
    try:
        weather_dict['temperature'] = float(sensor_data['tc'])
    except Exception:
        weather_dict['temperature'] = -999

    # humidity
    try:
        weather_dict['humidity'] = float(sensor_data['h'])
    except Exception:
        weather_dict['humidity'] = -999

    # time
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
    except Exception:
        weather_dict['update_time'] = -999
        weather_dict['dt'] = -999

    return weather_dict


def get_internal():
    """Get the dome internal temperature and humidity.

    If more than one source is defined in params.INTERNAL_WEATHER_SOURCES then this function
    only returns values from the first in the list.

    """
    return get_roomalert(params.INTERNAL_WEATHER_SOURCES[0])


def get_vaisala(source):
    """Get the current weather from the Warwick Vaisala weather stations."""
    url = 'http://{}/data/raw/{}-vaisala'.format(params.VAISALA_LOCATION, source)
    outfile = os.path.join(params.FILE_PATH, '{}-vaisala.json'.format(source))

    indata = download_data_from_url(url, outfile)
    if len(indata) < 2 or '500 Internal Server Error' in indata:
        raise IOError

    try:
        data = json.loads(indata)
    except Exception:
        print('Error reading data for {}'.format(source))
        print(indata)
        raise

    weather_dict = {}

    # temperature
    try:
        assert data['temperature_valid']
        weather_dict['temperature'] = float(data['temperature'])
    except Exception:
        weather_dict['temperature'] = -999

    # pressure
    try:
        assert data['pressure_valid']
        weather_dict['pressure'] = float(data['pressure'])
    except Exception:
        weather_dict['pressure'] = -999

    # windspeed
    try:
        assert data['wind_speed_valid']
        weather_dict['windspeed'] = float(data['wind_speed'])
    except Exception:
        weather_dict['windspeed'] = -999

    # winddir
    try:
        assert data['wind_direction_valid']
        weather_dict['winddir'] = float(data['wind_direction'])
    except Exception:
        weather_dict['winddir'] = -999

    # humidity
    try:
        assert data['relative_humidity_valid']
        weather_dict['humidity'] = float(data['relative_humidity'])
    except Exception:
        weather_dict['humidity'] = -999

    # rain
    try:
        assert data['rain_intensity_valid']
        weather_dict['rain'] = float(data['rain_intensity']) > 0
    except Exception:
        weather_dict['rain'] = -999

    # dew point
    try:
        assert data['dew_point_delta_valid']
        weather_dict['dew_point'] = float(data['dew_point_delta'])
    except Exception:
        weather_dict['dew_point'] = -999

    # time
    try:
        weather_dict['update_time'] = Time(data['date'], precision=0).iso
        dt = Time.now() - Time(data['date'])
        weather_dict['dt'] = int(dt.to('second').value)
    except Exception:
        weather_dict['update_time'] = -999
        weather_dict['dt'] = -999

    return weather_dict


def get_tng():
    """Get the seeing and dust level from the TNG."""
    url = 'https://tngweb.tng.iac.es/api/meteo/weather'
    outfile = os.path.join(params.FILE_PATH, 'tng.json')

    indata = download_data_from_url(url, outfile, verify=False)
    if len(indata) < 2 or '500 Internal Server Error' in indata:
        raise IOError

    try:
        data = json.loads(indata)
    except Exception:
        print('Error reading data from TNG')
        print(indata)
        raise

    weather_dict = {}

    # seeing
    try:
        try:
            weather_dict['seeing'] = float(data['seeing']['median'])
            weather_dict['seeing_error'] = float(data['seeing']['stdev'])
        except Exception:
            weather_dict['seeing'] = float(data['seeing']['value'])
            weather_dict['seeing_error'] = None
        weather_dict['seeing_update_time'] = Time(data['seeing']['timestamp'], precision=0).iso
        dt = Time.now() - Time(weather_dict['seeing_update_time'])
        weather_dict['seeing_dt'] = int(dt.to('second').value)
    except Exception:
        weather_dict['seeing'] = -999
        weather_dict['seeing_error'] = -999
        weather_dict['seeing_update_time'] = -999
        weather_dict['seeing_dt'] = -999

    # dust
    try:
        weather_dict['dust'] = float(data['dust']['value'])
        weather_dict['dust_update_time'] = Time(data['dust']['timestamp'], precision=0).iso
        dt = Time.now() - Time(weather_dict['dust_update_time'])
        weather_dict['dust_dt'] = int(dt.to('second').value)
    except Exception:
        weather_dict['dust'] = -999
        weather_dict['dust_update_time'] = -999
        weather_dict['dust_dt'] = -999

    return weather_dict


def get_robodimm():
    """Get the current readings from the ING RoboDIMM."""
    url = 'http://catserver.ing.iac.es/robodimm/robodimm.php'
    outfile = os.path.join(params.FILE_PATH, 'dimm.php')
    indata = download_data_from_url(url, outfile)

    indata = indata.replace('>', '>\n').split('\n')
    data = indata[-3].split()

    weather_dict = {}

    # seeing
    try:
        # seeing is estimated as the average of the three smallest values,
        # matching the NOT weather pages
        # (from https://github.com/warwick-one-metre/robodimmd/blob/master/robodimmd)
        samples = sorted(float(s) for s in data[10:14])
        weather_dict['seeing'] = round((samples[0] + samples[1] + samples[2]) / 3, 2)
    except Exception:
        weather_dict['seeing'] = -999

    # time
    try:
        datestr = data[1] + ' ' + data[2] + '00'
        utc = datetime.timezone.utc
        date = datetime.datetime.strptime(datestr, '%Y-%m-%d %H:%M:%S%z').astimezone(utc)
        date = Time(date)
        weather_dict['update_time'] = date.iso
        dt = Time.now() - date
        weather_dict['dt'] = int(dt.to('second').value)
    except Exception:
        weather_dict['update_time'] = -999
        weather_dict['dt'] = -999

    return weather_dict


def get_ing():
    """Get the current weather from the ING weather page (JKT mast)."""
    url = 'http://catserver.ing.iac.es/weather/'
    outfile = os.path.join(params.FILE_PATH, 'weather.html')
    indata = download_data_from_url(url, outfile, encoding='ISO-8859-1')

    weather_dict = {}

    for line in indata.split('\n'):
        columns = misc.remove_html_tags(line).replace(':', ' ').split()
        if not columns:
            continue

        if columns[0] == 'Temperature':
            try:
                weather_dict['temperature'] = float(columns[1])
            except Exception:
                weather_dict['temperature'] = -999

        elif columns[0] == 'Pressure':
            try:
                weather_dict['pressure'] = float(columns[1])
            except Exception:
                weather_dict['pressure'] = -999

        elif columns[0] == 'Wind' and columns[1] == 'Speed':
            try:
                weather_dict['windspeed'] = float(columns[2])
            except Exception:
                weather_dict['windspeed'] = -999

        elif columns[0] == 'Wind' and columns[1] == 'Direction':
            try:
                weather_dict['winddir'] = str(columns[2])
            except Exception:
                weather_dict['winddir'] = -999

        elif columns[0] == 'Wind' and columns[1] == 'Gust':
            try:
                weather_dict['windgust'] = float(columns[2])
            except Exception:
                weather_dict['windgust'] = -999

        elif columns[0] == 'Humidity':
            try:
                weather_dict['humidity'] = float(columns[1])
            except Exception:
                weather_dict['humidity'] = -999

        elif columns[0] == 'Rain':
            try:
                if columns[1] == 'DRY':
                    weather_dict['rain'] = False
                elif columns[1] == 'WET':
                    weather_dict['rain'] = True
            except Exception:
                weather_dict['rain'] = -999

        elif len(columns) == 4 and columns[3] == 'UT':
            try:
                update_date = columns[0].replace('/', '-')
                update_time = '{}:{}'.format(columns[1], columns[2])
                update = '{} {}'.format(update_date, update_time)
                weather_dict['update_time'] = Time(update, precision=0).iso
                dt = Time.now() - Time(update)
                weather_dict['dt'] = int(dt.to('second').value)
            except Exception:
                weather_dict['update_time'] = -999
                weather_dict['dt'] = -999

    return weather_dict


def get_ing_internal(source):
    """Get the current weather from the internal ING xml weather file."""
    if source == 'wht':
        url = 'http://whtmetsystem.ing.iac.es/WeatherXMLData/LocalData.xml'
    elif source == 'int':
        url = 'http://intmetsystem.ing.iac.es/WeatherXMLData/LocalData.xml'
    elif source == 'jkt':
        url = 'http://intmetsystem.ing.iac.es/WeatherXMLData/MainData.xml'
    else:
        sources = ['wht', 'int', 'jkt']
        raise ValueError('Invalid weather source "{}", must be in {}'.format(source, sources))

    outfile = os.path.join(params.FILE_PATH, 'weather.xml')
    indata = download_data_from_url(url, outfile)

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
    """Get rain readings from the W1m boards."""
    rain_daemon_uri = 'PYRO:{}@{}:{}'.format(params.RAINDAEMON_NAME,
                                             params.RAINDAEMON_LOCATION,
                                             params.RAINDAEMON_PORT)
    with Pyro4.Proxy(rain_daemon_uri) as rain_daemon:
        rain_daemon._pyroSerializer = 'serpent'
        info = rain_daemon.last_measurement()

    rain_dict = {}

    rain_dict['update_time'] = Time(info['date'])
    dt = Time.now() - rain_dict['update_time']
    rain_dict['dt'] = int(dt.to('second').value)

    if info['unsafe_boards'] > 0:
        rain_dict['rain'] = True
    else:
        rain_dict['rain'] = False

    return rain_dict


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
