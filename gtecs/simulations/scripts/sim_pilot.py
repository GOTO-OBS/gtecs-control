#!/usr/bin/env python3
"""A script simulate a night's run with the pilot."""

import warnings
from argparse import ArgumentParser, ArgumentTypeError

from astropy import units as u
from astropy.time import Time

from gtecs import logger
from gtecs.simulations.misc import get_sites
from gtecs.simulations.pilot import FakePilot


warnings.simplefilter("ignore", DeprecationWarning)


def run(start_time, duration=24, sites='N', telescopes=1):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_pilot', log_stdout=False, log_to_file=True, log_to_stdout=True)

    # If no start_time is given use tonight
    if start_time is None:
        start_time = Time.now()
    stop_time = start_time + duration * u.hour

    # Define the observing sites
    site_names = [name for name in sites.upper()]
    sites = get_sites(site_names)

    # Create the pilot
    pilot = FakePilot(start_time, stop_time, sites, telescopes, log=log)

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


if __name__ == '__main__':
    def date_validator(date):
        """Validate dates."""
        try:
            date = Time(date)
        except ValueError:
            msg = "invalid date: '{}' not a recognised format".format(date)
            raise ArgumentTypeError(msg)
        return date

    parser = ArgumentParser(description='Run the fake pilot for a night')
    parser.add_argument('date', type=date_validator, nargs='?',
                        help='simulation start date (default=now)',
                        )
    parser.add_argument('-d', '--duration', type=float, nargs='?', default=24,
                        help='time to simulate, in hours (default=24)'
                        )
    parser.add_argument('-s', '--sites', type=str, choices=['N', 'S', 'K', 'NS', 'NK'], default='N',
                        help=('which sites to simulate observing from '
                              '(N=La Palma, S=Siding Spring, K=Mt Kent, default=N)'),
                        )
    parser.add_argument('-t', '--telescopes', type=str, default='1',
                        help=('number of telescopes to observe with at each site '
                              '(e.g. "1", "2", "2,1", default=1)'),
                        )
    args = parser.parse_args()

    date = args.date
    duration = args.duration
    sites = args.sites
    if ',' in args.telescopes:
        telescopes = [int(telescope) for telescope in args.telescopes.split(',')]
    else:
        telescopes = int(args.telescopes)

    run(date, duration, sites, telescopes)
