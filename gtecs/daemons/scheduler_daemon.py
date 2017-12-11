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


class SchedulerDaemon(InterfaceDaemon):
    """Scheduler interface daemon class"""

    def __init__(self):
        InterfaceDaemon.__init__(self, 'scheduler')


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


def start():
    '''
    Create Pyro server, register the daemon and enter request loop
    '''
    host = params.DAEMONS['scheduler']['HOST']
    port = params.DAEMONS['scheduler']['PORT']

    # Check the daemon isn't already running
    if not misc.there_can_only_be_one('scheduler'):
        sys.exit()

    # Start the daemon
    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        scheduler_daemon = SchedulerDaemon()
        uri = pyro_daemon.register(scheduler_daemon, objectId='scheduler')
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        scheduler_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=scheduler_daemon.status_function)

    # Loop has closed
    scheduler_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)


if __name__ == "__main__":
    start()
