#!/usr/bin/env python
"""Simulate a night observing a given skymap, treating it as an LVC binary-black hole.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import argparse
import warnings

from astroplan import Observer

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

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
        completed_tiles = [p.grid_tile.name for p in db_pointings]

    # Account for multiple observations of the same tile
    completed_tiles_unique = list(set(completed_tiles))
    print('{} unique tiles covered:'.format(len(completed_tiles_unique)))
    for tile in completed_tiles_unique:
        print('{} observed {} time(s)'.format(tile, completed_tiles.count(tile)))

    # Get where the actual event was
    source_tiles = get_source_tiles(event, grid)
    print('Source was within {} tile(s):'.format(len(source_tiles)), ', '.join(source_tiles))
    source_observed = any(tile in completed_tiles for tile in source_tiles)
    print('Source observed?:', source_observed)
    if source_observed:
        print('Source was observed {} times'.format(sum([completed_tiles.count(tile)
                                                         for tile in source_tiles])))

        # We care about the first time it was observed, which should be first in the list
        first_obs = min([completed_tiles.index(tile) for tile in source_tiles])
        first_obs_pointing = completed_pointings[first_obs]
        first_obs_tile = completed_tiles[first_obs]
        print('Source was first observed in tile {}, pointing {} ({}/{})'.format(
            first_obs_tile, first_obs_pointing, first_obs + 1, len(completed_pointings)))

        # Get time and position from the database
        with db.open_session() as session:
            first_obs_db_pointing = db.get_pointing_by_id(session, first_obs_pointing)
            first_obs_time = Time(first_obs_db_pointing.stopped_time)
            first_obs_coord = SkyCoord(first_obs_db_pointing.ra,
                                       first_obs_db_pointing.dec,
                                       unit='deg')
        print('Source was first observed at {}, {:.4f} hours after the event'.format(
            first_obs_time.iso, (first_obs_time - event.time).to(u.hour)))

        # Get the altaz and airmass at the time it was observed
        observer = Observer(site)
        first_obs_altaz = observer.altaz(first_obs_time, first_obs_coord)
        print('Source was first observed at altitude {:.3f} deg'.format(first_obs_altaz.alt.value))
        print('Source was first observed at airmass {:.2f}'.format(first_obs_altaz.secz.value))

    # Plot tiles on skymap
    grid.apply_skymap(event.skymap)
    visible_tiles = get_visible_tiles(event, grid, (start_time, stop_time))
    notvisible_tiles = [tile for tile in grid.tilenames if tile not in visible_tiles]
    grid.plot(highlight=completed_tiles_unique,
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
