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
import os,sys
import socket
import numpy
# TeCS modules
import power_control

########################################################################
# General parameters

# File locations
HOST = socket.gethostname() # Need to alter depending on system
if HOST == 'eddie': # MJD's laptop
    TECS_PATH = '/home/martin/Dropbox/Sheffield/g-tecs/'
elif HOST == 'host-137-205-160-42.warwick.ac.uk': # Warwick test NUC
    TECS_PATH = '/home/mdyer/g-tecs/'

SCRIPT_PATH = TECS_PATH
LOG_PATH = TECS_PATH + 'logs/'
IMAGE_PATH = TECS_PATH + 'images/'

# Log form
LOGGING = 1

# Site location (predicted location of GOTO dome on La Palma)
SITE_LATITUDE = 28.7598742
SITE_LONGITUDE = -17.8793802

# Pyro connection
PROXY_TIMEOUT = 0.5

########################################################################
# Daemon parameters

DAEMONS = {
    'mnt':{ # mount daemon
        'PROCESS':  'mnt_daemon.py',
        'HOST':     'host-137-205-160-42.warwick.ac.uk',
        'PORT':     9001,
        'PYROID':   'mnt_daemon',
        'PINGLIFE': 10.
        },
    'filt':{ # filter wheel daemon
        'PROCESS':  'filt_daemon.py',
        'HOST':     'eddie',
        'PORT':     9002,
        'PYROID':   'filt_daemon',
        'PINGLIFE': 10.
        },
    'foc':{ # focuser daemon
        'PROCESS':  'foc_daemon.py',
        'HOST':     'eddie',
        'PORT':     9003,
        'PYROID':   'foc_daemon',
        'PINGLIFE': 10.
        },
    'cam':{ # camera daemon
        'PROCESS':  'cam_daemon.py',
        'HOST':     'eddie',
        'PORT':     9004,
        'PYROID':   'cam_daemon',
        'PINGLIFE': 10.
        },
    'queue':{ # exposure queue daemon
        'PROCESS':  'queue_daemon.py',
        'HOST':     'eddie',
        'PORT':     9005,
        'PYROID':   'queue_daemon',
        'PINGLIFE': 10.
        },
    'power':{ # hardware power daemon
        'PROCESS':  'power_daemon.py',
        'HOST':     'host-137-205-160-42.warwick.ac.uk',
        'PORT':     9006,
        'PYROID':   'power_daemon',
        'PINGLIFE': 10.
        }
}

for key in DAEMONS:
    DAEMONS[key]['ADDRESS'] = 'PYRO:' + DAEMONS[key]['PYROID'] + '@' + DAEMONS[key]['HOST'] + ':' + str(DAEMONS[key]['PORT'])

########################################################################
# Mount parameters
WIN_HOST = '137.205.160.1'

SITECH_PROCESS = 'sitech.py'
SITECH_PYROID = 'sitech'
SITECH_PORT = 9000
SITECH_ADDRESS = 'PYRO:' + SITECH_PYROID + '@' + WIN_HOST + ':' + str(SITECH_PORT)

WIN_PATH = 'C:/goto_mount/'
CYGWIN_PATH = '/cygdrive/c/goto_mount/'
CYGWIN_PYTHON_PATH = '/cygdrive/c/Python27/python.exe'

MIN_ELEVATION = 20. #degrees
DEFAULT_OFFSET_STEP = 10. #arcsec

# Filter wheel parameters
FILTER_LIST = ['L','R','G','B','C']

# Camera parameters
FRAMETYPE_LIST = ['normal','dark','rbi_flush']

# Queue parameters
QUEUE_PATH = TECS_PATH

# Power parameters
POWER = power_control.APCPower('137.205.160.50')
POWER_CHECK_SCRIPT = '_power_status.py'
POWER_LIST = ['mnt','filt','foc','cam','_5_','_6_','_7_','_8_']
