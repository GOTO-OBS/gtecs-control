#!/usr/bin/env python
"""Simulate a night observing a given skymap, treating it as an LVC binary-black hole.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import argparse
import warnings

from astropy import units as u

from gotoalert.alert import event_handler

from gototile.skymap import SkyMap

from gtecs import logger
from gtecs.astronomy import get_night_times
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.pilot import FakePilot


warnings.simplefilter("ignore", DeprecationWarning)


def run(fits_path):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_skymap', log_stdout=False, log_to_file=True, log_to_stdout=True)

    # Prepare the ObsDB
    prepare_database()

    # Load the skymap
    skymap = SkyMap.from_fits(fits_path)

    # Create the Event
    event = FakeEvent(skymap)

    # Handle the event
    # This should add tiles to the observation database, using the appropriate strategy
    event_handler(event, log=log)

    # Get sun rise and set times
    sunset, sunrise = get_night_times(event.time, horizon=-10 * u.deg)

    # If the event occurs after sunset there's no reason to simulate the start of the night
    if event.time > sunset:
        start_time = event.time
    else:
        start_time = sunset

    # Create the pilot
    pilot = FakePilot(start_time=start_time, stop_time=sunrise, log=log)

    # Loop until the night is over
    pilot.observe()

    # Print results
    print('{} pointings completed:'.format(len(pilot.completed_pointings)))
    for pointing_id, timedone in zip(pilot.completed_pointings, pilot.completed_times):
        print(pointing_id, timedone.iso)

    print('{} pointings aborted:'.format(len(pilot.aborted_pointings)))
    for pointing_id in pilot.aborted_pointings:
        print(pointing_id)

    print('{} pointings interrupted:'.format(len(pilot.interrupted_pointings)))
    for pointing_id in pilot.interrupted_pointings:
        print(pointing_id)


if __name__ == "__main__":
    description = 'Process a skymap using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_skymap.py <path>')
    parser.add_argument('path',
                        help='path to the FITS skymap file')
    args = parser.parse_args()

    run(args.path)
