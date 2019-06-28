#!/usr/bin/env python
"""A script simulate a night's run with the pilot."""

import argparse
import warnings

from astropy import units as u
from astropy.time import Time

from gototile.grid import SkyGrid

from gtecs import logger
from gtecs.astronomy import get_night_times
from gtecs.simulations.database import prepare_database
from gtecs.simulations.pilot import FakePilot


warnings.simplefilter("ignore", DeprecationWarning)


def run(date):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_pilot', log_stdout=False, log_to_file=True, log_to_stdout=True)

    # Hardcode the GOTO-4 grid, for now
    grid = SkyGrid(fov=(3.7, 4.9), overlap=(0.1, 0.1))

    # Prepare the ObsDB
    prepare_database(grid, clear=False)

    # If no date is given use tonight
    if date is None:
        date = Time.now()

    # Get sun rise and set times
    sunset, sunrise = get_night_times(date, horizon=-10 * u.deg)

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

    usage = 'python sim_pilot.py date'

    parser = argparse.ArgumentParser(description='Run the fake pilot for a night',
                                     usage=usage)
    parser.add_argument('date',
                        nargs='?',
                        default=None,
                        help='night starting date to simulate, default to tonight')
    args = parser.parse_args()

    run(args.date)
