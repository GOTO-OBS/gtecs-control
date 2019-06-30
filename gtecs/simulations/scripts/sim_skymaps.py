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
from astropy.time import Time

from gotoalert.alert import event_handler

from gototile.grid import SkyGrid
from gototile.skymap import SkyMap

from gtecs import logger
from gtecs import params
from gtecs.astronomy import observatory_location
from gtecs.misc import NeatCloser
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.misc import (get_pointing_obs_details, get_source_tiles,
                                    source_ever_visible, source_selected, source_visible)
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
            f.write('not_selected_events=' + str(not_selected_events) + '\n')
            f.write('never_visible_events=' + str(never_visible_events) + '\n')
            f.write('not_visible_events=' + str(not_visible_events) + '\n')
            f.write('not_observed_events=' + str(not_observed_events) + '\n')
            f.write('observed_events=' + str(observed_events) + '\n')
            f.write('observed_delta_event_times=' + str(observed_delta_event_times) + '\n')
            f.write('observed_delta_visible_times=' + str(observed_delta_visible_times) + '\n')
            f.write('observed_airmasses=' + str(observed_airmasses) + '\n')
            f.write(Time.now().iso + '\n')

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
        print('observed Dtes:')
        print(observed_delta_event_times)
        print('observed Dtvs:')
        print(observed_delta_visible_times)
        print('observed Ams:')
        print(observed_airmasses)

        n_complete = len(not_selected_events + not_visible_events + never_visible_events +
                         not_observed_events + observed_events)
        print('-----')
        print('Simulations aborted early, {}/{} processed:'.format(n_complete, self.n_target))
        print(' not_selected: {:4.0f}/{} ({:7.5f})'.format(len(not_selected_events), n_complete,
                                                           len(not_selected_events) / n_complete))
        print('never_visible: {:4.0f}/{} ({:7.5f})'.format(len(never_visible_events), n_complete,
                                                           len(never_visible_events) / n_complete))
        print('  not_visible: {:4.0f}/{} ({:7.5f})'.format(len(not_visible_events), n_complete,
                                                           len(not_visible_events) / n_complete))
        print(' not_observed: {:4.0f}/{} ({:7.5f})'.format(len(not_observed_events), n_complete,
                                                           len(not_observed_events) / n_complete))
        print('     observed: {:4.0f}/{} ({:7.5f})'.format(len(observed_events), n_complete,
                                                           len(observed_events) / n_complete))

        if len(observed_events) > 0:
            print('     mean Dte: {:8.5f} hours'.format(np.mean(observed_delta_event_times)))
            print('     mean Dtv: {:8.5f} hours'.format(np.mean(observed_delta_visible_times)))
            print('      mean Am: {:.3f} deg'.format(np.mean(observed_airmasses)))


def run(fits_direc, system='GOTO-8'):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_skymaps', log_stdout=False, log_to_file=True, log_to_stdout=False)

    # Oh, and another one, just in case
    global fname
    fname = os.path.join(params.FILE_PATH, 'sim_skymaps_output')
    with open(fname, 'a') as f:
        f.write(Time.now().iso + '\n')

    # Select the grid based on the system
    if system == 'GOTO-4':
        grid = SkyGrid(fov=(3.7, 4.9), overlap=(0.1, 0.1))
    elif system == 'GOTO-8':
        grid = SkyGrid(fov=(7.8, 5.1), overlap=(0.1, 0.1))
    else:
        raise ValueError('Invalid system: "{}"'.format(system))

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
    global observed_delta_event_times
    global observed_delta_visible_times
    global observed_airmasses
    not_selected_events = []
    not_visible_events = []
    never_visible_events = []
    not_observed_events = []
    observed_events = []
    observed_delta_event_times = []
    observed_delta_visible_times = []
    observed_airmasses = []

    # Print results if we exit early
    Closer(len(fits_files))

    # Loop through all files
    for i, fits_file in enumerate(fits_files):
        sim_start_time = Time.now()

        # Prepare the ObsDB
        prepare_database(grid, clear=True)

        # Load the skymap
        skymap = SkyMap.from_fits(os.path.join(fits_direc, fits_file))

        # Create the Event
        event = FakeEvent(skymap)
        event_id = event.id
        line = '{: >4}/{} :: Event {}: '.format(i + 1, len(fits_files), event_id)
        print(line, end='')
        with open(fname, 'a') as f:
            f.write(line)

        # Check if the source will be within the selected tiles
        # If not there's no point running through the simulation
        if not source_selected(event, grid):
            result = 'not_selected'
            dt = (Time.now() - sim_start_time).to(u.s).value
            result += ' :: t={:.1f}'.format(dt)
            print(result)
            with open(fname, 'a') as f:
                f.write(result + '\n')
            not_selected_events.append(event_id)
            continue

        # Set the simulation start and stop times
        start_time = event.time
        stop_time = start_time + 24 * u.hour

        # Check if the source will ever be visible from La Palma
        # If not there's no point running through the simulation
        if not source_ever_visible(event, grid):
            result = 'never_visible'
            dt = (Time.now() - sim_start_time).to(u.s).value
            result += ' :: t={:.1f}'.format(dt)
            print(result)
            with open(fname, 'a') as f:
                f.write(result + '\n')
            never_visible_events.append(event_id)
            continue

        # Check if the source will be visible during the given time
        # If not there's no point running through the simulation
        if not source_visible(event, grid, start_time, stop_time):
            result = 'not_visible'
            dt = (Time.now() - sim_start_time).to(u.s).value
            result += ' :: t={:.1f}'.format(dt)
            print(result)
            with open(fname, 'a') as f:
                f.write(result + '\n')
            not_visible_events.append(event_id)
            continue

        # Handle the event
        # This should add tiles to the observation database, using the appropriate strategy
        event_handler(event, log=log)

        # Create the pilot
        site = observatory_location()
        pilot = FakePilot(site, start_time, stop_time, quick=True, log=log)

        # Loop until the night is over
        pilot.observe()

        # Get completed pointings
        completed_pointings = pilot.completed_pointings[0]

        # Get observed tiles
        with db.open_session() as session:
            db_pointings = db.get_pointings(session, completed_pointings)
            # DB query will sort by id, need to resort into order of pointings
            db_pointings.sort(key=lambda db_pointing: completed_pointings.index(db_pointing.db_id))
            # Get tile name from grid tile
            completed_tiles = [p.grid_tile.name for p in db_pointings]

        # Get where the actual event was
        source_tiles = get_source_tiles(event, grid)
        source_observed = any(tile in completed_tiles for tile in source_tiles)
        if not source_observed:
            result = 'not_observed'
            dt = (Time.now() - sim_start_time).to(u.s).value
            result += ' :: t={:.1f}'.format(dt)
            print(result)
            with open(fname, 'a') as f:
                f.write(result + '\n')
            not_observed_events.append(event_id)
            continue
        else:
            # Print details
            first_index = min([completed_tiles.index(tile)
                               for tile in source_tiles
                               if tile in completed_tiles])
            first_pointing = completed_pointings[first_index]
            _, obs_time, rise_time, airmass = get_pointing_obs_details(event, site, first_pointing)
            delta_event_time = (obs_time - event.time).to(u.hour).value
            visible_time = max(event.time, rise_time)
            delta_visible_time = (obs_time - visible_time).to(u.hour).value
            result = 'OBSERVED (Dte={:.5f}, Dtv={:.5f}, Am={:.3f})'.format(
                delta_event_time, delta_visible_time, airmass)
            dt = (Time.now() - sim_start_time).to(u.s).value
            result += ' :: t={:.1f}'.format(dt)
            print(result)
            with open(fname, 'a') as f:
                f.write(result + '\n')

            # Store details
            observed_events.append(event_id)
            observed_delta_event_times.append(delta_event_time)
            observed_delta_visible_times.append(delta_visible_time)
            observed_airmasses.append(airmass)

            continue

    with open(fname, 'a') as f:
        f.write('not_selected_events=' + str(not_selected_events) + '\n')
        f.write('never_visible_events=' + str(never_visible_events) + '\n')
        f.write('not_visible_events=' + str(not_visible_events) + '\n')
        f.write('not_observed_events=' + str(not_observed_events) + '\n')
        f.write('observed_events=' + str(observed_events) + '\n')
        f.write('observed_delta_event_times=' + str(observed_delta_event_times) + '\n')
        f.write('observed_delta_visible_times=' + str(observed_delta_visible_times) + '\n')
        f.write('observed_airmasses=' + str(observed_airmasses) + '\n')
        f.write(Time.now().iso + '\n')

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
    print('observed Dtes:')
    print(observed_delta_event_times)
    print('observed Dtvs:')
    print(observed_delta_visible_times)
    print('observed Ams:')
    print(observed_airmasses)

    print('-----')
    print('Simulations completed:')
    print(' not_selected: {:4.0f}/{} ({:7.5f})'.format(len(not_selected_events), len(fits_files),
                                                       len(not_selected_events) / len(fits_files)))
    print('never_visible: {:4.0f}/{} ({:7.5f})'.format(len(never_visible_events), len(fits_files),
                                                       len(never_visible_events) / len(fits_files)))
    print('  not_visible: {:4.0f}/{} ({:7.5f})'.format(len(not_visible_events), len(fits_files),
                                                       len(not_visible_events) / len(fits_files)))
    print(' not_observed: {:4.0f}/{} ({:7.5f})'.format(len(not_observed_events), len(fits_files),
                                                       len(not_observed_events) / len(fits_files)))
    print('     observed: {:4.0f}/{} ({:7.5f})'.format(len(observed_events), len(fits_files),
                                                       len(observed_events) / len(fits_files)))

    if len(observed_events) > 0:
        print('     mean Dte: {:.5f} hours'.format(np.mean(observed_delta_event_times)))
        print('     mean Dtv: {:.5f} hours'.format(np.mean(observed_delta_visible_times)))
        print('      mean Am: {:.3f} deg'.format(np.mean(observed_airmasses)))


if __name__ == "__main__":
    description = 'Process skymaps using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('path', help='path to the FITS skymap files')
    parser.add_argument('system', choices=['GOTO-4', 'GOTO-8'],
                        help='which telescope system to simulate')
    args = parser.parse_args()

    run(args.path, args.system)
