#!/usr/bin/env python
"""A script simulate a night's run with the pilot."""

import argparse
import warnings

from astropy import units as u
from astropy.time import Time

from gtecs import logger
from gtecs.astronomy import get_night_times, observatory_location
from gtecs.simulations.pilot import FakePilot


warnings.simplefilter("ignore", DeprecationWarning)


def run(date):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_pilot', log_stdout=False, log_to_file=True, log_to_stdout=True)

    # If no date is given use tonight
    if date is None:
        date = Time.now()

    # Get sun rise and set times
    sunset, sunrise = get_night_times(date, horizon=-10 * u.deg)

    # Create the pilot
    site = observatory_location()
    pilot = FakePilot(sunset, sunrise, site, log=log)

    # Loop until the night is over
    pilot.observe()

    # Get completed pointings
    completed_pointings = pilot.all_completed_pointings
    completed_times = pilot.all_completed_times
    aborted_pointings = pilot.all_aborted_pointings
    interrupted_pointings = pilot.all_interrupted_pointings

    # Print results
    print('{} pointings completed:'.format(len(completed_pointings)))
    for pointing_id, timedone in zip(completed_pointings, completed_times):
        print(pointing_id, timedone.iso)

    print('{} pointings aborted:'.format(len(aborted_pointings)))
    for pointing_id in aborted_pointings:
        print(pointing_id)

    print('{} pointings interrupted:'.format(len(interrupted_pointings)))
    for pointing_id in interrupted_pointings:
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
