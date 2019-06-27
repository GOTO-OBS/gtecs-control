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

from gototile import SkyGrid

from gtecs import logger
from gtecs.astronomy import get_night_times
from gtecs.simulations.database import prepare_database
from gtecs.simulations.misc import get_notvisible_tiles
from gtecs.simulations.pilot import FakePilot

import obsdb as db


warnings.simplefilter("ignore", DeprecationWarning)


def run(ivorn):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_event', log_stdout=False, log_to_file=True, log_to_stdout=True)

    # Hardcode the GOTO-4 grid, for now
    grid = SkyGrid(fov=(3.7, 4.9), overlap=(0.1, 0.1))

    # Prepare the ObsDB
    prepare_database(grid)

    # Create the Event
    event = Event.from_ivorn(ivorn)

    # Handle the event
    # This should add tiles to the observation database, using the appropriate strategy
    event_handler(event, log=log)

    # Get sun rise and set times
    sunset, sunrise = get_night_times(event.time, horizon=-10 * u.deg)

    # If the event occurs after sunset there's no reason to simulate the start of the night
    if event.time > sunset:
        start_time = event.time
    else:
        start_time = sunset
    stop_time = sunrise

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

        # Plot tiles on skymap
        grid.apply_skymap(event.skymap)
        notvisible_tiles = get_notvisible_tiles(event, start_time, stop_time)
        grid.plot(highlight=tiles,
                  plot_skymap=True,
                  plot_contours=True,
                  color={tilename: '0.5' for tilename in notvisible_tiles},
                  )


if __name__ == "__main__":
    description = 'Process an event using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_event.py <ivorn>')
    parser.add_argument('ivorn',
                        help='ivorn of the event to fetch fro nthe VOEvent database')
    args = parser.parse_args()

    run(args.ivorn)
