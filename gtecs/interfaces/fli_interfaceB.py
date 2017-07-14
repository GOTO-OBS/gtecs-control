#!/usr/bin/env python

########################################################################
#                           fli_interfaceB.py                          #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#            Clone FLI interface to allow testing on one host          #
#                    Martin Dyer, Sheffield, 2015-16                   #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import time
import sys
import Pyro4
# TeCS modules
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params

########################################################################

# Directly import a copy of the real interface daemon
from fli_interface import FLIDaemon

########################################################################

def start():
    '''
    Create Pyro server, register the daemon and enter request loop
    '''
    # define which interface this is
    intf = 'fli2'

    host = params.FLI_INTERFACES[intf]['HOST']
    port = params.FLI_INTERFACES[intf]['PORT']
    pyroID = params.FLI_INTERFACES[intf]['PYROID']

    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(intf):
        sys.exit()

    # Start the daemon
    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        fli_daemon = FLIDaemon(intf)
        uri = pyro_daemon.register(fli_daemon, objectId=pyroID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        fli_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=fli_daemon.status_function)

    # Loop has closed
    fli_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
