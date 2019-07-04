#!/usr/bin/env python
"""Simulate a night observing a given event.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import warnings
from argparse import ArgumentParser

from astropy import units as u

from gotoalert.alert import event_handler
from gotoalert.events import Event

from gototile.grid import SkyGrid

from gtecs import logger
from gtecs.simulations.database import prepare_database
from gtecs.simulations.misc import get_sites, get_visible_tiles
from gtecs.simulations.pilot import FakePilot

import obsdb as db


warnings.simplefilter("ignore", DeprecationWarning)


def run(ivorn, system='GOTO-8', duration=24, sites='N', telescopes=1):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_event', log_stdout=False, log_to_file=True, log_to_stdout=True)

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

    # Prepare the ObsDB
    prepare_database(grid, clear=True)

    # Create the Event
    event = Event.from_ivorn(ivorn)
    print('Processing skymap for Event {}'.format(event.name))

    # Handle the event
    # This should add tiles to the observation database, using the appropriate strategy.
    # It will select the "current" grid as the last one in the Grid table, which is why
    # prepare_database() up above will insert it if that's not the grid we want.
    event_handler(event, log=log)

    # Set the simulation start and stop times
    start_time = event.time
    stop_time = start_time + duration * u.hour

    # Create the pilot
    pilot = FakePilot(start_time, stop_time, sites, telescopes, log=log)

    # Loop until the night is over
    pilot.observe()

    # Get completed pointings
    completed_pointings = pilot.all_completed_pointings

    # Print and plot results
    print('{} pointings completed'.format(len(completed_pointings)))
    if len(sites) > 1 or telescopes > 1:
        for telescope_id in pilot.telescope_ids:
            print('Telescope {} observed {} pointings'.format(
                  telescope_id + 1, len(pilot.completed_pointings[telescope_id])))
    if len(completed_pointings) == 0:
        print('Did not observe any pointings')
        print('Exiting')
        return

    # Get observed tiles
    with db.open_session() as session:
        db_pointings = db.get_pointings(session, completed_pointings)
        # DB query will sort by id, need to resort into order of pointings
        db_pointings.sort(key=lambda db_pointing: completed_pointings.index(db_pointing.db_id))
        # Get tile name from grid tile
        completed_tiles = [p.grid_tile.name for p in db_pointings]

    # Account for multiple observations of the same tile
    completed_tiles_unique = list(set(completed_tiles))
    print('{} unique tiles covered:'.format(len(completed_tiles_unique)))
    for tile in sorted(completed_tiles_unique):
        print('{} observed {} time(s)'.format(tile, completed_tiles.count(tile)))

    # Plot tiles on skymap
    grid.apply_skymap(event.skymap)
    visible_tiles = get_visible_tiles(event, grid, (start_time, stop_time), sites)
    notvisible_tiles = [tile for tile in grid.tilenames if tile not in visible_tiles]
    grid.plot(highlight=completed_tiles_unique,
              plot_skymap=True,
              plot_contours=True,
              color={tilename: '0.5' for tilename in notvisible_tiles},
              )


if __name__ == "__main__":
    parser = ArgumentParser(description='Simulate observations of an event using the fake pilot')
    parser.add_argument('ivorn', type=str,
                        help='ivorn of the event to fetch from the VOEvent database',
                        )
    parser.add_argument('system', type=str, choices=['GOTO-4', 'GOTO-8'],
                        help='which telescope system to simulate',
                        )
    parser.add_argument('-d', '--duration', type=float, default=24,
                        help='time to simulate, in hours (default=24)'
                        )
    parser.add_argument('-s', '--sites', type=str, choices=['N', 'S', 'NS'], default='N',
                        help=('which sites to simulate observing from '
                              '(N=La Palma, S=Siding Spring, NS=both, default=N)'),
                        )
    parser.add_argument('-t', '--telescopes', type=str, default='1',
                        help=('number of telescopes to observe with at each site '
                              '(e.g. "1", "2", "2,1", default=1)'),
                        )
    args = parser.parse_args()

    ivorn = args.ivorn
    system = args.system
    duration = args.duration
    sites = args.sites
    if ',' in args.telescopes:
        telescopes = [int(telescope) for telescope in args.telescopes.split(',')]
    else:
        telescopes = int(args.telescopes)

    run(ivorn, system, duration, sites, telescopes)
