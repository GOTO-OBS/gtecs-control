#!/usr/bin/env python
"""Simulate a night observing a given skymap, treating it as an LVC binary-black hole.

Where possible, this script uses the real G-TeCS scheduling, ObsDB database,
GOTO-alert event handling and GOTO-tile tiling functions to mimic the real system.

The only major fake code is the pilot itself, and we don't bother using the real fake hardware
daemons.
"""

import warnings
from argparse import ArgumentParser

from astropy import units as u

from gotoalert.alert import event_handler

from gototile.grid import SkyGrid
from gototile.skymap import SkyMap

from gtecs import logger
from gtecs.simulations.database import prepare_database
from gtecs.simulations.events import FakeEvent
from gtecs.simulations.misc import (get_pointing_obs_details, get_sites, get_source_tiles,
                                    get_visible_tiles, source_selected, source_visible)
from gtecs.simulations.pilot import FakePilot

import obsdb as db


warnings.simplefilter("ignore", DeprecationWarning)


def run(fits_path, system='GOTO-8', sites='N', telescopes=1):
    """Run the simulation."""
    # Create a log file
    log = logger.get_logger('sim_skymap', log_stdout=False, log_to_file=True, log_to_stdout=True)

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
    if not source_visible(event, grid, start_time, stop_time, sites):
        print('Source is not visible during given period')
        print('Exiting')
        return
    else:
        print('Source is visible during given period')

    # Handle the event
    # This should add tiles to the observation database, using the appropriate strategy.
    # It will select the "current" grid as the last one in the Grid table, which is why
    # prepare_database() up above will insert it if that's not the grid we want.
    event_handler(event, log=log)

    # Create the pilot
    pilot = FakePilot(start_time, stop_time, sites, telescopes, quick=True, log=log)

    # Loop until the night is over
    pilot.observe()

    # Get completed pointings
    completed_pointings = pilot.all_completed_pointings
    completed_telescopes = pilot.all_completed_telescopes

    # Print and plot results
    print('{} pointings completed'.format(len(completed_pointings)))
    if len(sites) > 1 or telescopes > 1:
        for telescope_id in pilot.telescope_ids:
            print('Telescope {} observed {} pointings'.format(
                  telescope_id + 1, len(pilot.completed_pointings[telescope_id])))
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
        first_obs = min([completed_tiles.index(tile)
                         for tile in source_tiles
                         if tile in completed_tiles])
        first_obs_pointing = completed_pointings[first_obs]

        # For the details we need to know which site observed it!
        # We know which telescope observed it from the pilot, and we can work out the site ID as
        # we know the number of telescopes we gave it.
        first_obs_telescope_id = completed_telescopes[first_obs]
        first_obs_site_id = pilot.sites_hosting_telescope[first_obs_telescope_id]
        first_obs_site = sites[first_obs_site_id]
        first_obs_site_name = site_names[first_obs_site_id]

        # Get the observation details from the database
        first_obs_details = get_pointing_obs_details(event, first_obs_site, first_obs_pointing)
        first_obs_tile = first_obs_details[0]
        first_obs_time = first_obs_details[1]
        first_obs_risetime = first_obs_details[2]
        first_obs_airmass = first_obs_details[3]

        # Only care about visible time past event time
        first_obs_visibletime = max(first_obs_risetime, event.time)

        print('Source was first observed in tile {}, pointing {} ({}/{})'.format(
            first_obs_tile, first_obs_pointing, first_obs + 1, len(completed_pointings)))

        if len(sites) > 1 or telescopes > 1:
            print('Source was first observed by telescope {} at site {}'.format(
                first_obs_telescope_id + 1, first_obs_site_name))

        print('Source was first observed at {}, {:.4f} hours after the event'.format(
            first_obs_time.iso, (first_obs_time - event.time).to(u.hour).value))

        print('Source was rose above the horizon at {}'.format(first_obs_risetime.iso))

        print('Source was first observed {:.4f} hours after becoming visible'.format(
            (first_obs_time - first_obs_visibletime).to(u.hour).value))

        print('Source was first observed at airmass {:.2f}'.format(first_obs_airmass))

    # Plot tiles on skymap
    grid.apply_skymap(event.skymap)
    visible_tiles = get_visible_tiles(event, grid, (start_time, stop_time), sites)
    notvisible_tiles = [tile for tile in grid.tilenames if tile not in visible_tiles]
    grid.plot(highlight=completed_tiles_unique,
              plot_skymap=True,
              plot_contours=True,
              color={tilename: '0.5' for tilename in notvisible_tiles},
              coordinates=event.source_coord,
              tilenames=source_tiles,
              )


if __name__ == "__main__":
    parser = ArgumentParser(description='Simulate observations of a skymap using the fake pilot')
    parser.add_argument('path', type=str,
                        help='path to the FITS skymap file',
                        )
    parser.add_argument('system', type=str, choices=['GOTO-4', 'GOTO-8'],
                        help='which telescope system to simulate',
                        )
    parser.add_argument('-s', '--sites', type=str, choices=['N', 'S', 'NS'], default='N',
                        help=('which sites to simulate observing from '
                              '(N=La Palma, S=Siding Spring, NS=both, default=N)'),
                        )
    parser.add_argument('-t', '--telescopes', type=str, default='1',
                        help=('number of telescopes to observe with at each site '
                              '(e.g. "1", "2", "2,1", default=1)'),
                        )
    args = parser.parse_args()

    path = args.path
    system = args.system
    sites = args.sites
    if ',' in args.telescopes:
        telescopes = [int(telescope) for telescope in args.telescopes.split(',')]
    else:
        telescopes = int(args.telescopes)

    run(path, system, sites, telescopes)
