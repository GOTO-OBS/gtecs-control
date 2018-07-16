"""
G-TeCS core control system parameters
"""

import os
import sys
import socket
import Pyro4
import pkg_resources
import configobj
import validate

import numpy

from . import __version__


# Module parameters
GTECS_VERSION = __version__

# get a default spec for config file, either from local path, or installed path
if os.path.exists('gtecs/data/configspec.ini'):
    # we are running in install dir, during installation
    configspec_file = 'gtecs/data/configspec.ini'
else:
    # we are being imported, find pkg_resources
    configspec_file = pkg_resources.resource_filename('gtecs', 'data/configspec.ini')

# try and load config file
# look in current dir, home directory and anywhere specified by GTECS_CONF environment variable
paths = [os.curdir, os.path.expanduser("~")]
if "GTECS_CONF" in os.environ:
    paths.append(os.environ["GTECS_CONF"])

# now load config file
config = configobj.ConfigObj({}, configspec=configspec_file)
for loc in paths:
    try:
        with open(os.path.join(loc, ".gtecs.conf")) as source:
            config = configobj.ConfigObj(source, configspec=configspec_file)
    except IOError as e:
        pass

# validate ConfigObj, filling defaults from configspec if missing from config file
validator = validate.Validator()
result = config.validate(validator)
if result != True:
    print(result)
    print('Config file validation failed')
    sys.exit(1)

############################################################
# General parameters
LOCAL_HOST = config['LOCAL_HOST']
LOCAL_HOSTNAME = socket.gethostname()
# Common file strings
ORIGIN = config['ORIGIN']
TELESCOP = config['TELESCOP'] # "the telescope used", will be appended with details (e.g. [GOTO_N]-ut2"
ROBOTIC_OBSERVER = config['ROBOTIC_OBSERVER']

# File locations (need to alter depending on system)
CONFIG_PATH = config['CONFIG_PATH']
DAEMON_PATH = pkg_resources.resource_filename('gtecs', 'daemons')
LOG_PATH = CONFIG_PATH + 'logs/'
QUEUE_PATH = CONFIG_PATH + 'queue/'
IMAGE_PATH = config['IMAGE_PATH']

# Site location (predicted location of GOTO dome on La Palma)
SITE_LATITUDE = config['SITE_LATITUDE']
SITE_LONGITUDE = config['SITE_LONGITUDE']
SITE_ALTITUDE = config['SITE_ALTITUDE']
SITE_LOCATION = config['SITE_LOCATION']

# use colour and fancy formatting in output?
FANCY_OUTPUT = config['FANCY_OUTPUT']

# Email alerts
EMAIL_LIST = config['EMAIL_LIST']
EMAIL_ADDRESS = config['EMAIL_ADDRESS'] # An example
EMAIL_SERVER = config['EMAIL_SERVER']

############################################################
# Daemon parameters
Pyro4.config.SERIALIZER = 'pickle' # IMPORTANT - Can seralize numpy arrays for images
Pyro4.config.SERIALIZERS_ACCEPTED.add('pickle')
Pyro4.config.REQUIRE_EXPOSE = False

PYRO_TIMEOUT = config['PYRO_TIMEOUT']
DAEMON_SLEEP_TIME = config['DAEMON_SLEEP_TIME']

FILE_LOGGING = config['FILE_LOGGING']
STDOUT_LOGGING = config['STDOUT_LOGGING']
REDIRECT_STDOUT = config['REDIRECT_STDOUT']

USE_FAKE_FLI = config['USE_FAKE_FLI']

DAEMONS = config['DAEMONS']
for daemon_ID in DAEMONS:
    if  DAEMONS[daemon_ID]['HOST'] == 'localhost':
        DAEMONS[daemon_ID]['HOST'] = LOCAL_HOST
    DAEMONS[daemon_ID]['ADDRESS'] = 'PYRO:' + daemon_ID + '@' + DAEMONS[daemon_ID]['HOST'] + ':' + str(DAEMONS[daemon_ID]['PORT'])
    if 'fli' in DAEMONS[daemon_ID]['DEPENDS']:
        DAEMONS[daemon_ID]['DEPENDS'].remove('fli')
        DAEMONS[daemon_ID]['DEPENDS'].extend([i for i in config['FLI_INTERFACES']])

FLI_INTERFACES = config['FLI_INTERFACES']

TEL_DICT = {}
for intf in FLI_INTERFACES:
    for HW, tel in enumerate(FLI_INTERFACES[intf]['TELS']):
        TEL_DICT[tel] = [intf, HW]

############################################################
# Conditions parameters
MAX_CONDITIONS_AGE = config['MAX_CONDITIONS_AGE']
CURL_WAIT_TIME = config['CURL_WAIT_TIME']

USE_ING_WEATHER = config['USE_ING_WEATHER']

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
CRITICAL_UPSBATTERY = config['CRITICAL_UPSBATTERY']
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

# Diskspace - free space in %
MIN_DISKSPACE = config['MIN_DISKSPACE']

############################################################
# Mount parameters
MIN_ELEVATION = config['MIN_ELEVATION'] #degrees
DEFAULT_OFFSET_STEP = config['DEFAULT_OFFSET_STEP'] #arcsec
SITECH_HOST = config['SITECH_HOST']
SITECH_PORT = config['SITECH_PORT']
FREEZE_DEC = config['FREEZE_DEC']
FAKE_MOUNT = config['FAKE_MOUNT']

############################################################
# Filter wheel parameters
FILTER_LIST = config['FILTER_LIST']

############################################################
# Camera parameters
FRAMETYPE_LIST = config['FRAMETYPE_LIST']
CCD_TEMP = config['CCD_TEMP']

# cant add these to validation without adding unwanted defaults
# enforce type here instead.
if 'FOCUS_SLOPE_ABOVE' in config:
    FOCUS_SLOPE_ABOVE = {int(key): float(config['FOCUS_SLOPE_ABOVE'][key])
                         for key in config['FOCUS_SLOPE_ABOVE']}
else:
    FOCUS_SLOPE_ABOVE = {key: 12.0 for key in TEL_DICT}

if 'FOCUS_SLOPE_BELOW' in config:
    FOCUS_SLOPE_BELOW = {int(key): float(config['FOCUS_SLOPE_BELOW'][key])
                         for key in config['FOCUS_SLOPE_BELOW']}
else:
    FOCUS_SLOPE_BELOW = {key: -12.0 for key in TEL_DICT}

if 'FOCUS_INTERCEPT_DIFFERENCE' in config:
    FOCUS_INTERCEPT_DIFFERENCE = {int(key): float(config['FOCUS_INTERCEPT_DIFFERENCE'][key])
                                  for key in config['FOCUS_INTERCEPT_DIFFERENCE']}
else:
    FOCUS_INTERCEPT_DIFFERENCE = {key: 0.1 for key in TEL_DICT}

############################################################
# Exposure Queue parameters
QUEUE_PATH = CONFIG_PATH

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
DOME_HEARTBEAT_ENABLED = config['DOME_HEARTBEAT_ENABLED']
DOME_HEARTBEAT_LOCATION = config['DOME_HEARTBEAT_LOCATION']
DOME_HEARTBEAT_PERIOD = config['DOME_HEARTBEAT_PERIOD']
FAKE_DOME = config['FAKE_DOME']
QUICK_CLOSE_BUTTON = config['QUICK_CLOSE_BUTTON']
QUICK_CLOSE_BUTTON_PORT = config['QUICK_CLOSE_BUTTON_PORT']
EMERGENCY_FILE = CONFIG_PATH + 'EMERGENCY-SHUTDOWN'
SILENCE_ALARM_IN_MANUAL_MODE = config['SILENCE_ALARM_IN_MANUAL_MODE']
DEHUMIDIFIER_IP = config['DEHUMIDIFIER_IP']
DEHUMIDIFIER_PORT = config['DEHUMIDIFIER_PORT']

############################################################
# Observing parameters
MOONELEV_LIMIT = config['MOONELEV_LIMIT']

############################################################
# Pilot parameters
NUM_DARKS = config['NUM_DARKS']

############################################################
# Slack bot parameters
ENABLE_SLACK = config['ENABLE_SLACK']
SLACK_BOT_NAME = config['SLACK_BOT_NAME']
SLACK_BOT_TOKEN = config['SLACK_BOT_TOKEN']
SLACK_BOT_CHANNEL = config['SLACK_BOT_CHANNEL']
