"""VOEvent handlers."""

import os

from astropy.time import Time

from six.moves.urllib.parse import quote_plus

import voeventparse as vp

from . import params


class Event(object):
    """A simple class to represent a single VOEvent."""

    def __init__(self, payload):
        self.creation_time = Time.now()
        self.payload = payload
        self.voevent = vp.loads(self.payload)
        self.ivorn = self.voevent.attrib['ivorn']
        self.filename = quote_plus(self.ivorn)

    def __repr__(self):
            return self.ivorn

    def archive(self, log=None):
        """Archive this event in the config directory."""
        self.alert_direc = params.CONFIG_PATH + 'voevents/'
        if not os.path.exists(self.alert_direc):
            os.mkdir(self.alert_direc)

        with open(self.alert_direc + self.filename, 'wb') as f:
            f.write(self.payload)

        if log:
            log.info('Archived to {}'.format(self.alert_direc))
