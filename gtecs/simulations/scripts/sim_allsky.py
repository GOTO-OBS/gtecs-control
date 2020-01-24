#!/usr/bin/env python3
"""Simulate observing the all-sky survey.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import os
import warnings
from argparse import ArgumentParser, ArgumentTypeError

from astropy import units as u
from astropy.time import Time

from gototile.grid import SkyGrid

from gtecs import logger
from gtecs import params
from gtecs.simulations.database import prepare_database
from gtecs.simulations.misc import get_pointing_obs_details, get_sites
from gtecs.simulations.pilot import FakePilot


warnings.simplefilter("ignore", DeprecationWarning)


def run(start_date, system='GOTO-8', duration=1, sites='N', telescopes=1, verbose=False):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_allsky', log_stdout=False, log_to_file=True, log_to_stdout=verbose)

    # Oh, and another one, just in case
    fname = os.path.join(params.FILE_PATH, 'sim_allsky_output')
    with open(fname, 'a') as f:
        f.write(Time.now().iso + '\n')

    # Select the grid based on the system
    if system == 'GOTO-4':
        grid = SkyGrid(fov=(3.7, 4.9), overlap=(0.1, 0.1))
    elif system == 'GOTO-8':
        grid = SkyGrid(fov=(7.8, 5.1), overlap=(0.1, 0.1))
    else:
        raise ValueError('Invalid system: "{}"'.format(system))

    # Define the observing sites
    site_names = [name for name in sites.upper()]
    sites = get_sites(site_names)

    # Create output lists
    observed_tiles = []
    observed_times = []
    observed_airmasses = []
    observed_sites = []

    # If no start_time is given start tonight
    if start_date is None:
        start_date = Time.now()
    midnight = Time(start_date.strftime('%Y-%m-%d') + 'T00:00:00')

    # Create night start times
    start_times = [midnight + n * u.day for n in range(duration)]
    print('Simulating {} nights'.format(len(start_times)))

    # Prepare the ObsDB
    prepare_database(grid, clear=True, add_allsky=True, allsky_start_time=midnight)

    # Loop for given number of days
    for i, start_time in enumerate(start_times):
        sim_start_time = Time.now()

        # Write log line
        line = '{: >4}/{} :: {}: '.format(i + 1, len(start_times), start_time.strftime('%Y-%m-%d'))
        print(line, end='')
        with open(fname, 'a') as f:
            f.write(line)

        # Calculate the stop time
        stop_time = start_time + 1 * u.day

        # Create the pilot
        pilot = FakePilot(start_time, stop_time, sites, telescopes, quick=True, log=log)

        # Loop until the night is over
        pilot.observe()

        # Get completed pointings
        completed_pointings = pilot.all_completed_pointings
        completed_telescopes = pilot.all_completed_telescopes

        # Print results
        result = '{} tiles observed'.format(len(completed_pointings))
        dt = (Time.now() - sim_start_time).to(u.s).value
        result += ' :: t={:.1f}'.format(dt)
        print(result)
        with open(fname, 'a') as f:
            f.write(result + '\n')

        # Exit now if no tiles were observed
        if len(completed_pointings) == 0:
            return

        # Save details
        for pointing, telescope_id in zip(completed_pointings, completed_telescopes):
            site_id = pilot.sites_hosting_telescope[telescope_id]
            site = sites[site_id]
            site_name = site_names[site_id]

            tile, obs_time, airmass = get_pointing_obs_details(site, pointing)

            observed_tiles.append(tile)
            observed_times.append(obs_time.mjd)
            observed_airmasses.append(airmass)
            observed_sites.append(site_name)

    with open(fname, 'a') as f:
        f.write('start_times=' + str([time.mjd for time in start_times]) + '\n')
        f.write('observed_tiles=' + str(observed_tiles) + '\n')
        f.write('observed_times=' + str(observed_times) + '\n')
        f.write('observed_airmasses=' + str(observed_airmasses) + '\n')
        f.write('observed_sites=' + str(observed_sites) + '\n')
        f.write(Time.now().iso + '\n')

    print('-----')
    print('start_times:')
    print([time.mjd for time in start_times])
    print('observed_tiles:')
    print(observed_tiles)
    print('observed_times:')
    print(observed_times)
    print('observed_airmasses:')
    print(observed_airmasses)
    print('observed_sites:')
    print(observed_sites)

    print('-----')
    print('Simulations completed:')
    print('  total observations: {}'.format(len(observed_tiles)))


if __name__ == '__main__':
    def date_validator(date):
        """Validate dates."""
        try:
            date = Time(date)
        except ValueError:
            msg = "invalid date: '{}' not a recognised format".format(date)
            raise ArgumentTypeError(msg)
        return date

    parser = ArgumentParser(description='Simulate observations of the all-sky survey')
    parser.add_argument('date', type=date_validator, nargs='?',
                        help='simulation start date (default=now)',
                        )
    parser.add_argument('system', type=str, choices=['GOTO-4', 'GOTO-8'],
                        help='which telescope system to simulate',
                        )
    parser.add_argument('-d', '--duration', type=int, default=1,
                        help='number of days to simulate (default=1)'
                        )
    parser.add_argument('-s', '--sites', type=str, choices=['N', 'S', 'K', 'NS', 'NK'], default='N',
                        help=('which sites to simulate observing from '
                              '(N=La Palma, S=Siding Spring, K=Mt Kent, default=N)'),
                        )
    parser.add_argument('-t', '--telescopes', type=str, default='1',
                        help=('number of telescopes to observe with at each site '
                              '(e.g. "1", "2", "2,1", default=1)'),
                        )
    parser.add_argument('-v', '--verbose', action='store_true',
                        help=('print out more infomation'),
                        )
    args = parser.parse_args()

    date = args.date
    system = args.system
    duration = args.duration
    sites = args.sites
    if ',' in args.telescopes:
        telescopes = [int(telescope) for telescope in args.telescopes.split(',')]
    else:
        telescopes = int(args.telescopes)
    verbose = args.verbose

    run(date, system, duration, sites, telescopes, verbose)
