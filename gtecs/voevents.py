"""VOEvent handlers."""

import os

from gotoalert.alert import event_handler

from six.moves.urllib.parse import quote_plus

import voeventparse as vp

from . import params


class Handler(object):
    """A class to generate VOEvent payload handlers.

    Needed so we can pass a log object to the handler without changing the PyGCN code.
    """

    def __init__(self, log):
        self.log = log

    def get_handler(self):
        """Create a handler function."""
        def handler(payload, root):
            """Payload handler that calls GOTO-alert's `event_handler`.

            It also archives the alert as a file in the config directory, based on PyGCN's
            default archive handler.
            """
            v = vp.loads(payload)
            ivorn = v.attrib['ivorn']
            filename = quote_plus(ivorn)
            alert_direc = params.CONFIG_PATH + 'voevents/'
            if not os.path.exists(alert_direc):
                os.mkdir(alert_direc)

            with open(alert_direc + filename, 'wb') as f:
                f.write(payload)
            self.log.info(ivorn)
            self.log.info('Archived to {}'.format(alert_direc))

            # Run GOTO-alert's event handler
            event_handler(payload, self.log, write_html=True, send_messages=False)

        return handler
