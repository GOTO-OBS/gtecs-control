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
from gtecs.daemons import InterfaceDaemon, run


DAEMON_ID = 'scheduler'


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
    daemon = SchedulerDaemon()
    run(daemon, DAEMON_ID)
