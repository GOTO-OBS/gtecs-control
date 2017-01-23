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
from astroplan import Observer
import configobj
import validate

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
if sys.platform == 'win32':
    CONFIG_PATH = config['WIN_CONFIG_PATH']
else:
    CONFIG_PATH = config['CONFIG_PATH']
DAEMON_PATH = pkg_resources.resource_filename('gtecs', 'daemons')
LOG_PATH = CONFIG_PATH + 'logs/'
IMAGE_PATH = CONFIG_PATH + 'images/'
QUEUE_PATH = CONFIG_PATH + 'queue/'

WIN_PATH = config['WIN_PATH']
CYGWIN_PATH = config['CYGWIN_PATH']
CYGWIN_PYTHON_PATH = config['CYGWIN_PYTHON_PATH']

# Daemons should log to file?
FILE_LOGGING = config['FILE_LOGGING']
# Daemons should to stdout?
STDOUT_LOGGING = config['STDOUT_LOGGING']
# redirect Daemon stdout to file?
REDIRECT_STDOUT = config['REDIRECT_STDOUT']

# Site location (predicted location of GOTO dome on La Palma)
SITE_LATITUDE = config['SITE_LATITUDE']
SITE_LONGITUDE = config['SITE_LONGITUDE']
SITE_ALTITUDE = config['SITE_ALTITUDE']
SITE_LOCATION = config['SITE_LOCATION']
SITE_OBSERVER = Observer.at_site(SITE_LOCATION)

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
for key in DAEMONS:
    DAEMONS[key]['HOST'] = HOST if config['DAEMONS_HOST'] == '' else config['DAEMONS_HOST']
    DAEMONS[key]['ADDRESS'] = 'PYRO:' + DAEMONS[key]['PYROID'] + '@' + DAEMONS[key]['HOST'] + ':' + str(DAEMONS[key]['PORT'])

FLI_INTERFACES = config['FLI_INTERFACES']
for key in FLI_INTERFACES:
    FLI_INTERFACES[key]['HOST'] = config['FLI_HOST_OVERRIDE'] if config['FLI_HOST_OVERRIDE'] != '' else FLI_INTERFACES[key]['HOST']
    FLI_INTERFACES[key]['ADDRESS'] = 'PYRO:' + FLI_INTERFACES[key]['PYROID'] + '@' + FLI_INTERFACES[key]['HOST'] + ':' + str(FLI_INTERFACES[key]['PORT'])

TEL_DICT = {}
for nuc in FLI_INTERFACES:
    for HW, tel in enumerate(FLI_INTERFACES[nuc]['TELS']):
        TEL_DICT[tel] = [nuc,HW]

WIN_INTERFACES = config['WIN_INTERFACES']
for key in WIN_INTERFACES:
    WIN_INTERFACES[key]['HOST'] = config['WIN_HOST_OVERRIDE'] if config['WIN_HOST_OVERRIDE'] != '' else WIN_INTERFACES[key]['HOST']
    WIN_INTERFACES[key]['ADDRESS'] = 'PYRO:' + WIN_INTERFACES[key]['PYROID'] + '@' + WIN_INTERFACES[key]['HOST'] + ':' + str(WIN_INTERFACES[key]['PORT'])

########################################################################
# Mount parameters
MIN_ELEVATION = config['MIN_ELEVATION'] #degrees
DEFAULT_OFFSET_STEP = config['DEFAULT_OFFSET_STEP'] #arcsec

# Filter wheel parameters
FILTER_LIST = config['FILTER_LIST']

# Camera parameters
FRAMETYPE_LIST = config['FRAMETYPE_LIST']
DARKFILT = config['DARKFILT'] #as an example
BIASEXP = config['BIASEXP'] #seconds, as an example

FOCUS_SLOPE_ABOVE = config['FOCUS_SLOPE_ABOVE']
FOCUS_SLOPE_BELOW = config['FOCUS_SLOPE_BELOW']
FOCUS_INTERCEPT_DIFFERENCE = config['FOCUS_INTERCEPT_DIFFERENCE']

# Queue parameters
QUEUE_PATH = CONFIG_PATH

# Power parameters
POWER_TYPE = config['POWER_TYPE']
POWER_IP = config['POWER_IP']
POWER_PORT = config['POWER_PORT']
POWER_CHECK_SCRIPT = '_power_status'
POWER_LIST = config['POWER_LIST']

# Dome parameters
DOME_LOCATION = '/dev/serial/by-id/usb-FTDI_UC232R_FTWDFJ4H-if00-port0'
FAKE_DOME = config['FAKE_DOME']
BIG_RED_BUTTON_PORT = config['BIG_RED_BUTTON_PORT']
EMERGENCY_FILE = CONFIG_PATH + 'EMERGENCY-SHUTDOWN'

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
