#!/usr/bin/env python
"""G-TeCS core control system parameters."""

import os
import sys

import configobj

import pkg_resources

import validate

from .version import __version__


# Load configspec file for default configuration
if os.path.exists('gtecs/data/configspec.ini'):
    # We are running in install dir, during installation
    CONFIGSPEC_FILE = 'gtecs/data/configspec.ini'
else:
    # We are being imported, find pkg_resources
    CONFIGSPEC_FILE = pkg_resources.resource_filename('gtecs', 'data/configspec.ini')

# Try to find .gtecs.conf file, look in the home directory and
# anywhere specified by GTECS_CONF environment variable
paths = [os.path.expanduser("~")]
if "GTECS_CONF" in os.environ:
    GTECS_CONF_PATH = os.environ["GTECS_CONF"]
    paths.append(GTECS_CONF_PATH)
else:
    GTECS_CONF_PATH = None

# Load the .gtecs.conf file as a ConfigObj
config = configobj.ConfigObj({}, configspec=CONFIGSPEC_FILE)
CONFIG_FILE_PATH = None
for loc in paths:
    try:
        with open(os.path.join(loc, ".gtecs.conf")) as source:
            config = configobj.ConfigObj(source, configspec=CONFIGSPEC_FILE)
            CONFIG_FILE_PATH = loc
    except IOError:
        pass

# Validate ConfigObj, filling defaults from configspec if missing from config file
validator = validate.Validator()
result = config.validate(validator)
if result is not True:
    print('Config file validation failed')
    print([k for k in result if not result[k]])
    sys.exit(1)

############################################################
# Module parameters
VERSION = __version__

# File locations
FILE_PATH = config['FILE_PATH']
if FILE_PATH in ['path_not_set', '/path/goes/here/']:
    if config['CONFIG_PATH'] != 'path_not_set':
        # backwads compatability with old name
        FILE_PATH = config['CONFIG_PATH']
    else:
        raise ValueError('G-TeCS FILE_PATH not set, check your .gtecs.conf file')

if config['IMAGE_PATH'] != 'path_not_set':
    IMAGE_PATH = config['IMAGE_PATH']
else:
    IMAGE_PATH = os.path.join(FILE_PATH, 'images')
LOG_PATH = os.path.join(FILE_PATH, 'logs')
QUEUE_PATH = os.path.join(FILE_PATH, 'queue')
PID_PATH = os.path.join(FILE_PATH, '.pid')
DAEMON_PATH = pkg_resources.resource_filename('gtecs', 'daemons')

# General parameters
LOCAL_HOST = config['LOCAL_HOST']

# Common file strings
ORG_NAME = config['ORG_NAME']
TELESCOPE_NAME = config['TELESCOPE_NAME']
ROBOTIC_OBSERVER = config['ROBOTIC_OBSERVER']

# Site location (predicted location of GOTO dome on La Palma)
SITE_LATITUDE = config['SITE_LATITUDE']
SITE_LONGITUDE = config['SITE_LONGITUDE']
SITE_ALTITUDE = config['SITE_ALTITUDE']
SITE_LOCATION = config['SITE_LOCATION']

# use colour and fancy formatting in output?
FANCY_OUTPUT = config['FANCY_OUTPUT']

# Email alerts
EMAIL_LIST = config['EMAIL_LIST']
EMAIL_ADDRESS = config['EMAIL_ADDRESS']
EMAIL_SERVER = config['EMAIL_SERVER']

############################################################
# Daemon parameters
PYRO_TIMEOUT = config['PYRO_TIMEOUT']
DAEMON_CHECK_PERIOD = config['DAEMON_CHECK_PERIOD']
DAEMON_SLEEP_TIME = config['DAEMON_SLEEP_TIME']

FILE_LOGGING = config['FILE_LOGGING']
STDOUT_LOGGING = config['STDOUT_LOGGING']
REDIRECT_STDOUT = config['REDIRECT_STDOUT']

DAEMONS = config['DAEMONS']
for daemon_id in DAEMONS:
    if DAEMONS[daemon_id]['HOST'] == 'localhost':
        DAEMONS[daemon_id]['HOST'] = LOCAL_HOST

INTERFACES = config['INTERFACES']
UT_INTERFACES = {}
for interface_id in INTERFACES:
    INTERFACES[interface_id]['UTS'] = sorted(int(ut) for ut in INTERFACES[interface_id]['UTS'])
    for ut in INTERFACES[interface_id]['UTS']:
        UT_INTERFACES[ut] = interface_id
ALL_UTS = sorted(UT_INTERFACES)
UTS_WITH_CAMERAS = sorted(ut for intf in INTERFACES
                          if 'CAMERAS' in INTERFACES[intf]
                          for ut in INTERFACES[intf]['UTS'])
UTS_WITH_FOCUSERS = sorted(ut for intf in INTERFACES
                           if 'FOCUSERS' in INTERFACES[intf]
                           for ut in INTERFACES[intf]['UTS'])
UTS_WITH_FILTERWHEELS = sorted(ut for intf in INTERFACES
                               if 'FILTERWHEELS' in INTERFACES[intf]
                               for ut in INTERFACES[intf]['UTS'])

############################################################
# Conditions parameters
MAX_CONDITIONS_AGE = config['MAX_CONDITIONS_AGE']
CURL_WAIT_TIME = config['CURL_WAIT_TIME']

USE_ING_WEATHER = config['USE_ING_WEATHER']
USE_W1M_RAINBOARDS = config['USE_W1M_RAINBOARDS']

WEATHER_TIMEOUT = config['WEATHER_TIMEOUT']
WEATHER_STATIC = config['WEATHER_STATIC']
WEATHER_INTERVAL = config['WEATHER_INTERVAL']

# Rain
RAIN_BADDELAY = config['RAIN_BADDELAY']
RAIN_GOODDELAY = config['RAIN_GOODDELAY']

# Humidity - measured in %
MAX_HUMIDITY = config['MAX_HUMIDITY']
MAX_INTERNAL_HUMIDITY = config['MAX_INTERNAL_HUMIDITY']
CRITICAL_INTERNAL_HUMIDITY = config['CRITICAL_INTERNAL_HUMIDITY']
HUMIDITY_BADDELAY = config['HUMIDITY_BADDELAY']
HUMIDITY_GOODDELAY = config['HUMIDITY_GOODDELAY']

# Windspeed - measured in km/h
MAX_WINDSPEED = config['MAX_WINDSPEED']
WINDSPEED_BADDELAY = config['WINDSPEED_BADDELAY']
WINDSPEED_GOODDELAY = config['WINDSPEED_GOODDELAY']

# Windgust - measured in km/h
WINDGUST_PERIOD = config['WINDGUST_PERIOD']
MAX_WINDGUST = config['MAX_WINDGUST']
WINDGUST_BADDELAY = config['WINDGUST_BADDELAY']
WINDGUST_GOODDELAY = config['WINDGUST_GOODDELAY']

# Dew point - measured in Celsius above ambient temperature
MIN_DEWPOINT = config['MIN_DEWPOINT']
DEWPOINT_BADDELAY = config['DEWPOINT_BADDELAY']
DEWPOINT_GOODDELAY = config['DEWPOINT_GOODDELAY']

# Temperature - measured in Celsius
MAX_TEMPERATURE = config['MAX_TEMPERATURE']
MIN_TEMPERATURE = config['MIN_TEMPERATURE']
MIN_INTERNAL_TEMPERATURE = config['MIN_INTERNAL_TEMPERATURE']
MAX_INTERNAL_TEMPERATURE = config['MAX_INTERNAL_TEMPERATURE']
CRITICAL_INTERNAL_TEMPERATURE = config['CRITICAL_INTERNAL_TEMPERATURE']
TEMPERATURE_BADDELAY = config['TEMPERATURE_BADDELAY']
TEMPERATURE_GOODDELAY = config['TEMPERATURE_GOODDELAY']
ICE_BADDELAY = config['ICE_BADDELAY']
ICE_GOODDELAY = config['ICE_GOODDELAY']

# Internal
INTERNAL_BADDELAY = config['INTERNAL_BADDELAY']
INTERNAL_GOODDELAY = config['INTERNAL_GOODDELAY']

# Dark - sunalt measured in degrees
SUN_ELEVATION_LIMIT = config['SUN_ELEVATION_LIMIT']

# UPS battery - measured in %
MIN_UPSBATTERY = config['MIN_UPSBATTERY']
UPS_BADDELAY = config['UPS_BADDELAY']
UPS_GOODDELAY = config['UPS_GOODDELAY']

# Link - time measured in seconds
LINK_URLS = config['LINK_URLS']
LINK_BADDELAY = config['LINK_BADDELAY']
LINK_GOODDELAY = config['LINK_GOODDELAY']

# Hatch
IGNORE_HATCH = config['IGNORE_HATCH']
HATCH_BADDELAY = config['HATCH_BADDELAY']
HATCH_GOODDELAY = config['HATCH_GOODDELAY']

# Diskspace - free space measured in %
MIN_DISKSPACE = config['MIN_DISKSPACE']

# Satellite clouds - opacity measured in %
MAX_SATCLOUDS = config['MAX_SATCLOUDS']
SATCLOUDS_BADDELAY = config['SATCLOUDS_BADDELAY']
SATCLOUDS_GOODDELAY = config['SATCLOUDS_GOODDELAY']

# Seeing
SEEING_TIMEOUT = config['SEEING_TIMEOUT']

############################################################
# Sentinel parameters
LOCAL_IVO = config['LOCAL_IVO']
VOSERVER_HOST = config['VOSERVER_HOST']
VOSERVER_PORT = config['VOSERVER_PORT']
SENTINEL_SEND_MESSAGES = config['SENTINEL_SEND_MESSAGES']

############################################################
# Mount parameters
MIN_ELEVATION = config['MIN_ELEVATION']
DEFAULT_OFFSET_STEP = config['DEFAULT_OFFSET_STEP']
SITECH_HOST = config['SITECH_HOST']
SITECH_PORT = config['SITECH_PORT']
FAKE_MOUNT = config['FAKE_MOUNT']

############################################################
# FLI parameters
FAKE_FLI = config['FAKE_FLI']

############################################################
# Filter wheel parameters
FILTER_LIST = config['FILTER_LIST']

############################################################
# Focuser parameters
RASA_PORT = config['RASA_PORT']

############################################################
# Camera parameters
FRAMETYPE_LIST = config['FRAMETYPE_LIST']
CCD_TEMP = config['CCD_TEMP']

############################################################
# Exposure Queue parameters
QUEUE_PATH = FILE_PATH

############################################################
# Power parameters
POWER_CHECK_PERIOD = config['POWER_CHECK_PERIOD']
POWER_CHECK_SCRIPT = '_power_status'
POWER_UNITS = config['POWER_UNITS']
POWER_GROUPS = config['POWER_GROUPS']

############################################################
# Dome parameters
DOME_CHECK_PERIOD = config['DOME_CHECK_PERIOD']
DOME_LOCATION = config['DOME_LOCATION']
ARDUINO_LOCATION = config['ARDUINO_LOCATION']
DOME_HEARTBEAT_LOCATION = config['DOME_HEARTBEAT_LOCATION']
DOME_HEARTBEAT_PERIOD = config['DOME_HEARTBEAT_PERIOD']
FAKE_DOME = config['FAKE_DOME']
QUICK_CLOSE_BUTTON = config['QUICK_CLOSE_BUTTON']
QUICK_CLOSE_BUTTON_PORT = config['QUICK_CLOSE_BUTTON_PORT']
EMERGENCY_FILE = os.path.join(FILE_PATH, 'EMERGENCY-SHUTDOWN')
DOME_ALARM_DURATION = config['DOME_ALARM_DURATION']
DEHUMIDIFIER_IP = config['DEHUMIDIFIER_IP']
DEHUMIDIFIER_PORT = config['DEHUMIDIFIER_PORT']

############################################################
# Observing parameters
MOONELEV_LIMIT = config['MOONELEV_LIMIT']

############################################################
# Obs script parameters
AUTOFOCUS_NEARFOCUSVALUE = config['AUTOFOCUS_NEARFOCUSVALUE']
AUTOFOCUS_BIGSTEP = config['AUTOFOCUS_BIGSTEP']
AUTOFOCUS_SMALLSTEP = config['AUTOFOCUS_SMALLSTEP']
AUTOFOCUS_EXPTIME = config['AUTOFOCUS_EXPTIME']
AUTOFOCUS_FILTER = config['AUTOFOCUS_FILTER']

# cant add these to validation without adding unwanted defaults
# enforce type here instead.
if 'FOCUS_SLOPE_ABOVE' in config:
    FOCUS_SLOPE_ABOVE = {int(key): float(config['FOCUS_SLOPE_ABOVE'][key])
                         for key in config['FOCUS_SLOPE_ABOVE']}
else:
    FOCUS_SLOPE_ABOVE = {key: 12.0 for key in UTS_WITH_FOCUSERS}

if 'FOCUS_SLOPE_BELOW' in config:
    FOCUS_SLOPE_BELOW = {int(key): float(config['FOCUS_SLOPE_BELOW'][key])
                         for key in config['FOCUS_SLOPE_BELOW']}
else:
    FOCUS_SLOPE_BELOW = {key: -12.0 for key in UTS_WITH_FOCUSERS}

if 'FOCUS_INTERCEPT_DIFFERENCE' in config:
    FOCUS_INTERCEPT_DIFFERENCE = {int(key): float(config['FOCUS_INTERCEPT_DIFFERENCE'][key])
                                  for key in config['FOCUS_INTERCEPT_DIFFERENCE']}
else:
    FOCUS_INTERCEPT_DIFFERENCE = {key: 0.1 for key in UTS_WITH_FOCUSERS}


FLATS_SKYMEANTARGET = config['FLATS_SKYMEANTARGET']
FLATS_NUM = config['FLATS_NUM']
FLATS_MAXEXPTIME = config['FLATS_MAXEXPTIME']
FLATS_STEPSIZE = config['FLATS_STEPSIZE']

FOCUSRUN_EXPTIME = config['FOCUSRUN_EXPTIME']
FOCUSRUN_FILTER = config['FOCUSRUN_FILTER']
FOCUSRUN_DELTAS = config['FOCUSRUN_DELTAS']

IERS_A_URL = config['IERS_A_URL']
IERS_A_URL_BACKUP = config['IERS_A_URL_BACKUP']

############################################################
# Pilot parameters
NUM_DARKS = config['NUM_DARKS']

############################################################
# Slack bot parameters
ENABLE_SLACK = config['ENABLE_SLACK']
SLACK_BOT_NAME = config['SLACK_BOT_NAME']
SLACK_BOT_TOKEN = config['SLACK_BOT_TOKEN']
SLACK_BOT_CHANNEL = config['SLACK_BOT_CHANNEL']
