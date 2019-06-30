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

from gototile.grid import SkyGrid

from gtecs import logger
from gtecs.astronomy import observatory_location
from gtecs.simulations.database import prepare_database
from gtecs.simulations.misc import get_visible_tiles
from gtecs.simulations.pilot import FakePilot

import obsdb as db


warnings.simplefilter("ignore", DeprecationWarning)


def run(ivorn, system='GOTO-8'):
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
    stop_time = start_time + 24 * u.hour

    # Create the pilot
    site = observatory_location()
    pilot = FakePilot(start_time, stop_time, site, log=log)

    # Loop until the night is over
    pilot.observe()

    # Get completed pointings
    completed_pointings = pilot.all_completed_pointings

    # Print and plot results
    print('{} pointings completed'.format(len(completed_pointings)))
    if len(completed_pointings) == 0:
        print('Did not observe any pointings')
        print('Exiting')
        return

    # Get all observed tiles
    with db.open_session() as session:
        db_pointings = db.get_pointings(session, completed_pointings)
        all_tiles = [p.grid_tile.name for p in db_pointings]

    # Account for multiple observations of the same tile
    observed_tiles = list(set(all_tiles))
    print('{} unique tiles covered:'.format(len(observed_tiles)))
    for tile in observed_tiles:
        print('{} observed {} time(s)'.format(tile, all_tiles.count(tile)))

    # Plot tiles on skymap
    grid.apply_skymap(event.skymap)
    visible_tiles = get_visible_tiles(event, grid, (start_time, stop_time))
    notvisible_tiles = [tile for tile in grid.tilenames if tile not in visible_tiles]
    grid.plot(highlight=observed_tiles,
              plot_skymap=True,
              plot_contours=True,
              color={tilename: '0.5' for tilename in notvisible_tiles},
              )


if __name__ == "__main__":
    description = 'Process an event using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_event.py <ivorn>')
    parser.add_argument('ivorn', help='ivorn of the event to fetch from the VOEvent database')
    parser.add_argument('system', choices=['GOTO-4', 'GOTO-8'],
                        help='which telescope system to simulate')
    args = parser.parse_args()

    run(args.ivorn, args.system)
