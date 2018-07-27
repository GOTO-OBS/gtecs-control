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


DAEMON_ID = 'fli2'
DAEMON_HOST = params.DAEMONS[DAEMON_ID]['HOST']
DAEMON_PORT = params.DAEMONS[DAEMON_ID]['PORT']


def run():
    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(DAEMON_ID):
        sys.exit()

    # Create the daemon object
    daemon = FLIDaemon(intf=DAEMON_ID)

    # Start the daemon
    with Pyro4.Daemon(host=DAEMON_HOST, port=DAEMON_PORT) as pyro_daemon:
        uri = pyro_daemon.register(daemon, objectId=DAEMON_ID)
        Pyro4.config.COMMTIMEOUT = params.PYRO_TIMEOUT

        # Start request loop
        daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=daemon.status_function)

    # Loop has closed
    daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)


if __name__ == "__main__":
    run()
