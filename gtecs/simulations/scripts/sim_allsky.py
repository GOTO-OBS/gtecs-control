#!/usr/bin/env python
"""Simulate observing the all-sky survey.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import os
import warnings
from argparse import ArgumentParser

from astropy import units as u
from astropy.time import Time

from gototile.grid import SkyGrid

from gtecs import logger
from gtecs import params
from gtecs.misc import NeatCloser
from gtecs.simulations.database import prepare_database
from gtecs.simulations.misc import get_sites
from gtecs.simulations.pilot import FakePilot

import numpy as np

import obsdb as db


warnings.simplefilter("ignore", DeprecationWarning)


class Closer(NeatCloser):
    """A class to neatly handle Ctrl-C requests before we've finished all the skymaps."""

    def __init__(self, n_target):
        super().__init__('')
        self.n_target = n_target

    def tidy_up(self):
        """Print logs."""
        with open(fname, 'a') as f:
            f.write('\n')
            f.write('start_times=' + str([time.mjd for time in start_times]) + '\n')
            f.write('tile_dict=' + str(tile_dict) + '\n')

        print('-----')
        print('start_times:')
        print([time.mjd for time in start_times])
        print('tile_dict:')
        print(tile_dict)

        n_complete = sum(sum([len(x) for x in tile_dict.values()]))
        print('-----')
        print('Simulations aborted early, {}/{} processed:'.format(n_complete, self.n_target))
        print('  total observations: {}'.format(sum([len(x) for x in tile_dict.values()])))
        print('      average visits: {:.2f}'.format(np.mean([len(x) for x in tile_dict.values()])))


def run(system='GOTO-8', duration=1, sites='N', telescopes=1):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_allsky', log_stdout=False, log_to_file=True, log_to_stdout=False)

    # Oh, and another one, just in case
    global fname
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

    # Create output dict
    global tile_dict
    tile_dict = {}

    # Create night start times
    global start_times
    start_times = [Time.now() + n * u.day for n in range(duration)]
    print('Simulating {} nights'.format(len(start_times)))

    # Print results if we exit early
    Closer(len(start_times))

    # Prepare the ObsDB
    prepare_database(grid, clear=True, add_allsky=True)

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
        completed_times = pilot.all_completed_times
        completed_telescopes = pilot.all_completed_telescopes

        # Print and plot results
        result = '{} tiles observed'.format(len(completed_pointings))
        dt = (Time.now() - sim_start_time).to(u.s).value
        result += ' :: t={:.1f}'.format(dt)
        print(result)
        with open(fname, 'a') as f:
            f.write(result + '\n')

        # Exit now if no tiles were observed
        if len(completed_pointings) == 0:
            return

        # Get observed tiles
        with db.open_session() as session:
            db_pointings = db.get_pointings(session, completed_pointings)
            # DB query will sort by id, need to resort into order of pointings
            db_pointings.sort(key=lambda db_pointing: completed_pointings.index(db_pointing.db_id))
            # Get tile name from grid tile
            completed_tiles = [p.grid_tile.name for p in db_pointings]

        # Add to the master dictionary
        for j, tile in enumerate(completed_tiles):
            obs_time = completed_times[j].mjd
            obs_site = site_names[pilot.sites_hosting_telescope[completed_telescopes[j]]]
            if tile in tile_dict:
                tile_dict[tile].append((i, j, obs_time, obs_site))
            else:
                tile_dict[tile] = [(i, j, obs_time, obs_site)]

    with open(fname, 'a') as f:
        f.write('start_times=' + str([time.mjd for time in start_times]) + '\n')
        f.write('tile_dict=' + str(tile_dict) + '\n')

    print('-----')
    print('start_times:')
    print([time.mjd for time in start_times])
    print('tile_dict:')
    print(tile_dict)

    print('-----')
    print('Simulations completed:')
    print('  total observations: {}'.format(sum([len(x) for x in tile_dict.values()])))
    print('      average visits: {:.2f}'.format(np.mean([len(x) for x in tile_dict.values()])))


if __name__ == "__main__":
    parser = ArgumentParser(description='Simulate observations of the all-sky survey')
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
    args = parser.parse_args()

    system = args.system
    duration = args.duration
    sites = args.sites
    if ',' in args.telescopes:
        telescopes = [int(telescope) for telescope in args.telescopes.split(',')]
    else:
        telescopes = int(args.telescopes)

    run(system, duration, sites, telescopes)
