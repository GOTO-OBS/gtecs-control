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
from astropy.coordinates import SkyCoord

from gotoalert.alert import event_handler

from gototile.grid import SkyGrid
from gototile.skymap import SkyMap

from gtecs import logger
from gtecs.astronomy import get_night_times
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.misc import get_visible_tiles, source_selected, source_visible
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
    prepare_database(grid)

    # Load the skymap
    skymap = SkyMap.from_fits(fits_path)

    # Create the Event
    event = FakeEvent(skymap)

    # Get sun rise and set times
    sunset, sunrise = get_night_times(event.time, horizon=-10 * u.deg)

    # If the event occurs after sunset there's no reason to simulate the start of the night
    if event.time > sunset:
        start_time = event.time
    else:
        start_time = sunset
    stop_time = sunrise

    print('Processing skymap for Event {} from {} to {}'.format(event.name,
                                                                start_time.iso,
                                                                stop_time.iso))

    # Check if the source will be within the selected tiles
    # If not there's no point running through the simulation
    if not source_selected(event, grid):
        print('Source is not in any selected tiles')
        print('Exiting')
        return
    else:
        print('Source is within selected tiles')

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
    pilot = FakePilot(start_time, stop_time, log=log)

    # Loop until the night is over
    pilot.observe()

    # Print and plot results
    print('{} pointings completed'.format(len(pilot.completed_pointings)))

    if len(pilot.completed_pointings) > 0:
        # Get grid and tiles
        with db.open_session() as session:
            db_pointings = db.get_pointings(session, pilot.completed_pointings)
            all_tiles = [p.grid_tile.name for p in db_pointings]

        # Account for multiple observations of the same tile
        tiles = list(set(all_tiles))
        print('{} tiles covered:'.format(len(tiles)))
        for tile in tiles:
            print('{} observed {} time(s)'.format(tile, all_tiles.count(tile)))

        # Get where the actual event was
        source = SkyCoord(skymap.header['source_ra'], skymap.header['source_dec'], unit='deg')
        source_tiles = grid.get_tile(source, overlap=True)
        print('Source was in tiles:', source_tiles)
        source_observed = any(tile in tiles for tile in source_tiles)
        print('Source observed?:', source_observed)

        # Plot tiles on skymap
        grid.apply_skymap(event.skymap)
        visible_tiles = get_visible_tiles(event, grid, start_time, stop_time)
        notvisible_tiles = [tile for tile in grid.tilenames if tile not in visible_tiles]
        grid.plot(highlight=tiles,
                  plot_skymap=True,
                  plot_contours=True,
                  color={tilename: '0.5' for tilename in notvisible_tiles},
                  coordinates=source,
                  )


if __name__ == "__main__":
    description = 'Process a skymap using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_skymap.py <path>')
    parser.add_argument('path',
                        help='path to the FITS skymap file')
    args = parser.parse_args()

    run(args.path)
