#!/usr/bin/env python
"""
Daemon to allow remote computation of next observation
"""

import sys
import time
import Pyro4
import datetime

from gtecs import misc
from gtecs import params
from gtecs import scheduler
from gtecs.daemons import InterfaceDaemon


DAEMON_ID = 'scheduler'
DAEMON_HOST = params.DAEMONS[DAEMON_ID]['HOST']
DAEMON_PORT = params.DAEMONS[DAEMON_ID]['PORT']


class SchedulerDaemon(InterfaceDaemon):
    """Scheduler interface daemon class"""

    def __init__(self):
        self.daemon_id = DAEMON_ID
        InterfaceDaemon.__init__(self, self.daemon_id)


    def check_queue(self, *args):
        next_pointing = scheduler.check_queue(*args)
        if next_pointing is not None:
            self.logfile.info('Scheduler returns: pointing ID {}'.format(next_pointing.id))
        else:
            self.logfile.info('Scheduler returns: None')
        return next_pointing


    def get_info(self, *args):
        info = {}
        next_pointing = scheduler.check_queue(*args)
        if next_pointing is not None:
            self.logfile.info('Scheduler returns: pointing ID {}'.format(next_pointing.id))
        else:
            self.logfile.info('Scheduler returns: None')
        info['next_pointing'] = next_pointing

        now = datetime.datetime.utcnow()
        info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

        return info


    def get_info_simple(self):
        """Return plain status dict, or None"""
        try:
            info = self.get_info()
        except:
            return None
        return info


if __name__ == "__main__":
    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(DAEMON_ID):
        sys.exit()

    # Create the daemon object
    daemon = SchedulerDaemon()

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
