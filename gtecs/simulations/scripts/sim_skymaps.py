#!/usr/bin/env python
"""Simulate a night observing multiple skymaps, to find statistics.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import argparse
import os
import warnings

from astropy import units as u

from gotoalert.alert import event_handler

from gototile.grid import SkyGrid
from gototile.skymap import SkyMap

from gtecs import logger
from gtecs.astronomy import get_night_times
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.misc import (get_source_tiles, source_ever_visible, source_selected,
                                    source_visible)
from gtecs.simulations.pilot import FakePilot

import obsdb as db


warnings.simplefilter("ignore", DeprecationWarning)


def run(fits_direc):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_skymaps', log_stdout=False, log_to_file=True, log_to_stdout=False)

    # Hardcode the GOTO-4 grid, for now
    grid = SkyGrid(fov=(3.7, 4.9), overlap=(0.1, 0.1))

    # Find the files
    fits_files = os.listdir(fits_direc)
    fits_files = list(sorted(fits_files, key=lambda x: int(x.split('_')[0])))
    print('Processing {} skymaps'.format(len(fits_files)))

    # Create output lists
    not_selected_events = []
    not_visible_events = []
    never_visible_events = []
    not_observed_events = []
    observed_events = []

    # Loop through all files
    for i, fits_file in enumerate(fits_files[0:6]):
        # Prepare the ObsDB
        prepare_database(grid)

        # Load the skymap
        skymap = SkyMap.from_fits(os.path.join(fits_direc, fits_file))

        # Create the Event
        event = FakeEvent(skymap)
        event_id = event.id
        print('{: >4}/{} :: Event {}:'.format(i + 1, len(fits_files), event_id), end=' ')

        # Get sun rise and set times
        sunset, sunrise = get_night_times(event.time, horizon=-10 * u.deg)

        # If the event occurs after sunset there's no reason to simulate the start of the night
        if event.time > sunset:
            start_time = event.time
        else:
            start_time = sunset
        stop_time = sunrise

        # Check if the source will be within the selected tiles
        # If not there's no point running through the simulation
        if not source_selected(event, grid):
            print('not_selected')
            not_selected_events.append(event_id)
            continue

        # Check if the source will ever be visible from La Palma
        # If not there's no point running through the simulation
        if not source_ever_visible(event, grid):
            print('never_visible')
            never_visible_events.append(event_id)
            continue

        # Check if the source will be visible during the given time
        # If not there's no point running through the simulation
        if not source_visible(event, grid, start_time, stop_time):
            print('not_visible')
            not_visible_events.append(event_id)
            continue

        # Handle the event
        # This should add tiles to the observation database, using the appropriate strategy
        event_handler(event, log=log)

        # Create the pilot
        pilot = FakePilot(start_time, stop_time, log=log)

        # Loop until the night is over
        pilot.observe()

        # Get observed tiles
        with db.open_session() as session:
            db_pointings = db.get_pointings(session, pilot.completed_pointings)
            all_tiles = [p.grid_tile.name for p in db_pointings]

        # Account for multiple observations of the same tile
        observed_tiles = list(set(all_tiles))

        # Get where the actual event was
        source_tiles = get_source_tiles(event, grid)
        source_observed = any(tile in observed_tiles for tile in source_tiles)
        if not source_observed:
            print('not_observed')
            not_observed_events.append(event_id)
            continue
        else:
            print('OBSERVED')
            observed_events.append(event_id)
            continue

    print('-----')
    print(' not_selected: {}/{} ({:7.5f})'.format(len(not_selected_events), len(fits_files),
                                                  len(not_selected_events) / len(fits_files)))
    print('never_visible: {}/{} ({:7.5f})'.format(len(never_visible_events), len(fits_files),
                                                  len(never_visible_events) / len(fits_files)))
    print('  not_visible: {}/{} ({:7.5f})'.format(len(not_visible_events), len(fits_files),
                                                  len(not_visible_events) / len(fits_files)))
    print(' not_observed: {}/{} ({:7.5f})'.format(len(not_observed_events), len(fits_files),
                                                  len(not_observed_events) / len(fits_files)))
    print('     observed: {}/{} ({:7.5f})'.format(len(observed_events), len(fits_files),
                                                  len(observed_events) / len(fits_files)))


if __name__ == "__main__":
    description = 'Process skymaps using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_skymap.py <path>')
    parser.add_argument('path',
                        help='path to the FITS skymap files')
    args = parser.parse_args()

    run(args.path)
