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
LOG_PATH='/local/mjd/logs/'
SCRIPT_PATH='/local/mjd/backed_up_on_astro3/main_edits/'

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
        'HOST':     'janus',
        'PORT':     9001,
        'PYROID':   'mnt_daemon',
        'PINGLIFE': 10.
        },
    'sitech':{ # sitech daemon
        'PROCESS':  'sitech_daemon.py',
        'HOST':     '143.167.113.234', # MJD's laptop
        'PORT':     7766, # No real reason
        'PYROID':   'sitech_daemon',
        'PINGLIFE': 10.
        },
    'filt':{ # filter wheel daemon
        'PROCESS':  'filt_daemon.py',
        'HOST':     'janus', # MJD's laptop
        'PORT':     9002, # No real reason
        'PYROID':   'filt_daemon',
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
FILTER_LIST=('H','B','V','R','I')
