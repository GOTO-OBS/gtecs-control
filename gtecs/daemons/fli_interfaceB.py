#!/usr/bin/env python
"""
Clone FLI interface to allow testing on a single host
"""

import sys
import time
import Pyro4

from gtecs import misc
from gtecs import params


# Directly import a copy of the real interface daemon
from fli_interface import FLIDaemon


def start():
    """Create Pyro server, register the daemon and enter request loop"""

    # define which interface this is
    intf = 'fli2'

    host = params.DAEMONS[intf]['HOST']
    port = params.DAEMONS[intf]['PORT']

    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(intf):
        sys.exit()

    # Start the daemon
    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        fli_daemon = FLIDaemon(intf)
        uri = pyro_daemon.register(fli_daemon, objectId=intf)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        fli_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=fli_daemon.status_function)

    # Loop has closed
    fli_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)


if __name__ == "__main__":
    start()
