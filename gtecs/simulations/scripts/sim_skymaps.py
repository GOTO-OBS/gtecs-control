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
from gtecs.astronomy import get_night_times, observatory_location
from gtecs.misc import NeatCloser
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.misc import (get_source_tiles, source_ever_visible, source_selected,
                                    source_visible)
from gtecs.simulations.pilot import FakePilot

import obsdb as db


warnings.simplefilter("ignore", DeprecationWarning)


class Closer(NeatCloser):
    """A class to neatly handle Ctrl-C requests before we've finished all the skymaps."""

    def __init__(self, n_target):
        super().__init__('')
        self.n_target = n_target

    def tidy_up(self):
        """Cancel the pointing."""
        n_complete = len(not_selected_events + not_visible_events + never_visible_events +
                         not_observed_events + observed_events)
        print('-----')
        print('Simulations aborted early, {}/{} processed:'.format(n_complete, self.n_target))
        print(' not_selected: {}/{} ({:7.5f})'.format(len(not_selected_events), n_complete,
                                                      len(not_selected_events) / n_complete))
        print('never_visible: {}/{} ({:7.5f})'.format(len(never_visible_events), n_complete,
                                                      len(never_visible_events) / n_complete))
        print('  not_visible: {}/{} ({:7.5f})'.format(len(not_visible_events), n_complete,
                                                      len(not_visible_events) / n_complete))
        print(' not_observed: {}/{} ({:7.5f})'.format(len(not_observed_events), n_complete,
                                                      len(not_observed_events) / n_complete))
        print('     observed: {}/{} ({:7.5f})'.format(len(observed_events), n_complete,
                                                      len(observed_events) / n_complete))

        print('-----')
        print('not_selected events:')
        print(not_selected_events)
        print('never_visible events:')
        print(never_visible_events)
        print('not_visible events:')
        print(not_visible_events)
        print('not_observed events:')
        print(not_observed_events)
        print('observed events:')
        print(observed_events)


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
    global not_selected_events
    global not_visible_events
    global never_visible_events
    global not_observed_events
    global observed_events
    not_selected_events = []
    not_visible_events = []
    never_visible_events = []
    not_observed_events = []
    observed_events = []

    # Loop through all files
    for i, fits_file in enumerate(fits_files):
        # Print results if we exit early
        Closer(len(fits_files))

        # Prepare the ObsDB
        prepare_database(grid, clear=True)

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

        # We're going to observe it
        print('observing...', end=' ')

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

        # Get observed tiles
        with db.open_session() as session:
            db_pointings = db.get_pointings(session, completed_pointings)
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
    print('Simulations completed:')
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

    print('-----')
    print('not_selected events:')
    print(not_selected_events)
    print('never_visible events:')
    print(never_visible_events)
    print('not_visible events:')
    print(not_visible_events)
    print('not_observed events:')
    print(not_observed_events)
    print('observed events:')
    print(observed_events)


if __name__ == "__main__":
    description = 'Process skymaps using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_skymap.py <path>')
    parser.add_argument('path',
                        help='path to the FITS skymap files')
    args = parser.parse_args()

    run(args.path)
