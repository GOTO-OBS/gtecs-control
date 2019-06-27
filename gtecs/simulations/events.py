"""Fake Event class to use with raw skymap files."""

from astropy.time import Time

from gotoalert.strategy import get_event_strategy


class FakeEvent(object):
    """A class to model an Event created from a VOEvent payload.

    All this Event needs at creating is a `gototile.skymap.SkyMap`.

    Since we can't use `gotoalert.events.Event` or its subclasses we need to make a
    fake class that can fool the event_handler.

    Right now this is optimised for the First Two Years simulated skymaps.
    """

    def __init__(self, skymap):
        # Define a fake, unique IVORN
        # The Time hack gets around the ObsDB requirement that Event IVORNs are unique
        self.ivorn = skymap.object + str(Time.now().mjd)

        # Set role as observation and interesting=True,
        # otherwise the event handler will reject it
        self.role = 'observation'
        self.interesting = True

        # Get the event time from the skymap
        self.time = skymap.date_det

        # Set the GCN packet type and other notice attributes
        self.packet_type = 151
        self.notice = 'LVC_INITIAL'
        self.type = 'GW'
        self.source = 'LVC'

        # Get the event id from the skymap
        self.id = skymap.header['event_id']
        self.name = '{}_{}'.format(self.source, self.id)

        # Fake the event as a BBH
        # A lot of the extra info like GraceDB URL is only used in Slack alerts,
        # so we can ignore them.
        # We only add the ones here that are needed to fool the strategy selection
        self.group = 'CBC'
        self.properties = {'HasNS': 1}

        # Save the skymap here, since it's already created
        self.skymap = skymap
        self.skymap_url = skymap.filename
        self.distance = skymap.header['distance']
        self.distance_error = 0

    def get_skymap(self, nside=128):
        """We already have the skymap, but the event handler expects this function."""
        return self.skymap

    def get_strategy(self):
        """Get the event observing strategy from the GOTO0-alert dicts."""
        self.strategy = get_event_strategy(self)
        return self.strategy
