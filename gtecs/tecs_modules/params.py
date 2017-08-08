#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                               params.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#       G-TeCS module containing core controll system parameters       #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
import os,sys
import socket
import numpy
import Pyro4
import pkg_resources
import configobj
import validate


########################################################################
# Module parameters
GTECS_VERSION = '0.1.0'

########################################################################

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
    print('Config file validation failed')
    sys.exit(1)

########################################################################
# General parameters

# Common file strings
ORIGIN = config['ORIGIN']
TELESCOP = config['TELESCOP'] # "the telescope used", will be appended with details (e.g. [GOTO_N]-ut2"

# File locations (need to alter depending on system)
HOST = socket.gethostname()

CONFIG_PATH = config['CONFIG_PATH']
DAEMON_PATH = pkg_resources.resource_filename('gtecs', 'daemons')
LOG_PATH = CONFIG_PATH + 'logs/'
QUEUE_PATH = CONFIG_PATH + 'queue/'

IMAGE_PATH = config['IMAGE_PATH']

# Daemons should log to file?
FILE_LOGGING = config['FILE_LOGGING']
# Daemons should to stdout?
STDOUT_LOGGING = config['STDOUT_LOGGING']
# redirect Daemon stdout to file?
REDIRECT_STDOUT = config['REDIRECT_STDOUT']
# use colour and fancy formatting in output?
FANCY_OUTPUT = config['FANCY_OUTPUT']

# Site location (predicted location of GOTO dome on La Palma)
SITE_LATITUDE = config['SITE_LATITUDE']
SITE_LONGITUDE = config['SITE_LONGITUDE']
SITE_ALTITUDE = config['SITE_ALTITUDE']
SITE_LOCATION = config['SITE_LOCATION']

# Conditions checks
MAX_CONDITIONS_AGE = config['MAX_CONDITIONS_AGE']

# Pyro connection
PROXY_TIMEOUT = config['PROXY_TIMEOUT']
Pyro4.config.SERIALIZER = 'pickle' # IMPORTANT - Can seralize numpy arrays for images
Pyro4.config.SERIALIZERS_ACCEPTED.add('pickle')
Pyro4.config.REQUIRE_EXPOSE = False

# Email alerts
EMAIL_LIST = config['EMAIL_LIST']
EMAIL_ADDRESS = config['EMAIL_ADDRESS'] # An example
EMAIL_SERVER = config['EMAIL_SERVER']

########################################################################
# Daemon parameters
DAEMONS = config['DAEMONS']
for daemon_ID in DAEMONS:
    if  DAEMONS[daemon_ID]['HOST'] == 'localhost':
        DAEMONS[daemon_ID]['HOST'] = HOST
    DAEMONS[daemon_ID]['ADDRESS'] = 'PYRO:' + daemon_ID + '@' + DAEMONS[daemon_ID]['HOST'] + ':' + str(DAEMONS[daemon_ID]['PORT'])
    if 'fli' in DAEMONS[daemon_ID]['DEPENDS']:
        DAEMONS[daemon_ID]['DEPENDS'].remove('fli')
        DAEMONS[daemon_ID]['DEPENDS'].extend([i for i in config['FLI_INTERFACES']])

USE_FAKE_FLI = config['USE_FAKE_FLI']
FLI_INTERFACES = config['FLI_INTERFACES']

TEL_DICT = {}
for intf in FLI_INTERFACES:
    for HW, tel in enumerate(FLI_INTERFACES[intf]['TELS']):
        TEL_DICT[tel] = [intf, HW]

########################################################################
# Weather parameters

# We are going to temporarily rely on the ING weather systems until the
# GOTO Vaisala is up and running.
WEATHER_SOURCE = config['WEATHER_SOURCE']       # select weather source: html = external ING weather html web page
                               # (JKT mast); wht = internal ING xml weather file (WHT mast); int =
                               # internal ING xml weather file (INT mast); jkt = internal ING xml
                               # weather file (JKT mast)
BACKUP_WEATHER_SOURCE = config['BACKUP_WEATHER_SOURCE']  # select backup weather source: html = external ING weather html web
                               # page (JKT mast); wht = internal ING xml weather file (WHT mast); int
                               # = internal ING xml weather file (INT mast); jkt = internal ING xml
                               # weather file (JKT mast)
# Shutdown criteria
MAX_HUMIDITY = config['MAX_HUMIDITY']          # relative humidity limit in per cent
MAX_LOCAL_HUMIDITY = config['MAX_LOCAL_HUMIDITY']    # relative humidity limit in per cent, as measured by local sensor
MAX_WINDSPEED = config['MAX_WINDSPEED']         # wind speed limit in m/s
MAX_TEMPERATURE = config['MAX_TEMPERATURE']     # max temperature limit in Celsius
MIN_TEMPERATURE = config['MIN_TEMPERATURE']       # min temperature limit in Celsius
WEATHER_TIMEOUT = config['WEATHER_TIMEOUT']     # weather data age limit in seconds
WEATHER_STATIC = config['WEATHER_STATIC']     # max time that weather parameters can remain unchanged in seconds
WEATHER_INTERVAL = config['WEATHER_INTERVAL']     # weather monitoring interval in seconds

SUN_ELEVATION_LIMIT = config['SUN_ELEVATION_LIMIT']  # maximum altitude limit of the Sun in degrees

WARWICK_CLOSED = config['WARWICK_CLOSED']       # max time in secs that can elapse without contact with Warwick server when dome closed
WARWICK_OPEN = config['WARWICK_OPEN']        # max time in secs that can elapse without contact with Warwick server when dome open


########################################################################
# Mount parameters
MIN_ELEVATION = config['MIN_ELEVATION'] #degrees
DEFAULT_OFFSET_STEP = config['DEFAULT_OFFSET_STEP'] #arcsec
SITECH_HOST = config['SITECH_HOST']
SITECH_PORT = config['SITECH_PORT']
FREEZE_DEC = config['FREEZE_DEC']

# Filter wheel parameters
FILTER_LIST = config['FILTER_LIST']

# Camera parameters
FRAMETYPE_LIST = config['FRAMETYPE_LIST']
DARKFILT = config['DARKFILT'] #as an example
BIASEXP = config['BIASEXP'] #seconds, as an example
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

# Queue parameters
QUEUE_PATH = CONFIG_PATH

# Power parameters
POWER_CHECK_PERIOD = config['POWER_CHECK_PERIOD']
POWER_CHECK_SCRIPT = '_power_status'
POWER_UNITS = config['POWER_UNITS']

# Dome parameters
DOME_CHECK_PERIOD = config['DOME_CHECK_PERIOD']
DOME_LOCATION = config['DOME_LOCATION']
ARDUINO_LOCATION = config['ARDUINO_LOCATION']
FAKE_DOME = config['FAKE_DOME']
QUICK_CLOSE_BUTTON = config['QUICK_CLOSE_BUTTON']
QUICK_CLOSE_BUTTON_PORT = config['QUICK_CLOSE_BUTTON_PORT']
EMERGENCY_FILE = CONFIG_PATH + 'EMERGENCY-SHUTDOWN'
SILENCE_ALARM_IN_MANUAL_MODE = config['SILENCE_ALARM_IN_MANUAL_MODE']

# Observing parameters
MOONDIST_LIMIT = config['MOONDIST_LIMIT']
MOONELEV_LIMIT = config['MOONELEV_LIMIT']

# Database parameters
DATABASE_USER = config['DATABASE_USER']
DATABASE_PASSWORD = config['DATABASE_PASSWORD']
DATABASE_HOST = config['DATABASE_HOST']
DATABASE_NAME = config['DATABASE_NAME']
DATABASE_LOCATION = DATABASE_USER + ':' + DATABASE_PASSWORD + '@' + DATABASE_HOST + '/' + DATABASE_NAME
DATABASE_ECHO = config['DATABASE_ECHO']

# slack bot params - optional
try:
    SLACK_BOT_NAME = config['SLACK_BOT_NAME']
    SLACK_BOT_TOKEN = config['SLACK_BOT_TOKEN']
    SLACK_BOT_CHANNEL = config['SLACK_BOT_CHANNEL']
except:
    pass
