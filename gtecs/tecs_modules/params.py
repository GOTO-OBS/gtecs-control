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
# TeCS modules
from . import power_control
from . import dome_control

########################################################################
# General parameters

# Common file strings
ORIGIN = "Gravitational-wave Optical Transient Observer" # "organisation or institution"
TELESCOP = "GOTO_sim" # "the telescope used", will be appended with details (e.g. [GOTO_N]-ut2"

# File locations (need to alter depending on system)
HOST = socket.gethostname()
if HOST == 'eddie': # MJD's laptop
    TECS_PATH = '/home/martin/Dropbox/Sheffield/g-tecs/'
elif HOST == 'janus': # MJD's desktop
    TECS_PATH = '/local/mjd/backed_up_on_astro3/g-tecs/'
elif HOST == 'host-137-205-160-42.warwick.ac.uk' or HOST == 'b8-ae-ed-75-09-42.warwick.ac.uk' or HOST == 'gotolapalma': # Warwick test NUCs
    TECS_PATH = '/home/mdyer/g-tecs/'
elif HOST == 'Stus-MacBook-Pro.local' or HOST.startswith('dyn'):  # SL laptop
    TECS_PATH = '/Users/sl/g-tecs/'
else:
    TECS_PATH = '/home/goto/g-tecs/'

DAEMON_PATH = pkg_resources.resource_filename('gtecs', 'daemons')
CONFIG_PATH = TECS_PATH
LOG_PATH = TECS_PATH + 'logs/'
IMAGE_PATH = TECS_PATH + 'images/'

# Daemons should log to file?
FILE_LOGGING = 1
# Daemons should to stdout?
STDOUT_LOGGING = 1
# redirect Daemon stdout to file?
REDIRECT_STDOUT = 0

# Site location (predicted location of GOTO dome on La Palma)
SITE_LATITUDE = 28.7598742
SITE_LONGITUDE = -17.8793802
SITE_ALTITUDE = 2327

# Pyro connection
PROXY_TIMEOUT = 0.5
Pyro4.config.SERIALIZER = 'pickle' # IMPORTANT - Can seralize numpy arrays for images
Pyro4.config.SERIALIZERS_ACCEPTED.add('pickle')

# Email alerts
EMAIL_LIST = ['martin.dyer@sheffield.ac.uk']
EMAIL_ADDRESS = 'goto-observatory@gmail.com' # An example
EMAIL_SERVER = 'smtp.gmail.com:587'

########################################################################
# Daemon parameters
DAEMONS = {
    'mnt':{ # mount daemon
        'PROCESS':  'mnt_daemon.py',
        'HOST':     HOST, #'host-137-205-160-42.warwick.ac.uk',
        'PORT':     9001,
        'PYROID':   'mnt_daemon',
        'PINGLIFE': 10.
        },
    'filt':{ # filter wheel daemon
        'PROCESS':  'filt_daemon.py',
        'HOST':     HOST,
        'PORT':     9002,
        'PYROID':   'filt_daemon',
        'PINGLIFE': 10.
        },
    'foc':{ # focuser daemon
        'PROCESS':  'foc_daemon.py',
        'HOST':     HOST,
        'PORT':     9003,
        'PYROID':   'foc_daemon',
        'PINGLIFE': 10.
        },
    'cam':{ # camera daemon
        'PROCESS':  'cam_daemon.py',
        'HOST':     HOST,
        'PORT':     9004,
        'PYROID':   'cam_daemon',
        'PINGLIFE': 10.
        },
    'exq':{ # exposure queue daemon
        'PROCESS':  'exq_daemon.py',
        'HOST':     HOST,
        'PORT':     9005,
        'PYROID':   'exq_daemon',
        'PINGLIFE': 10.
        },
    'power':{ # hardware power daemon
        'PROCESS':  'power_daemon.py',
        'HOST':     HOST, #'host-137-205-160-42.warwick.ac.uk',
        'PORT':     9006,
        'PYROID':   'power_daemon',
        'PINGLIFE': 10.
        },
    'dome':{ # dome daemon
        'PROCESS':  'dome_daemon.py',
        'HOST':     HOST,
        'PORT':     9007,
        'PYROID':   'dome_daemon',
        'PINGLIFE': 10.
        }
}

for key in DAEMONS:
    DAEMONS[key]['ADDRESS'] = 'PYRO:' + DAEMONS[key]['PYROID'] + '@' + DAEMONS[key]['HOST'] + ':' + str(DAEMONS[key]['PORT'])

FLI_INTERFACES = {
    'nuc1':{ # for unit telescopes 1 and 2
        'PROCESS': 'fli_interface.py',
        'HOST':    HOST,
        'PORT':    9010,
        'PYROID':  'fli_interface',
        'TELS':    [1,2],
        'SERIALS': {
            'cam': ['fake', 'fake'],
            'foc': ['fake', 'fake'],
            'filt':['fake', 'fake']
            }
       },
   'nuc2':{ # for unit telescopes 3 and 4
       'PROCESS':  'fli_interfaceB.py',
       'HOST':     HOST,
       'PORT':     9020,
       'PYROID':   'fli_interfaceB',
       'TELS':      [3,4],
       'SERIALS': {
           'cam': ['fake', 'fake'],
           'foc': ['fake', 'fake'],
           'filt':['fake', 'fake']
           }
        }
    }

TEL_DICT = {}
for nuc in FLI_INTERFACES:
    FLI_INTERFACES[nuc]['ADDRESS'] = 'PYRO:' + FLI_INTERFACES[nuc]['PYROID'] + '@' + FLI_INTERFACES[nuc]['HOST'] + ':' + str(FLI_INTERFACES[nuc]['PORT'])
    for HW, tel in enumerate(FLI_INTERFACES[nuc]['TELS']):
        TEL_DICT[tel] = [nuc,HW]

########################################################################
# Mount parameters
WIN_HOST = '137.205.160.1'

SITECH_PROCESS = 'sitech_interface.py'
SITECH_PYROID = 'sitech_interface'
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
DARKFILT = 'C' #as an example
BIASEXP = 0.1 #seconds, as an example

# Queue parameters
QUEUE_PATH = TECS_PATH

# Power parameters
POWER = power_control.FakePower(' ',' ') #power_control.APCPower('137.205.160.50')
POWER_CHECK_SCRIPT = '_power_status'
POWER_LIST = ['mnt','filt','foc','cam','_5_','_6_','_7_','_8_']

# Dome parameters
DOME_LOCATION = '/dev/serial/by-id/usb-FTDI_UC232R_FTWDFJ4H-if00-port0'
DOME = dome_control.FakeDome('') #AstroHavenDome(DOME_LOCATION)
BIG_RED_BUTTON_PORT = 'N/A'
EMERGENCY_FILE = CONFIG_PATH + 'EMERGENCY-SHUTDOWN'
