#!/usr/bin/env python
"""Daemon to listen for alerts and insert them into the database."""

import socket
import threading
import time

from astropy.time import Time

import gcn.voeventclient as pygcn

from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon
from gtecs.voevents import Handler

from lxml.etree import XMLSyntaxError


class SentinelDaemon(BaseDaemon):
    """Sentinel alerts daemon class."""

    def __init__(self):
        super().__init__('sentinel')

        # sentinel variables
        self.check_period = 5  # TODO: put into params

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

        # start alert listener thread
        t2 = threading.Thread(target=self._alert_listener_thread)
        t2.daemon = True
        t2.start()

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

    # Secondary threads
    def _alert_listener_thread(self):
        """Connect to a VOEvent Transport Protocol server and listen for VOEvents.

        Based on PyGCN's listen function:
        https://github.com/lpsinger/pygcn/blob/master/gcn/voeventclient.py

        """
        self.log.info('Alert Listener thread started')

        # Generate a handler function using the logger
        handler = Handler(self.log).get_handler()

        # This first while loop means the socket will be recreated if it closes.
        while self.running:
            # Create the socket
            vo_socket = pygcn._open_socket(params.VOSERVER_HOST, params.VOSERVER_PORT,
                                           log=self.log,
                                           iamalive_timeout=90,
                                           max_reconnect_timeout=32)

            # Create a simple listen function
            def _listen(vo_socket, handler):
                try:
                    while True:
                        pygcn._ingest_packet(vo_socket, params.LOCAL_IVO, handler, self.log)
                except socket.timeout:
                    self.log.warning('socket timed out')
                except socket.error:
                    self.log.warning('socket error')
                except XMLSyntaxError:
                    self.log.error('XML syntax error')
                    self.log.debug('', exc_info=True)
                except Exception as err:
                    self.log.error('Error in alert listener')
                    self.log.debug('', exc_info=True)

            # launch the listener within a new thread
            listner = threading.Thread(target=_listen, args=(vo_socket, handler))
            listner.daemon = True
            listner.start()

            # This second loop will monitor the thread
            while self.running:
                if listner.is_alive():
                    time.sleep(1)
                else:
                    print('Alert listener failed')
                    break

            # Either the listener failed or we're no longer running
            # Close the socket nicely
            try:
                vo_socket.shutdown(socket.SHUT_RDWR)
            except socket.error:
                self.log.error('Could not shut down socket')
                self.log.debug('', exc_info=True)
            try:
                vo_socket.close()
            except socket.error:
                self.log.error('Could not close socket')
                self.log.debug('', exc_info=True)
            else:
                self.log.info('closed socket connection')

        self.log.info('Alert Listener thread stopped')
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
    daemon_id = 'sentinel'
    with misc.make_pid_file(daemon_id):
        SentinelDaemon()._run()
