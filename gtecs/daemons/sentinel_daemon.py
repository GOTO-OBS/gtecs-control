#!/usr/bin/env python
"""Daemon to listen for alerts and insert them into the database."""

import socket
import threading
import time

from astropy.time import Time

import gcn.voeventclient as pygcn

from gotoalert.alert import event_handler
from gotoalert.events import Event

from gtecs import misc
from gtecs import params
from gtecs.daemons import BaseDaemon
from gtecs.slack import send_slack_msg


class SentinelDaemon(BaseDaemon):
    """Sentinel alerts daemon class."""

    def __init__(self):
        super().__init__('sentinel')

        # sentinel variables
        self.listening = True
        self.events_queue = []
        self.latest_event = None
        self.processed_events = 0
        self.interesting_events = 0

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

            # sentinel processes
            # check the events queue, take off the first entry
            if len(self.events_queue) > 0:
                # There's at least one new event!
                self.latest_event = self.events_queue.pop(0)
                self.log.info('Processing new event: {}'.format(self.latest_event.ivorn))
                try:
                    self._handle_event()
                except Exception:
                    self.log.error('handle_event command failed')
                    self.log.debug('', exc_info=True)

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Secondary threads
    def _alert_listener_thread(self):
        """Connect to a VOEvent Transport Protocol server and listen for VOEvents.

        Based on PyGCN's listen function:
        https://github.com/lpsinger/pygcn/blob/master/gcn/voeventclient.py

        """
        self.log.info('Alert listener thread started')

        # Define a handler function
        # All we need this to do is create and Event and add it to the queue
        def _handler(payload, root):
            event = Event(payload)
            self.events_queue.append(event)

        # This first while loop means the socket will be recreated if it closes.
        while self.running:
            # Only listen if self.listening is True
            if self.listening:
                # Create the socket
                vo_socket = pygcn._open_socket(params.VOSERVER_HOST, params.VOSERVER_PORT,
                                               log=self.log,
                                               iamalive_timeout=90,
                                               max_reconnect_timeout=8)

                # Create a simple listen function
                def _listen(vo_socket, handler):
                    try:
                        while True:
                            pygcn._ingest_packet(vo_socket, params.LOCAL_IVO, handler, self.log)
                    except socket.timeout:
                        self.log.warning('socket timed out')
                    except socket.error:
                        if self.running and self.listening:
                            # It's only a problem if we're not the one shutting the socket
                            self.log.warning('socket error')
                    except Exception as err:
                        self.log.error('Error in alert listener')
                        self.log.debug('', exc_info=True)

                # launch the listener within a new thread
                listener = threading.Thread(target=_listen, args=(vo_socket, _handler))
                listener.daemon = True
                listener.start()

                # This second loop will monitor the thread
                while self.running and self.listening:
                    if listener.is_alive():
                        time.sleep(1)
                    else:
                        self.log.error('Alert listener failed')
                        break

                # Either the listener failed or listening or running have been set to False
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

            else:
                self.log.warning('Alert listener paused')
                time.sleep(2)

        self.log.info('Alert listener thread stopped')
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

        # Get internal info
        if self.listening:
            temp_info['status'] = 'Listening'
        else:
            temp_info['status'] = 'Paused'
        temp_info['pending_events'] = len(self.events_queue)
        temp_info['latest_event'] = self.latest_event
        temp_info['processed_events'] = self.processed_events
        temp_info['interesting_events'] = self.interesting_events

        # Write debug log line
        try:
            now_str = '{} ({} processed, {} interesting)'.format(temp_info['status'],
                                                                 temp_info['processed_events'],
                                                                 temp_info['interesting_events'])
            if not self.info:
                self.log.debug('Sentinel is {}'.format(now_str))
            else:
                old_str = '{} ({} processed, {} interesting)'.format(
                    self.info['status'],
                    self.info['processed_events'],
                    self.info['interesting_events'])
                if now_str != old_str:
                    self.log.debug('Sentinel is {}'.format(now_str))
        except Exception:
            self.log.error('Could not write current status')

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    def _handle_event(self):
        """Archive each VOEvent, then pass it to GOTO-alert."""
        event = self.latest_event

        # Archive the event
        event.archive(params.CONFIG_PATH + 'voevents/', self.log)

        # Run GOTO-alert's event handler
        event = event_handler(event, self.log,
                              write_html=params.SENTINEL_WRITE_HTML,
                              send_messages=params.SENTINEL_SEND_MESSAGES)
        if event:
            # If the event was returned it was classed as "interesting"
            # If event is None then we don't care
            self.log.info('Interesting event {} processed'.format(event.name))
            self._send_slack_report(event)
            self.interesting_events += 1

        # Done!
        self.processed_events += 1

    def _send_slack_report(self, event):
        """Send a report to Slack detailing the interesting event."""
        title = ['*Sentinel processed {} event {}*'.format(event.source, event.id)]

        details = ['IVORN: {}'.format(event.ivorn),
                   'Notice type: {}'.format(event.notice),
                   'Type: {}'.format(event.type),
                   'Event time: {}'.format(event.time),
                   'Applied to grid: {}'.format(event.grid.name),
                   'Tile table:']

        table = ['```',
                 'tilename  ra        dec       prob    ',
                 '[str]     [deg]     [deg]     [%]     ',
                 '--------  --------  --------  ------- ',
                 ]
        line = '{}     {:8.4f}  {:+8.4f}  {:6.2f}% '
        table += [line.format(row['tilename'], row['ra'].value, row['dec'].value, row['prob'] * 100)
                  for row in event.tile_table]
        table.append('```')

        msg = '\n'.join(title + details + table)

        send_slack_msg(msg)

    # Control functions
    def pause_listener(self):
        """Pause the alert listener."""
        if not self.listening:
            return 'Alert listener already pasued'

        self.log.info('Pausing alert listener')
        self.listening = False
        return 'Alert listener paused'

    def resume_listener(self):
        """Pause the alert listener."""
        if self.listening:
            return 'Alert listener already running'

        self.log.info('Resuming alert listener')
        self.listening = True
        return 'Alert listener resumed'


if __name__ == "__main__":
    daemon_id = 'sentinel'
    with misc.make_pid_file(daemon_id):
        SentinelDaemon()._run()
