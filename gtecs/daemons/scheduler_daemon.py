#!/usr/bin/env python
"""
Daemon to allow remote computation of next observation
"""

import sys
import pid
import time
import Pyro4
import datetime

from gtecs import misc
from gtecs import params
from gtecs import scheduler
from gtecs.daemons import InterfaceDaemon, run


class SchedulerDaemon(InterfaceDaemon):
    """Scheduler interface daemon class"""

    def __init__(self):
        ### initiate daemon
        InterfaceDaemon.__init__(self, daemon_ID='scheduler')


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
    try:
        with pid.PidFile('scheduler', piddir=params.CONFIG_PATH):
            run(SchedulerDaemon())
    except pid.PidFileError:
        raise misc.MultipleDaemonError('Daemon already running')
