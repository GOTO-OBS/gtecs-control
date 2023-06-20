"""Conditions functions for other on-site observatories."""

import json
import os
import re
import traceback
from datetime import datetime

from astropy.time import Time

import pytz

from .utils import download_data_from_url


def get_ing():
    """Get the current weather from the ING weather page (JKT mast)."""
    url = 'http://catserver.ing.iac.es/weather/'
    indata = download_data_from_url(url, outfile='weather.html', encoding='ISO-8859-1')

    weather_dict = {}

    for line in indata.split('\n'):
        # Remove HTML tags
        p = re.compile(r'<.*?>')
        line = p.sub('', line).strip()

        columns = line.replace(':', ' ').split()
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

    indata = download_data_from_url(url, outfile='weather.xml')

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


def get_robodimm():
    """Get the current readings from the ING RoboDIMM."""
    url = 'http://catserver.ing.iac.es/robodimm/robodimm.php'
    indata = download_data_from_url(url, outfile='dimm.php')
    if 'DB Error: connect failed' in indata:
        raise IOError('Failed to connect to RoboDIMM')

    try:
        indata = indata.replace('>', '>\n').split('\n')
        data = indata[-3].split()
    except Exception:
        print('Error reading data from RoboDIMM')
        print(indata)
        raise

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


def get_tng():
    """Get the seeing and dust level from the TNG."""
    url = 'https://tngweb.tng.iac.es/api/meteo/weather'
    indata = download_data_from_url(url, outfile='tng.json', verify=False)
    if len(indata) < 2 or '500 Internal Server Error' in indata:
        raise IOError('Failed to connect to ING')

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


def get_aat():
    """Get the current weather from the AAT weather page."""
    url = "http://aat-ops.anu.edu.au/met/metdata.dat"
    indata = download_data_from_url(url, outfile='metdata.dat')

    weather_dict = {'update_time': -999,  # time of last update
                    'dt': -999,  # time since last update
                    'rain': -999,  # rain dry/wet
                    'temperature': -999,
                    'pressure': -999,
                    'winddir': -999,
                    'windspeed': -999,
                    'windgust': -999,
                    'humidity': -999,
                    'dew_point': -999
                    }
    data = re.split('\n|\t', indata)
    try:
        y, d, month = [int(x) for x in
                       data[0].replace('"', '').lstrip().replace('.', '').split('-')[::-1]]
        h, mins, s = [int(x) for x in data[1].split(':')]
        update = pytz.timezone('Australia/Sydney').localize(datetime(y, month, d, h, mins, s))

        weather_dict['update_time'] = Time(update).iso
        dt = Time.now() - Time(update)
        weather_dict['dt'] = dt.sec
    except Exception:
        weather_dict['update_time'] = -999

    try:
        weather_dict['temperature'] = float(data[2])
    except Exception:
        weather_dict['temperature'] = -999

    try:
        weather_dict['dew_point'] = float(data[2])-float(data[5])
    except Exception:
        weather_dict['dew_point'] = -999

    try:
        weather_dict['humidity'] = float(data[6])
    except Exception:
        weather_dict['humidity'] = -999

    try:
        weather_dict['pressure'] = float(data[7])
    except Exception:
        weather_dict['pressure'] = -999

    try:
        weather_dict['windspeed'] = float(data[8])
    except Exception:
        weather_dict['windspeed'] = -999

    try:
        weather_dict['windgust'] = float(data[9])
    except Exception:
        weather_dict['windgust'] = -999

    try:
        weather_dict['winddir'] = float(data[10])
    except Exception:
        weather_dict['winddir'] = -999

    try:
        weather_dict['rain'] = bool(int(data[17]))
    except Exception:
        weather_dict['rain'] = -999

    return weather_dict
