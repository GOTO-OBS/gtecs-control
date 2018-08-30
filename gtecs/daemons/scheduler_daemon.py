#!/usr/bin/env python
"""Daemon to allow remote computation of next observation."""

import threading
import time

from astropy.time import Time

from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon
from gtecs.scheduler import check_queue


class SchedulerDaemon(BaseDaemon):
    """Scheduler database daemon class."""

    def __init__(self):
        super().__init__('scheduler')

        # scheduler variables
        self.check_period = 5  # TODO: put into params, match with pilot

        self.write_html = False

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')

        while(self.running):
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time
                self.force_check_flag = False

                # Nothing to connect to, just get the info
                self._get_info()

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Internal functions
    def _get_info(self):
        """Get the latest status info from the heardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        # Get info from the database
        try:
            temp_info['next_pointing'] = check_queue(Time(self.loop_time, format='unix'),
                                                     write_html=self.write_html)
        except Exception:
            self.log.error('Failed to get next pointing from database')
            self.log.debug('', exc_info=True)
            temp_info['next_pointing'] = None

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    # Control functions
    def check_queue(self):
        """Check the current queue for the best pointing to do."""
        next_pointing = self.info['next_pointing']
        if next_pointing is not None:
            self.log.info('Scheduler returns: pointing ID {}'.format(next_pointing.pointing_id))
        else:
            self.log.info('Scheduler returns: None')
        return next_pointing


if __name__ == "__main__":
    daemon_id = 'scheduler'
    with misc.make_pid_file(daemon_id):
        SchedulerDaemon()._run()
