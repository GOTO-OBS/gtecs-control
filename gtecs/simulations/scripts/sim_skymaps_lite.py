#!/usr/bin/env python
"""Find visability statistics for skymaps, without running through the whole simulation."""

import argparse
import os
import warnings

from astropy import units as u
from astropy.time import Time

from gototile.grid import SkyGrid
from gototile.skymap import SkyMap

from gtecs import params
from gtecs.misc import NeatCloser
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.misc import source_ever_visible, source_selected, source_visible


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
            f.write(Time.now().iso + '\n')

        n_complete = len(not_selected_events + not_visible_events + never_visible_events +
                         observable_events)
        print('-----')
        print('Simulations aborted early, {}/{} processed:'.format(n_complete, self.n_target))
        print(' not_selected: {}/{} ({:7.5f})'.format(len(not_selected_events), n_complete,
                                                      len(not_selected_events) / n_complete))
        print('never_visible: {}/{} ({:7.5f})'.format(len(never_visible_events), n_complete,
                                                      len(never_visible_events) / n_complete))
        print('  not_visible: {}/{} ({:7.5f})'.format(len(not_visible_events), n_complete,
                                                      len(not_visible_events) / n_complete))
        print('   observable: {}/{} ({:7.5f})'.format(len(observable_events), n_complete,
                                                      len(observable_events) / n_complete))

        print('-----')
        print('not_selected events:')
        print(not_selected_events)
        print('never_visible events:')
        print(never_visible_events)
        print('not_visible events:')
        print(not_visible_events)
        print('observable events:')
        print(observable_events)


def run(fits_direc):
    """Run the simulation."""
    # Create a log file
    global fname
    fname = os.path.join(params.FILE_PATH, 'sim_skymaps_lite_output')
    with open(fname, 'a') as f:
        f.write(Time.now().iso + '\n')

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
    global observable_events
    not_selected_events = []
    not_visible_events = []
    never_visible_events = []
    observable_events = []

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

        # The event must be observable
        result = 'observable'
        dt = (Time.now() - sim_start_time).to(u.s).value
        result += ' :: t={:.1f}'.format(dt)
        print(result)
        with open(fname, 'a') as f:
            f.write(result + '\n')
        observable_events.append(event_id)
        continue

    with open(fname, 'a') as f:
        f.write(Time.now().iso + '\n')

    print('-----')
    print('Simulations completed:')
    print(' not_selected: {}/{} ({:7.5f})'.format(len(not_selected_events), len(fits_files),
                                                  len(not_selected_events) / len(fits_files)))
    print('never_visible: {}/{} ({:7.5f})'.format(len(never_visible_events), len(fits_files),
                                                  len(never_visible_events) / len(fits_files)))
    print('  not_visible: {}/{} ({:7.5f})'.format(len(not_visible_events), len(fits_files),
                                                  len(not_visible_events) / len(fits_files)))
    print('   observable: {}/{} ({:7.5f})'.format(len(observable_events), len(fits_files),
                                                  len(observable_events) / len(fits_files)))

    print('-----')
    print('not_selected events:')
    print(not_selected_events)
    print('never_visible events:')
    print(never_visible_events)
    print('not_visible events:')
    print(not_visible_events)
    print('observable events:')
    print(observable_events)


if __name__ == "__main__":
    description = 'Process skymaps using the fake pilot to simulate a night of observations'
    parser = argparse.ArgumentParser(description=description,
                                     usage='python sim_skymaps_lite.py <path>')
    parser.add_argument('path',
                        help='path to the FITS skymap files')
    args = parser.parse_args()

    run(args.path)
