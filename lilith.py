from __future__ import absolute_import
from __future__ import print_function
#!/usr/bin/env python

########################################################################
#                               lilith.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#       G-TeCS script to provide overall control of the daemons        #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import os, sys
import readline
import time
import Pyro4
# TeCS modules
from tecs_modules import misc
from tecs_modules import params

if __name__ == '__main__':
    if sys.argv[1] in ['start','shutdown','kill','ping']:
        if len(sys.argv) > 2:
            daemons = sys.argv[2:]
        else:
            daemons = list(params.DAEMONS.keys())
        for d in daemons:
            print(d+':\t' + misc.python_command(d+'.py', sys.argv[1]))
    else:
        print('Valid commands: start, shutdown, kill, ping')
