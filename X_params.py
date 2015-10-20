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
import sys
import numpy

########################################################################
# General parameters

# File locations
LOG_PATH='/home/mdyer/'
WIN_PATH='/cygdrive/c/goto_mount/'
SCRIPT_PATH='/home/mdyer/'

# Log form
LOGGING=1

# Site location (predicted location of GOTO dome on La Palma)
SITE_LATITUDE=28.7598742
SITE_LONGITUDE=-17.8793802

########################################################################
# Daemon parameters
DAEMONS={
    'mnt':{ # mount daemon
        'PROCESS':  'mnt_daemon.py',
        'HOST':     'host-137-205-160-42.warwick.ac.uk',
        'PORT':     9001,
        'PYROID':   'mnt_daemon',
        'PINGLIFE': 10.
        },
    'sitech':{ # sitech daemon
        'PROCESS':  'sitech_daemon.py',
        'HOST':     '137.205.160.1',
        'PORT':     7766, # No real reason
        'PYROID':   'sitech_daemon',
        'PINGLIFE': 10.
        },
    'filt':{ # filter wheel daemon
        'PROCESS':  'filt_daemon.py',
        'HOST':     'Aspire-VN7-791',
        'PORT':     9002,
        'PYROID':   'filt_daemon',
        'PINGLIFE': 10.
        },
    'foc':{ # focuser
        'PROCESS':  'foc_daemon.py',
        'HOST':     'Aspire-VN7-791', # MJD's laptop
        'PORT':     9003,
        'PYROID':   'foc_daemon',
        'PINGLIFE': 10.
        }
}

for key in DAEMONS:
    DAEMONS[key]['ADDRESS']='PYRO:'+DAEMONS[key]['PYROID']+'@'+DAEMONS[key]['HOST']+':'+str(DAEMONS[key]['PORT'])

########################################################################
# Mount parameters
MIN_ELEVATION=20. #degrees
DEFAULT_OFFSET_STEP=10. #arcsec

# Filter wheel parameters
FILTER_LIST=['L','R','B','G','C']
