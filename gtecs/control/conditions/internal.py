"""Conditions functions for internal conditions sensors."""

import json

from astropy.time import Time

import Pyro4

from .utils import download_data_from_url


def get_roomalert(source, ip):
    """Get internal conditions from the RoomAlert system."""
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


def get_domealert_daemon(uri):
    """Get internal readings from Paul's dome alert board."""
    with Pyro4.Proxy(uri) as pyro_daemon:
        pyro_daemon._pyroSerializer = 'serpent'
        info = pyro_daemon.last_measurement()

    weather_dict = {}

    weather_dict['update_time'] = Time(info['date'], precision=0).iso
    dt = Time.now() - Time(weather_dict['update_time'])
    weather_dict['dt'] = int(dt.to('second').value)

    try:
        if info['internal_temp_valid']:
            weather_dict['temperature'] = info['internal_temp']
        else:
            weather_dict['temperature'] = -999
        if info['internal_humidity_valid']:
            weather_dict['humidity'] = info['internal_humidity']
        else:
            weather_dict['humidity'] = -999
    except Exception:
        weather_dict['temperature'] = -999
        weather_dict['humidity'] = -999

    return weather_dict


def get_SHT35_daemon(uri):
    """Get internal readings from Paul's SHT35 board."""
    with Pyro4.Proxy(uri) as pyro_daemon:
        pyro_daemon._pyroSerializer = 'serpent'
        info = pyro_daemon.last_measurement()

    weather_dict = {}

    weather_dict['update_time'] = Time(info['date'], precision=0).iso
    dt = Time.now() - Time(weather_dict['update_time'])
    weather_dict['dt'] = int(dt.to('second').value)

    try:
        if info['temperature_valid']:
            weather_dict['temperature'] = info['temperature']
        else:
            weather_dict['temperature'] = -999
        if info['relative_humidity_valid']:
            weather_dict['humidity'] = info['relative_humidity']
        else:
            weather_dict['humidity'] = -999
    except Exception:
        weather_dict['temperature'] = -999
        weather_dict['humidity'] = -999

    return weather_dict
