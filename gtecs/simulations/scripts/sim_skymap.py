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

from gototile.grid import SkyGrid
from gototile.skymap import SkyMap

from gtecs import logger
from gtecs.astronomy import observatory_location
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.misc import (get_source_tiles, get_visible_tiles,
                                    source_selected, source_visible)
from gtecs.simulations.pilot import FakePilot

import obsdb as db


warnings.simplefilter("ignore", DeprecationWarning)


def run(fits_path):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_skymap', log_stdout=False, log_to_file=True, log_to_stdout=True)

    # Hardcode the GOTO-4 grid, for now
    grid = SkyGrid(fov=(3.7, 4.9), overlap=(0.1, 0.1))

    # Prepare the ObsDB
    prepare_database(grid, clear=True)

    # Load the skymap
    skymap = SkyMap.from_fits(fits_path)

    # Create the Event
    event = FakeEvent(skymap)
    print('Processing skymap for Event {}'.format(event.name))

    # Check if the source will be within the selected tiles
    # If not there's no point running through the simulation
    if not source_selected(event, grid):
        print('Source is not in any selected tiles')
        print('Exiting')
        return
    else:
        print('Source is within selected tiles')

    # Set the simulation start and stop times
    start_time = event.time
    stop_time = start_time + 24 * u.hour

    # Check if the source will be visible during the given time
    # If not there's no point running through the simulation
    if not source_visible(event, grid, start_time, stop_time):
        print('Source is not visible during given period')
        print('Exiting')
        return
    else:
        print('Source is visible during given period')

    # Handle the event
    # This should add tiles to the observation database, using the appropriate strategy
    event_handler(event, log=log)

    # Create the pilot
    site = observatory_location()
    pilot = FakePilot(site, start_time, stop_time, log=log)

    # Loop until the night is over
    pilot.observe()

    # Get completed pointings
    completed_pointings = pilot.completed_pointings[0]

    # Print and plot results
    print('{} pointings completed'.format(len(completed_pointings)))
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
        all_tiles = [p.grid_tile.name for p in db_pointings]

    # Account for multiple observations of the same tile
    observed_tiles = list(set(all_tiles))
    print('{} unique tiles covered:'.format(len(observed_tiles)))
    for tile in observed_tiles:
        print('{} observed {} time(s)'.format(tile, all_tiles.count(tile)))

    # Get where the actual event was
    source_tiles = get_source_tiles(event, grid)
    print('Source was within {} tile(s):'.format(len(source_tiles)), ', '.join(source_tiles))
    source_observed = any(tile in observed_tiles for tile in source_tiles)
    print('Source observed?:', source_observed)
    if source_observed:
        print('Source was observed {} times'.format(sum([all_tiles.count(tile)
                                                         for tile in source_tiles])))

    # Plot tiles on skymap
    grid.apply_skymap(event.skymap)
    visible_tiles = get_visible_tiles(event, grid, (start_time, stop_time))
    notvisible_tiles = [tile for tile in grid.tilenames if tile not in visible_tiles]
    grid.plot(highlight=observed_tiles,
              plot_skymap=True,
              plot_contours=True,
              color={tilename: '0.5' for tilename in notvisible_tiles},
              coordinates=event.source_coord,
              tilenames=source_tiles,
              )


if __name__ == "__main__":
    description = 'Process a skymap using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_skymap.py <path>')
    parser.add_argument('path',
                        help='path to the FITS skymap file')
    args = parser.parse_args()

    run(args.path)
