#!/usr/bin/env python
"""Simulate a night observing a given skymap, treating it as an LVC binary-black hole.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import argparse
import warnings

from gotoalert.alert import event_handler

from gototile.skymap import SkyMap

from gtecs import logger
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.pilot import run as run_pilot


warnings.simplefilter("ignore", DeprecationWarning)


def run(fits_path):
    """Run the simulation."""
    # Prepare the ObsDB
    prepare_database()

    # Load the skymap
    skymap = SkyMap.from_fits(fits_path)

    # Create the Event
    event = FakeEvent(skymap)

    # Create a log file
    log = logger.get_logger('sim_skymap', log_stdout=False, log_to_file=True, log_to_stdout=True)

    # Handle the event
    # This should add tiles to the observation database, using the appropriate strategy
    event_handler(event, log=log)

    # Get the night to simulate
    date = event.time.strftime('%Y-%m-%d')

    # Run the fake pilot for that night
    run_pilot(date, log=log)


if __name__ == "__main__":
    description = 'Process a skymap using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_skymap.py <path>')
    parser.add_argument('path',
                        help='path to the FITS skymap file')
    args = parser.parse_args()

    run(args.path)
