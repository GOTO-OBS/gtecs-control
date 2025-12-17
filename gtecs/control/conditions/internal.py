"""Conditions functions for internal conditions sensors."""

import json

from astropy.time import Time

import Pyro4

from .utils import download_data_from_url


def get_roomalert(source, ip):
    """Get the internal conditions from the RoomAlert system."""
    url = 'http://{}/getData.json'.format(ip)
    indata = download_data_from_url(url, outfile='roomalert.json')
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


def get_roomalert_json(source, location):
    """Get the internal conditions from the RoomAlert system."""
    url = 'http://{}/{}-roomalert'.format(location, source)
    indata = download_data_from_url(url, outfile='{}-roomalert.json'.format(source))
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
        weather_dict['temperature'] = float(data['internal_temp'])
    except Exception:
        weather_dict['temperature'] = -999

    # humidity
    try:
        weather_dict['humidity'] = float(data['internal_humidity'])
    except Exception:
        weather_dict['humidity'] = -999

    # time
    try:
        weather_dict['update_time'] = Time(data['date'], precision=0).iso
        dt = Time.now() - Time(data['date'])
        weather_dict['dt'] = int(dt.to('second').value)
    except Exception:
        weather_dict['update_time'] = -999
        weather_dict['dt'] = -999

    return weather_dict


def get_internal_daemon(uri):
    """Get the internal conditions from a local environment daemon.

    There are multiple possible data formats used by different versions with
    different key names and number of sensors.

    """
    # Get the latest measurement from the Pyro daemon
    with Pyro4.Proxy(uri) as proxy:
        proxy._pyroTimeout = 5
        proxy._pyroSerializer = 'serpent'
        data = proxy.last_measurement()

    weather_dict = {}

    # Get the update time
    weather_dict['update_time'] = Time(data['date'], precision=0).iso
    dt = Time.now() - Time(weather_dict['update_time'])
    weather_dict['dt'] = int(dt.to('second').value)

    # Try to get temperature and humidity
    try:
        # Option 1: just single 'temperature' and 'humidity' values
        if 'temperature' in data:
            if data['temperature_valid']:
                weather_dict['temperature'] = data['temperature']
            else:
                weather_dict['temperature'] = -999
            if data['relative_humidity_valid']:
                weather_dict['humidity'] = data['relative_humidity']
            else:
                weather_dict['humidity'] = -999

        # Option 2: single 'internal_temp' and 'internal_humidity' values
        elif 'internal_temp' in data:
            if data['internal_temp_valid']:
                weather_dict['temperature'] = data['internal_temp']
            else:
                weather_dict['temperature'] = -999
            if data['internal_humidity_valid']:
                weather_dict['humidity'] = data['internal_humidity']
            else:
                weather_dict['humidity'] = -999

        # Option 3: two separate sensors for east and west
        elif 'internal_temp_east' in data:
            weather_dict['temperature'] = {}
            if data['internal_temp_east_valid']:
                weather_dict['temperature']['east'] = data['internal_temp_east']
            else:
                weather_dict['temperature']['east'] = -999
            if data['internal_temp_west_valid']:
                weather_dict['temperature']['west'] = data['internal_temp_west']
            else:
                weather_dict['temperature']['west'] = -999

            weather_dict['humidity'] = {}
            if data['internal_humidity_east_valid']:
                weather_dict['humidity']['east'] = data['internal_humidity_east']
            else:
                weather_dict['humidity']['east'] = -999
            if data['internal_humidity_west_valid']:
                weather_dict['humidity']['west'] = data['internal_humidity_west']
            else:
                weather_dict['humidity']['west'] = -999

        # Option 4: two separate numbered sensors
        elif 'temperature1' in data:
            weather_dict['temperature'] = {}
            if data['temperature1_valid']:
                weather_dict['temperature']['1'] = data['temperature1']
            else:
                weather_dict['temperature']['1'] = -999
            if data['temperature2_valid']:
                weather_dict['temperature']['2'] = data['temperature2']
            else:
                weather_dict['temperature']['2'] = -999

            weather_dict['humidity'] = {}
            if data['humidity1_valid']:
                weather_dict['humidity']['1'] = data['humidity1']
            else:
                weather_dict['humidity']['1'] = -999
            if data['humidity2_valid']:
                weather_dict['humidity']['2'] = data['humidity2']
            else:
                weather_dict['humidity']['2'] = -999

        else:
            raise KeyError('No known temperature/humidity keys found in data')
    except Exception:
        weather_dict['temperature'] = -999
        weather_dict['humidity'] = -999

    return weather_dict


def get_arduino_readout(file):
    """Get the internal conditions from the Arduino readout file."""
    try:
        with open(file, 'r') as fp:
            lines = fp.readlines()

        last_line = lines[-1].strip().split(';')

        weather_dict = {}

        update_time = last_line[0][:-2] if last_line[0][-2] == ':' else last_line[0]
        update_time = Time(update_time, precision=0)
        dt = (Time.now() - update_time).to('second').value
        temperature = float(last_line[2])
        humidity = float(last_line[1])

        weather_dict = {
            'update_time': update_time.iso,
            'dt': int(dt),
            'temperature': temperature,
            'humidity': humidity,
        }
    except Exception:
        weather_dict = {
            'update_time': -999,
            'dt': 0,
            'temperature': -999,
            'humidity': -999,
        }

    return weather_dict
