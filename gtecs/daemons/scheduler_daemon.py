#!/usr/bin/env python
"""Daemon to allow remote computation of next observation."""

import datetime

from gtecs import misc
from gtecs.daemons import HardwareDaemon
from gtecs.scheduler import check_queue


class SchedulerDaemon(HardwareDaemon):
    """Scheduler interface daemon class."""

    def __init__(self):
        super().__init__('scheduler')

    # Control functions
    def check_queue(self, *args):
        """Check the current queue for the best pointing to do."""
        next_pointing = check_queue(*args)
        if next_pointing is not None:
            self.log.info('Scheduler returns: pointing ID {}'.format(next_pointing.pointing_id))
        else:
            self.log.info('Scheduler returns: None')
        return next_pointing

    def get_info(self, *args):
        """Return power status info."""
        info = {}
        next_pointing = check_queue(*args)
        if next_pointing is not None:
            self.log.info('Scheduler returns: pointing ID {}'.format(next_pointing.pointing_id))
        else:
            self.log.info('Scheduler returns: None')
        info['next_pointing'] = next_pointing

        now = datetime.datetime.utcnow()
        info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

        return info


if __name__ == "__main__":
    daemon_id = 'scheduler'
    with misc.make_pid_file(daemon_id):
        SchedulerDaemon()._run()
