"""Package parameters."""

import importlib.resources as pkg_resources
import os
import sys

from gtecs.common import config as pkg_config
from gtecs.common.package import get_package_version, load_config
from gtecs.common.system import get_local_ip


############################################################
# Load and validate config file
config, CONFIG_SPEC, CONFIG_FILE = load_config('control', ['.gtecs.conf', '.control.conf'])

############################################################
# Module parameters
VERSION = get_package_version('control')
PYTHON_EXE = sys.executable.split('/')[-1]
if PYTHON_EXE == 'python':
    # Now that python3 is the default, we might need to add the number
    PYTHON_EXE += f'{sys.version_info.major}.{sys.version_info.minor}'
if '.' not in PYTHON_EXE:
    # Needed for remote machines, which might have different versions as default
    # (e.g. python3 might be python3.6, but we want to force python3.8)
    PYTHON_EXE += f'.{sys.version_info.minor}'

# File locations
FILE_PATH = pkg_config.CONFIG_PATH / 'control'
IMAGE_PATH = config['IMAGE_PATH']
if config['IMAGE_PATH'] in ['path_not_set', '/path/goes/here/']:
    IMAGE_PATH = os.path.join(FILE_PATH, 'images')

# General parameters
LOCAL_HOST = get_local_ip()

# Common file strings
ORG_NAME = config['ORG_NAME']
TELESCOPE_NAME = config['TELESCOPE_NAME']
TELESCOPE_NUMBER = config['TELESCOPE_NUMBER']
ROBOTIC_OBSERVER = config['ROBOTIC_OBSERVER']

# Site location
VALID_SITES = ['La Palma', 'Siding Spring']
SITE_NAME = config['SITE_NAME']
if SITE_NAME not in VALID_SITES:
    raise ValueError('Unrecognised site {} (must be in {})'.format(SITE_NAME, VALID_SITES))
SITE_LATITUDE = config['SITE_LATITUDE']
SITE_LONGITUDE = config['SITE_LONGITUDE']
SITE_ALTITUDE = config['SITE_ALTITUDE']

# People to email in emergency
EMAIL_LIST = config['EMAIL_LIST']

# Email account to send emails from
EMAIL_ADDRESS = config['EMAIL_ADDRESS']
EMAIL_SERVER = config['EMAIL_SERVER']

# Use colour and fancy formatting in output?
FANCY_OUTPUT = config['FANCY_OUTPUT']

# Debug - print commands?
COMMAND_DEBUG = config['COMMAND_DEBUG']

############################################################
# Daemon parameters
PYRO_TIMEOUT = config['PYRO_TIMEOUT']
PYRO_LOGFILE = config['PYRO_LOGFILE']
DAEMON_CHECK_PERIOD = config['DAEMON_CHECK_PERIOD']
DAEMON_SLEEP_TIME = config['DAEMON_SLEEP_TIME']

DAEMONS = config['DAEMONS']
for daemon_id in DAEMONS:
    if DAEMONS[daemon_id]['HOST'] == 'localhost':
        DAEMONS[daemon_id]['HOST'] = LOCAL_HOST
    with pkg_resources.path('gtecs.control._daemon_scripts', DAEMONS[daemon_id]['PROCESS']) as path:
        DAEMONS[daemon_id]['PROCESS_PATH'] = str(path)

UT_DICT = config['UTS']
# UT IDs should be integers
UT_DICT = {int(ut): d for ut, d in UT_DICT.items()}
for ut in UT_DICT:
    # Add UT to interface list
    if 'INTERFACE' in UT_DICT[ut]:
        interface_id = UT_DICT[ut]['INTERFACE']
        if interface_id in DAEMONS:
            if 'UTS' in DAEMONS[interface_id]:
                # Add and sort
                DAEMONS[interface_id]['UTS'].append(ut)
                DAEMONS[interface_id]['UTS'] = sorted(DAEMONS[interface_id]['UTS'])
            else:
                # Just add
                DAEMONS[interface_id]['UTS'] = [ut]
        else:
            raise ValueError('Can not find interface "{}" for UT{}'.format(interface_id, ut))
    else:
        raise ValueError('No interface defined for UT{}'.format(ut))

    # Check hardware dicts
    if 'OTA' not in UT_DICT[ut]:
        # All UTs should have an OTA, by definition...
        raise ValueError('UT {} does not have an OTA defined'.format(ut))
    if 'MIRROR_COVER' not in UT_DICT[ut]['OTA']:
        UT_DICT[ut]['OTA']['MIRROR_COVER'] = False
    else:
        UT_DICT[ut]['OTA']['MIRROR_COVER'] = UT_DICT[ut]['OTA']['MIRROR_COVER'] != '0'
    for hw_class in ['CAMERA', 'FOCUSER', 'FILTERWHEEL']:
        # Add any `None`s for any missing hardware
        if hw_class not in UT_DICT[ut]:
            UT_DICT[ut][hw_class] = None
        else:
            # Should have at least a class defined (FLI, RASA, ASA etc)
            # In practice it'll also need a PORT or SERIAL, or both, but don't validate that here
            if 'CLASS' not in UT_DICT[ut][hw_class]:
                raise ValueError('{} for UT {} does not have a valid class'.format(hw_class, ut))

    # Define available filters
    if UT_DICT[ut]['FILTERWHEEL'] is not None:
        if UT_DICT[ut]['FILTERWHEEL']['CLASS'] in ['None', 'Static', 'Fixed']:
            # This UT has a "static" filter wheel, so a fixed filter which be given
            UT_DICT[ut]['FILTERS'] = [UT_DICT[ut]['FILTERWHEEL']['FILTER']]
            UT_DICT[ut]['FILTERWHEEL'] = None
        else:
            # This UT has a filter wheel with multiple filters
            UT_DICT[ut]['FILTERS'] = UT_DICT[ut]['FILTERWHEEL']['FILTERS']
    else:
        # No filter wheel or fixed filter, so just set to Clear
        UT_DICT[ut]['FILTERS'] = ['C']

UTS = sorted(UT_DICT)
UTS_WITH_CAMERAS = [ut for ut in UTS if UT_DICT[ut]['CAMERA'] is not None]
UTS_WITH_FOCUSERS = [ut for ut in UTS if UT_DICT[ut]['FOCUSER'] is not None]
UTS_WITH_FILTERWHEELS = [ut for ut in UTS if UT_DICT[ut]['FILTERWHEEL'] is not None]
UTS_WITH_COVERS = [ut for ut in UTS if UT_DICT[ut]['OTA']['MIRROR_COVER'] is True]
ALL_FILTERS = sorted({filt for ut in UTS for filt in UT_DICT[ut]['FILTERS']})

INTERFACES = {interface_id: DAEMONS[interface_id]['UTS']
              for interface_id in DAEMONS
              if 'UTS' in DAEMONS[interface_id]}

DASHBOARD_IP = config['DASHBOARD_IP']

############################################################
# Conditions parameters
MAX_CONDITIONS_AGE = config['MAX_CONDITIONS_AGE']
FAKE_CONDITIONS = config['FAKE_CONDITIONS']

WEATHER_TIMEOUT = config['WEATHER_TIMEOUT']
WEATHER_STATIC = config['WEATHER_STATIC']
WEATHER_INTERVAL = config['WEATHER_INTERVAL']
WEATHER_SOURCES = config['WEATHER_SOURCES']

CONDITIONS_JSON_LOCATION = config['CONDITIONS_JSON_LOCATION']
DOMEALERT_URI = config['DOMEALERT_URI']
RAINDAEMON_URI = config['RAINDAEMON_URI']

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
SHIELD_WINDGUST = config['SHIELD_WINDGUST']
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

# Diskspace - free space measured in %
MIN_DISKSPACE = config['MIN_DISKSPACE']

# Satellite clouds - opacity measured in %
MAX_SATCLOUDS = config['MAX_SATCLOUDS']
SATCLOUDS_BADDELAY = config['SATCLOUDS_BADDELAY']
SATCLOUDS_GOODDELAY = config['SATCLOUDS_GOODDELAY']

# Dust level - concentration measured in μg/m³
DUSTLEVEL_TIMEOUT = config['DUSTLEVEL_TIMEOUT']
MAX_DUSTLEVEL = config['MAX_DUSTLEVEL']
DUSTLEVEL_BADDELAY = config['DUSTLEVEL_BADDELAY']
DUSTLEVEL_GOODDELAY = config['DUSTLEVEL_GOODDELAY']

# Seeing
SEEING_TIMEOUT = config['SEEING_TIMEOUT']

############################################################
# Mount parameters
MOUNT_CLASS = config['MOUNT_CLASS']
MOUNT_HOST = config['MOUNT_HOST']
MOUNT_PORT = config['MOUNT_PORT']
MOUNT_DEBUG = config['MOUNT_DEBUG']
FAKE_MOUNT = config['FAKE_MOUNT']

FAKE_MOUNT_PARKING = config['FAKE_MOUNT_PARKING']

MIN_ELEVATION = config['MIN_ELEVATION']
MAX_HOURANGLE = config['MAX_HOURANGLE']

MOUNT_HISTORY_PERIOD = config['MOUNT_HISTORY_PERIOD']

############################################################
# Interface parameters
FAKE_FLI = config['FAKE_FLI']
FAKE_ASA = config['FAKE_ASA']

############################################################
# Camera parameters
MIN_EXPOSURE_DELAY = config['MIN_EXPOSURE_DELAY']
SAVE_IMAGES_LOCALLY = config['SAVE_IMAGES_LOCALLY']
FRAMETYPE_LIST = config['FRAMETYPE_LIST']
CAM_IMAGING_TEMPERATURE = config['CAM_IMAGING_TEMPERATURE']
CAM_STANDBY_TEMPERATURE = config['CAM_STANDBY_TEMPERATURE']
COMPRESS_IMAGES = config['COMPRESS_IMAGES']
MIN_HEADER_HIST_TIME = config['MIN_HEADER_HIST_TIME']

############################################################
# Exposure Queue parameters
EXQ_DITHERING = config['EXQ_DITHERING']
DITHERING_DURATION = config['DITHERING_DURATION']

############################################################
# Power parameters
POWER_CHECK_PERIOD = config['POWER_CHECK_PERIOD']
POWER_CHECK_SCRIPT = '_power_status'
POWER_UNITS = config['POWER_UNITS']
POWER_GROUPS = config['POWER_GROUPS']
DASHBOARD_ALLOWED_OUTLETS = config['DASHBOARD_ALLOWED_OUTLETS']
OBSERVING_OFF_OUTLETS = config['OBSERVING_OFF_OUTLETS']

############################################################
# Dome parameters
FAKE_DOME = config['FAKE_DOME']
DOME_DEBUG = config['DOME_DEBUG']

DOME_CHECK_PERIOD = config['DOME_CHECK_PERIOD']
DOME_LOCATION = config['DOME_LOCATION']

DOME_IGNORE_SWITCH_ERRORS = config['DOME_IGNORE_SWITCH_ERRORS']

DOME_HEARTBEAT_LOCATION = config['DOME_HEARTBEAT_LOCATION']
DOME_HEARTBEAT_PERIOD = config['DOME_HEARTBEAT_PERIOD']

HATCH_OPEN_DELAY = config['HATCH_OPEN_DELAY']

QUICK_CLOSE_BUTTON = config['QUICK_CLOSE_BUTTON']
QUICK_CLOSE_BUTTON_PORT = config['QUICK_CLOSE_BUTTON_PORT']

DOME_ASIDE_NAME = config['DOME_ASIDE_NAME']
DOME_BSIDE_NAME = config['DOME_BSIDE_NAME']
DOME_OPEN_ASIDE_TIME = config['DOME_OPEN_ASIDE_TIME']
DOME_OPEN_BSIDE_TIME = config['DOME_OPEN_BSIDE_TIME']
DOME_CLOSE_ASIDE_TIME = config['DOME_CLOSE_ASIDE_TIME']
DOME_CLOSE_BSIDE_TIME = config['DOME_CLOSE_BSIDE_TIME']
DOME_MOVE_TIMEOUT = config['DOME_MOVE_TIMEOUT']
DOME_MOVE_TIMESTEP = config['DOME_MOVE_TIMESTEP']

DOME_STUTTER_TIME = config['DOME_STUTTER_TIME']
DOME_STUTTER_TIMESTEP = config['DOME_STUTTER_TIMESTEP']

DOME_HAS_BUMPERGUARD = config['DOME_HAS_BUMPERGUARD']
DOME_HAS_DEHUMIDIFIER = config['DOME_HAS_DEHUMIDIFIER']

DOME_WINDSHIELD_PERMITTED = config['DOME_WINDSHIELD_PERMITTED']
DOME_WINDSHIELD_POSITION = config['DOME_WINDSHIELD_POSITION']

EMERGENCY_FILE = os.path.join(FILE_PATH, 'EMERGENCY-SHUTDOWN')

############################################################
# Scheduler parameters
SCHEDULER_HOST = config['SCHEDULER_HOST']
SCHEDULER_PORT = config['SCHEDULER_PORT']

############################################################
# Pilot parameters
NUM_DARKS = config['NUM_DARKS']
PILOT_TAKE_EXTRA_DARKS = config['PILOT_TAKE_EXTRA_DARKS']

NUM_FLATS = config['NUM_FLATS']
FLATS_FILTERS = config['FLATS_FILTERS']
if FLATS_FILTERS == 'all':  # default
    FLATS_FILTERS = ','.join(ALL_FILTERS)
FLATS_TARGET_COUNTS = config['FLATS_TARGET_COUNTS']

OBS_ADJUST_FOCUS = config['OBS_ADJUST_FOCUS']
OBS_FOCUS_TEMP_COMPENSATION = config['OBS_FOCUS_TEMP_COMPENSATION']
OBS_FOCUS_IMAGES = config['OBS_FOCUS_IMAGES']

AUTOFOCUS_SLACK_REPORTS = config['AUTOFOCUS_SLACK_REPORTS']

PILOT_TAKE_FOCRUNS = config['PILOT_TAKE_FOCRUNS']
FOCRUN_PERIOD = config['FOCRUN_PERIOD']

BAD_CONDITIONS_TASKS_PERIOD = config['BAD_CONDITIONS_TASKS_PERIOD']

############################################################
# Day marshal parameters
IERS_A_URL = config['IERS_A_URL']
IERS_A_URL_BACKUP = config['IERS_A_URL_BACKUP']

############################################################
# Obs script parameters
AUTOFOCUS_PARAMS = config['AUTOFOCUS_PARAMS']
AUTOFOCUS_PARAMS = {int(ut): AUTOFOCUS_PARAMS[ut]
                    for ut in AUTOFOCUS_PARAMS
                    if int(ut) in UTS_WITH_FOCUSERS}
for ut in AUTOFOCUS_PARAMS:
    # Use default params if they're not given (not perfect, they really need to be defined per UT)
    if 'NEAR_FOCUS_VALUE' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE'] = 5
    if 'BIG_STEP' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['BIG_STEP'] = 5000
    if 'SMALL_STEP' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['SMALL_STEP'] = 1000
    if 'SLOPE_LEFT' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['SLOPE_LEFT'] = -0.001
    if 'SLOPE_RIGHT' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['SLOPE_RIGHT'] = 0.001
    if 'DELTA_X' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['DELTA_X'] = 2000
    if 'TEMP_GRADIENT' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['TEMP_GRADIENT'] = 0
    if 'TEMP_MINCHANGE' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['TEMP_MINCHANGE'] = 0.5
    if 'FOCRUN_SCALE' not in AUTOFOCUS_PARAMS[ut]:
        AUTOFOCUS_PARAMS[ut]['FOCRUN_SCALE'] = 1
    # Enforce type
    AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE'] = int(AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE'])
    AUTOFOCUS_PARAMS[ut]['BIG_STEP'] = int(AUTOFOCUS_PARAMS[ut]['BIG_STEP'])
    AUTOFOCUS_PARAMS[ut]['SMALL_STEP'] = int(AUTOFOCUS_PARAMS[ut]['SMALL_STEP'])
    AUTOFOCUS_PARAMS[ut]['SLOPE_LEFT'] = float(AUTOFOCUS_PARAMS[ut]['SLOPE_LEFT'])
    AUTOFOCUS_PARAMS[ut]['SLOPE_RIGHT'] = float(AUTOFOCUS_PARAMS[ut]['SLOPE_RIGHT'])
    AUTOFOCUS_PARAMS[ut]['DELTA_X'] = float(AUTOFOCUS_PARAMS[ut]['DELTA_X'])
    AUTOFOCUS_PARAMS[ut]['TEMP_GRADIENT'] = float(AUTOFOCUS_PARAMS[ut]['TEMP_GRADIENT'])
    AUTOFOCUS_PARAMS[ut]['TEMP_MINCHANGE'] = float(AUTOFOCUS_PARAMS[ut]['TEMP_MINCHANGE'])
    AUTOFOCUS_PARAMS[ut]['FOCRUN_SCALE'] = float(AUTOFOCUS_PARAMS[ut]['FOCRUN_SCALE'])

############################################################
# Slack bot parameters
ENABLE_SLACK = config['ENABLE_SLACK']
SLACK_BOT_TOKEN = config['SLACK_BOT_TOKEN']
SLACK_DEFAULT_CHANNEL = config['SLACK_DEFAULT_CHANNEL']
