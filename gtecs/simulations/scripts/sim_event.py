#!/usr/bin/env python
"""Simulate a night observing a given event.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import argparse
import warnings

from gotoalert.alert import event_handler
from gotoalert.events import Event

from gtecs.simulations.pilot import run as run_pilot


warnings.simplefilter("ignore", DeprecationWarning)


def run(ivorn):
    """Run the simulation."""
    # Create the Event
    event = Event.from_ivorn(ivorn)

    # Handle the event
    # This should add tiles to the observation database, using the appropriate strategy
    event_handler(event)

    # Get the night to simulate
    date = event.time.strftime('%Y-%m-%d')

    # Run the fake pilot for that night
    run_pilot(date)


if __name__ == "__main__":
    description = 'Process an event using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_skymap.py <ivorn>')
    parser.add_argument('ivorn',
                        help='ivorn of the event to fetch fro nthe VOEvent database')
    args = parser.parse_args()

    run(args.ivorn)
