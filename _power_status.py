#!/usr/bin/env python

########################################################################
#                             _power_status.py                         #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#       G-TeCS script to check power status, part of power_deamon      #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

# Tiny script to print power status to stdout
# Allows function call to be killed if it locks up
# NOT for manual use - should ONLY be called by power daemon

import X_params as params

try:
    x = params.POWER.status(0)
    assert type(x) == type('')
    assert len(x) == 8
except:
    x = 'xERRORxx'

print x
