#!/usr/bin/env python
"""Simulate a night observing a given event.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import argparse
import warnings

from astropy import units as u

from gotoalert.alert import event_handler
from gotoalert.events import Event

from gtecs import logger
from gtecs.astronomy import get_night_times
from gtecs.simulations.database import prepare_database
from gtecs.simulations.pilot import FakePilot


warnings.simplefilter("ignore", DeprecationWarning)


def run(ivorn):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_event', log_stdout=False, log_to_file=True, log_to_stdout=True)

    # Prepare the ObsDB
    prepare_database()

    # Create the Event
    event = Event.from_ivorn(ivorn)

    # Handle the event
    # This should add tiles to the observation database, using the appropriate strategy
    event_handler(event, log=log)

    # Get sun rise and set times
    sunset, sunrise = get_night_times(event.time, horizon=-10 * u.deg)

    # Create the pilot
    pilot = FakePilot(start_time=sunset, stop_time=sunrise, log=log)

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
    description = 'Process an event using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_event.py <ivorn>')
    parser.add_argument('ivorn',
                        help='ivorn of the event to fetch fro nthe VOEvent database')
    args = parser.parse_args()

    run(args.ivorn)
