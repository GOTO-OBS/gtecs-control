#!/usr/bin/env python

########################################################################
#                          scheduler_daemon.py                         #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#     G-TeCS daemon to allow remote computation of next observation    #
#                    Stu Littlefair, Sheffield, 2017                   #
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
import datetime

# TeCS modules
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.tecs_modules import scheduler
from gtecs.tecs_modules.daemons import InterfaceDaemon


class SchedulerDaemon(InterfaceDaemon):
    """
    Scheduler daemon.

    Contains a single function check_queue, which returns the next job.
    """
    def __init__(self):
        InterfaceDaemon.__init__(self, 'scheduler')

    def check_queue(self, *args):
        next_pointing = scheduler.check_queue(*args)
        self.logfile.info('Scheduler gives pointing ID: {}'.format(next_pointing.id))
        return next_pointing

    def get_info(self, *args):
        info = {}
        next_pointing = scheduler.check_queue(*args)
        self.logfile.info('Scheduler gives pointing ID: {}'.format(next_pointing.id))
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
