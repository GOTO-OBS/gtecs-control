"""Conditions functions for local conditions sensors."""

import json

from astropy.time import Time

import Pyro4

from .utils import download_data_from_url


def get_vaisala_json(source, location):
    """Get the current weather from the local Vaisala weather station."""
    url = 'http://{}/{}-vaisala'.format(location, source)
    indata = download_data_from_url(url, outfile='{}-vaisala.json'.format(source))
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

    # windgust
    try:
        assert data['wind_gust_valid']
        weather_dict['windgust'] = float(data['wind_gust'])
    except Exception:
        weather_dict['windgust'] = -999

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


def get_vaisala_daemon(uri):
    """Get the current weather from the local Vaisala weather station."""
    with Pyro4.Proxy(uri) as proxy:
        proxy._pyroTimeout = 5
        proxy._pyroSerializer = 'serpent'
        data = proxy.last_measurement()

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

    # windgust
    try:
        assert data['wind_gust_valid']
        weather_dict['windgust'] = float(data['wind_gust'])
    except Exception:
        weather_dict['windgust'] = -999

    # humidity
    try:
        assert data['relative_humidity_valid']
        weather_dict['humidity'] = float(data['relative_humidity'])
    except Exception:
        weather_dict['humidity'] = -999

    # dew point
    try:
        assert data['dew_point_delta_valid']
        weather_dict['dew_point'] = float(data['dew_point_delta'])
    except Exception:
        weather_dict['dew_point'] = -999

    # rain
    try:
        assert data['rain_intensity_valid']
        weather_dict['rain'] = float(data['rain_intensity']) > 0
    except Exception:
        weather_dict['rain'] = -999

    # rain boards (custom additions)
    if any('rg11' in key for key in data):
        weather_dict['has_rainboards'] = True
        try:
            assert data['rg11_unsafe_valid']
            assert data['rg11_total_valid']
            weather_dict['rainboard_unsafe'] = float(data['rg11_unsafe'])
            weather_dict['rainboard_total'] = float(data['rg11_total'])
            if weather_dict['rainboard_unsafe'] > 0:
                weather_dict['rainboard_rain'] = True
            else:
                weather_dict['rainboard_rain'] = False
        except Exception:
            weather_dict['rainboard_unsafe'] = -999
            weather_dict['rainboard_total'] = -999
            weather_dict['rainboard_rain'] = -999
    else:
        weather_dict['has_rainboards'] = False

    # time
    try:
        weather_dict['update_time'] = Time(data['date'], precision=0).iso
        dt = Time.now() - Time(data['date'])
        weather_dict['dt'] = int(dt.to('second').value)
    except Exception:
        weather_dict['update_time'] = -999
        weather_dict['dt'] = -999

    return weather_dict


def get_rain_daemon(uri):
    """Get rain readings from the rain daemon, or a Vaisala with additional RG-11 boards."""
    with Pyro4.Proxy(uri) as proxy:
        proxy._pyroTimeout = 5
        proxy._pyroSerializer = 'serpent'
        data = proxy.last_measurement()

    weather_dict = {}

    weather_dict['update_time'] = Time(data['date'], precision=0).iso
    dt = Time.now() - Time(weather_dict['update_time'])
    weather_dict['dt'] = int(dt.to('second').value)

    if any('rg11' in key for key in data):
        # It's a Vaisala with the custom boards
        weather_dict['unsafe'] = int(data['rg11_unsafe'])
        weather_dict['total'] = int(data['rg11_total'])
    else:
        # It's the standalone rain daemon
        weather_dict['unsafe'] = int(data['unsafe_boards'])
        weather_dict['total'] = int(data['total_boards'])

    # Single good/bad flag
    if weather_dict['unsafe'] > 0:
        weather_dict['rain'] = True
    else:
        weather_dict['rain'] = False

    return weather_dict


def get_rain_domealert(uri):
    """Get rain readings from the domealert."""
    with Pyro4.Proxy(uri) as proxy:
        proxy._pyroTimeout = 5
        proxy._pyroSerializer = 'serpent'
        data = proxy.last_measurement()

    weather_dict = {}

    weather_dict['update_time'] = Time(data['date'], precision=0).iso
    dt = Time.now() - Time(weather_dict['update_time'])
    weather_dict['dt'] = int(dt.to('second').value)

    total_boards = 0
    unsafe_boards = 0
    for key in data:
        if 'rain' in key and 'valid' not in key and data[key + '_valid']:
            total_boards += 1
            unsafe = data[key] is False  # boards are NC
            unsafe_boards += int(unsafe)

    weather_dict['total'] = total_boards
    weather_dict['unsafe'] = unsafe_boards
    if total_boards == 0 or unsafe_boards > 0:
        weather_dict['rain'] = True
    else:
        weather_dict['rain'] = False

    return weather_dict


def get_cloudwatcher_daemon(uri):
    """Get sky temperature reading from the CloudWatcher daemon."""
    with Pyro4.Proxy(uri) as proxy:
        proxy._pyroTimeout = 5
        proxy._pyroSerializer = 'serpent'
        data = proxy.last_measurement()

    weather_dict = {}

    weather_dict['update_time'] = Time(data['date'], precision=0).iso
    dt = Time.now() - Time(weather_dict['update_time'])
    weather_dict['dt'] = int(dt.to('second').value)

    weather_dict['sky_temp'] = data['sky_temp']

    return weather_dict
