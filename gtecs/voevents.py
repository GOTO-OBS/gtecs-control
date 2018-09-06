"""VOEvent handlers."""

import os

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
            """Payload handler that archives VOEvent messages as files in the config directory.

            Based on PyGCN's default archive handler:
            https://github.com/lpsinger/pygcn/blob/master/gcn/handlers.py
            """
            v = vp.loads(payload)
            ivorn = v.attrib['ivorn']
            filename = quote_plus(ivorn)
            alert_direc = params.CONFIG_PATH + 'voevents/'
            if not os.path.exists(alert_direc):
                os.mkdir(alert_direc)

            with open(alert_direc + filename, 'wb') as f:
                f.write(payload)

            role = v.attrib['role']
            ra = vp.get_event_position(v).ra
            dec = vp.get_event_position(v).dec
            self.log.info('ivorn={}, role={}, ra={}, dec={}'.format(ivorn, role, ra, dec))
            self.log.info('Archived to {}'.format(alert_direc))

        return handler
