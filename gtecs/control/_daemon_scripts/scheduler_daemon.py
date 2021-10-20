#!/usr/bin/env python3
"""Daemon to allow remote computation of next observation."""

import threading
import time

from astropy.time import Time

from gtecs.control import misc
from gtecs.control import params
from gtecs.control.astronomy import above_horizon, get_horizon, observatory_location
from gtecs.control.daemons import BaseDaemon
from gtecs.obs.scheduler import check_queue


class SchedulerDaemon(BaseDaemon):
    """Scheduler database daemon class."""

    def __init__(self):
        super().__init__('scheduler')

        # scheduler variables
        self.check_period = params.SCHEDULER_CHECK_PERIOD

        self.location = observatory_location()
        try:
            self.horizon = get_horizon(params.HORIZON_FILE)
        except OSError:
            self.horizon = 30
            self.log.warning('Could not load horizon file ({}), using default ({} deg)'.format(
                             params.HORIZON_FILE, self.horizon))
        try:
            self.horizon_high = get_horizon(params.HORIZON_SHIELDING_FILE)
        except OSError:
            self.horizon_high = 40
            self.log.warning('Could not load high horizon file ({}), using default ({} deg)'.format(
                             params.HORIZON_SHIELDING_FILE, self.horizon_high))
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
            time = Time(self.loop_time, format='unix')
            pointing = check_queue(time, self.location, self.horizon,
                                   write_html=self.write_html,
                                   log=self.log)
            temp_info['pointing'] = pointing

            if pointing is not None and not above_horizon(pointing.ra, pointing.dec, time,
                                                          self.horizon_high):
                # The pointing is above the default horizon but not the higher windshield horizon.
                # This should be fairly rare, and while there might be a more efficient way here
                # we just recalculate using the higher horizon.
                # A better solution would be for the check_queue function to take multiple horizons,
                # then evaluate each pointing based on both (it should be fairly quick, since altaz
                # is cached).
                pointing_high = check_queue(time, self.location, self.horizon_high,
                                            write_html=self.write_html,
                                            log=self.log)
            else:
                pointing_high = pointing
            temp_info['pointing_high'] = pointing_high
        except Exception:
            self.log.error('Failed to get next pointing from database')
            self.log.debug('', exc_info=True)
            temp_info['pointing'] = None
            temp_info['pointing_high'] = None

        # Write debug log line
        try:
            now_pointing = temp_info['pointing']
            now_pointing_high = temp_info['pointing_high']

            if (self.info is None or
                    now_pointing != self.info['pointing'] or
                    now_pointing_high != self.info['pointing_high']):
                if now_pointing is not None and now_pointing_high == now_pointing:
                    self.log.debug('Scheduler returns pointing {}'.format(now_pointing.db_id))
                elif now_pointing_high != now_pointing:
                    self.log.debug('Scheduler returns pointing {}|high={}'.format(
                        now_pointing.db_id, now_pointing_high.db_id))
                else:
                    self.log.debug('Scheduler returns None')

        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    # Control functions
    def check_queue(self, horizon='low', force_update=False):
        """Returns the current highest priority pointing.

        Note this returns immediately with what's been previously calculated by the scheduler,
        unless the `force_update` flag is set to True.

        Parameters
        ----------
        horizon : 'low' or 'high', default='low'
            Flag to chose the active horizon file.
                - 'low' reads `gtecs.control.params.HORIZON_FILE`, and is the default
                - 'high' reads `gtecs.control.params.HORIZON_SHIELDING_FILE`, and is used if
                        the dome windshielding is active (reducing the available sky)

        force_update : bool, default=False
            If True force the scheduler to recalculate at the current time.
            Otherwise the pointing from the most recent check will be returned (~5s cadence).
        """
        if force_update:
            self.wait_for_info()
        if horizon == 'high':
            return self.info['pointing_high']
        else:
            return self.info['pointing']


if __name__ == '__main__':
    daemon_id = 'scheduler'
    with misc.make_pid_file(daemon_id):
        SchedulerDaemon()._run()
