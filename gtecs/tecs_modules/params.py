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

# TeCS modules
from gtecs.controls import power_control
from gtecs.controls import dome_control

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
TECS_PATH = config['CONFIG_PATH']
CONFIG_PATH = config['CONFIG_PATH']
DAEMON_PATH = pkg_resources.resource_filename('gtecs', 'daemons')
LOG_PATH = TECS_PATH + 'logs/'
IMAGE_PATH = TECS_PATH + 'images/'

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
    FLI_INTERFACES[key]['HOST'] = HOST if config['FLI_INTERFACE_HOST'] == '' else config['FLI_INTERFACE_HOST']

TEL_DICT = {}
for nuc in FLI_INTERFACES:
    FLI_INTERFACES[nuc]['ADDRESS'] = 'PYRO:' + FLI_INTERFACES[nuc]['PYROID'] + '@' + FLI_INTERFACES[nuc]['HOST'] + ':' + str(FLI_INTERFACES[nuc]['PORT'])
    for HW, tel in enumerate(FLI_INTERFACES[nuc]['TELS']):
        TEL_DICT[tel] = [nuc,HW]

########################################################################
# Mount parameters
WIN_HOST = config['WIN_HOST']

SITECH_PROCESS = config['SITECH_PROCESS']
SITECH_PYROID = config['SITECH_PYROID']
SITECH_PORT = config['SITECH_PORT']
SITECH_ADDRESS = 'PYRO:' + SITECH_PYROID + '@' + WIN_HOST + ':' + str(SITECH_PORT)

WIN_PATH = config['WIN_PATH']
CYGWIN_PATH = config['CYGWIN_PATH']
CYGWIN_PYTHON_PATH = config['CYGWIN_PYTHON_PATH']

MIN_ELEVATION = config['MIN_ELEVATION'] #degrees
DEFAULT_OFFSET_STEP = config['DEFAULT_OFFSET_STEP'] #arcsec

# Filter wheel parameters
FILTER_LIST = config['FILTER_LIST']

# Camera parameters
FRAMETYPE_LIST = config['FRAMETYPE_LIST']
DARKFILT = config['DARKFILT'] #as an example
BIASEXP = config['BIASEXP'] #seconds, as an example

# Queue parameters
QUEUE_PATH = TECS_PATH

# Power parameters
if config['POWER_TYPE'] == 'APCPower':
    POWER = power_control.APCPower(config['POWER_IP'])
elif config['POWER_TYPE'] == 'EruPower':
    POWER = power_control.EthPower(config['POWER_IP'], config['POWER_PORT'])
else:
    POWER = power_control.FakePower(' ',' ')
POWER_CHECK_SCRIPT = '_power_status'
POWER_LIST = config['POWER_LIST']

# Dome parameters
DOME_LOCATION = '/dev/serial/by-id/usb-FTDI_UC232R_FTWDFJ4H-if00-port0'
if config['FAKE_DOME'] == 1:
    DOME = dome_control.FakeDome('')
else:
    DOME = AstroHavenDome(DOME_LOCATION)
BIG_RED_BUTTON_PORT = config['BIG_RED_BUTTON_PORT']
EMERGENCY_FILE = CONFIG_PATH + 'EMERGENCY-SHUTDOWN'
