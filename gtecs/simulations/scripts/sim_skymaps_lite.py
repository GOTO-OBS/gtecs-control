#!/usr/bin/env python
"""Find visability statistics for skymaps, without running through the whole simulation."""

import os
import warnings
from argparse import ArgumentParser

from astropy import units as u
from astropy.time import Time

from gototile.grid import SkyGrid
from gototile.skymap import SkyMap

from gtecs import params
from gtecs.misc import NeatCloser
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.misc import (get_sites, source_dark, source_ever_visible,
                                    source_selected, source_visible)


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
            f.write('not_visible_sun_events=' + str(not_visible_sun_events) + '\n')
            f.write('never_visible_events=' + str(never_visible_events) + '\n')
            f.write('not_visible_events=' + str(not_visible_events) + '\n')
            f.write('not_selected_events=' + str(not_selected_events) + '\n')
            f.write('visible_events=' + str(visible_events) + '\n')
            f.write('visible_sites=' + str(visible_sites) + '\n')
            f.write(Time.now().iso + '\n')

        print('-----')
        print('not_visible_sun events:')
        print(not_visible_sun_events)
        print('never_visible events:')
        print(never_visible_events)
        print('not_visible events:')
        print(not_visible_events)
        print('not_selected events:')
        print(not_selected_events)
        print('visible events:')
        print(visible_events)
        print('visible sites:')
        print(visible_sites)

        n_all = len(not_visible_sun_events +
                    never_visible_events +
                    not_visible_events +
                    not_selected_events +
                    visible_events)
        print('-----')
        print('not_visible_sun: {:4.0f}/{} ({:7.5f})'.format(
            len(not_visible_sun_events), n_all, len(not_visible_sun_events) / n_all))
        print('never_visible: {:4.0f}/{} ({:7.5f})'.format(
            len(never_visible_events), n_all, len(never_visible_events) / n_all))
        print('not_visible: {:4.0f}/{} ({:7.5f})'.format(
            len(not_visible_events), n_all, len(not_visible_events) / n_all))
        print('   not_selected: {:4.0f}/{} ({:7.5f})'.format(
            len(not_selected_events), n_all, len(not_selected_events) / n_all))
        print('        visible: {:4.0f}/{} ({:7.5f})'.format(
            len(visible_events), n_all, len(visible_events) / n_all))
        if len(set(visible_sites)) > 1:
            print('    site counts: {}'.format(', '.join(['{}:{}'.format(
                name, visible_sites.count(name)) for name in set(visible_sites)])))


def run(fits_direc, system='GOTO-8', duration=24, sites='N'):
    """Run the simulation."""
    # Create a log file
    global fname
    fname = os.path.join(params.FILE_PATH, 'sim_skymaps_lite_output')
    with open(fname, 'a') as f:
        f.write(Time.now().iso + '\n')

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

    # Find the files
    fits_files = os.listdir(fits_direc)
    fits_files = list(sorted(fits_files, key=lambda x: int(x.split('_')[0])))
    print('Processing {} skymaps'.format(len(fits_files)))

    # Create output lists
    global not_visible_sun_events
    global never_visible_events
    global not_visible_events
    global not_selected_events
    global visible_events
    global visible_sites
    not_visible_sun_events = []
    never_visible_events = []
    not_visible_events = []
    not_selected_events = []
    visible_events = []
    visible_sites = []

    # Print results if we exit early
    Closer(len(fits_files))

    # Loop through all files
    for i, fits_file in enumerate(fits_files):
        sim_start_time = Time.now()

        # Load the skymap
        skymap = SkyMap.from_fits(os.path.join(fits_direc, fits_file))

        # Create the Event
        event = FakeEvent(skymap)
        event_id = event.id
        line = '{: >4}/{} :: Event {}: '.format(i + 1, len(fits_files), event_id)
        print(line, end='')
        with open(fname, 'a') as f:
            f.write(line)

        # Set the simulation start and stop times
        start_time = event.time
        stop_time = start_time + duration * u.hour

        # Check if the source is too close to the Sun
        # If not there's no point running through the simulation
        if not source_dark(event, grid, start_time, stop_time):
            result = 'not_visible_sun'
            dt = (Time.now() - sim_start_time).to(u.s).value
            result += ' :: t={:.1f}'.format(dt)
            print(result)
            with open(fname, 'a') as f:
                f.write(result + '\n')
            not_visible_sun_events.append(event_id)
            continue

        # Check if the source is out of the dec range of the site
        # If not there's no point running through the simulation
        if not source_ever_visible(event, grid, sites):
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
        if not source_visible(event, grid, start_time, stop_time, sites):
            result = 'not_visible'
            dt = (Time.now() - sim_start_time).to(u.s).value
            result += ' :: t={:.1f}'.format(dt)
            print(result)
            with open(fname, 'a') as f:
                f.write(result + '\n')
            not_visible_events.append(event_id)
            continue

        # The event must be visible

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

        # Check which site(s) it's visible from
        visible_from = ''
        for site_id, site in enumerate(sites):
            if source_visible(event, grid, start_time, stop_time, site):
                visible_from += site_names[site_id]

        result = 'visible'
        if len(sites) > 1:
            result += (' (St={})'.format(visible_from))
        dt = (Time.now() - sim_start_time).to(u.s).value
        result += ' :: t={:.1f}'.format(dt)
        print(result)
        with open(fname, 'a') as f:
            f.write(result + '\n')
        visible_events.append(event_id)
        visible_sites.append(visible_from)
        continue

    with open(fname, 'a') as f:
        f.write('not_visible_sun_events=' + str(not_visible_sun_events) + '\n')
        f.write('never_visible_events=' + str(never_visible_events) + '\n')
        f.write('not_visible_events=' + str(not_visible_events) + '\n')
        f.write('not_selected_events=' + str(not_selected_events) + '\n')
        f.write('visible_events=' + str(visible_events) + '\n')
        f.write('visible_sites=' + str(visible_sites) + '\n')
        f.write(Time.now().iso + '\n')

    print('-----')
    print('not_visible_sun events:')
    print(not_visible_sun_events)
    print('never_visible events:')
    print(never_visible_events)
    print('not_visible events:')
    print(not_visible_events)
    print('not_selected events:')
    print(not_selected_events)
    print('visible events:')
    print(visible_events)
    print('visible sites:')
    print(visible_sites)

    print('-----')
    print('Simulations completed:')
    n_all = len(fits_files)
    print('not_visible_sun: {:4.0f}/{} ({:7.5f})'.format(
        len(not_visible_sun_events), n_all, len(not_visible_sun_events) / n_all))
    print('never_visible: {:4.0f}/{} ({:7.5f})'.format(
        len(never_visible_events), n_all, len(never_visible_events) / n_all))
    print('not_visible: {:4.0f}/{} ({:7.5f})'.format(
        len(not_visible_events), n_all, len(not_visible_events) / n_all))
    print('   not_selected: {:4.0f}/{} ({:7.5f})'.format(
        len(not_selected_events), n_all, len(not_selected_events) / n_all))
    print('        visible: {:4.0f}/{} ({:7.5f})'.format(
        len(visible_events), n_all, len(visible_events) / n_all))
    if len(set(visible_sites)) > 1:
        print('    site counts: {}'.format(', '.join(['{}:{}'.format(
            name, visible_sites.count(name)) for name in set(visible_sites)])))


if __name__ == "__main__":
    parser = ArgumentParser(description='Simulate observations of skymaps using the fake pilot')
    parser.add_argument('path', type=str,
                        help='path to the directory containing the FITS skymap files',
                        )
    parser.add_argument('system', type=str, choices=['GOTO-4', 'GOTO-8'],
                        help='which telescope system to simulate',
                        )
    parser.add_argument('-d', '--duration', type=float, default=24,
                        help='time to simulate, in hours (default=24)'
                        )
    parser.add_argument('-s', '--sites', type=str, choices=['N', 'S', 'K', 'NS', 'NK'], default='N',
                        help=('which sites to simulate observing from '
                              '(N=La Palma, S=Siding Spring, K=Mt Kent, default=N)'),
                        )
    args = parser.parse_args()

    path = args.path
    system = args.system
    duration = args.duration
    sites = args.sites

    run(path, system, duration, sites)
