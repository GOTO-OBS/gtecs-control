#!/usr/bin/env python
"""Simulate observing the all-sky survey."""

import os
import warnings
from argparse import ArgumentParser, ArgumentTypeError

from astropy import units as u
from astropy.coordinates import AltAz, get_sun
from astropy.time import Time

from gototile.grid import SkyGrid

from gtecs import params
from gtecs.simulations.misc import get_sites

import numpy as np


warnings.simplefilter("ignore", DeprecationWarning)


def run(start_date, system='GOTO-8', duration=1, sites='N', telescopes=1):
    """Run the simulation."""
    # Create a log file
    fname = os.path.join(params.FILE_PATH, 'sim_allsky_lite_output')
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

    # Create output dict
    obs_count = np.zeros(grid.ntiles)

    # If no start_time is given start tonight
    if start_date is None:
        start_date = Time.now()
    midnight = Time(start_date.strftime('%Y-%m-%d') + 'T00:00:00')

    # Create night start times
    start_times = [midnight + n * u.day for n in range(duration)]
    print('Simulating {} nights'.format(len(start_times)))

    # Loop for given number of days
    for i, start_time in enumerate(start_times):
        sim_start_time = Time.now()

        # Write log line
        line = '{: >4}/{} :: {}: '.format(i + 1, len(start_times), start_time.strftime('%Y-%m-%d'))
        print(line, end='')

        # Loop over the whole day
        now = start_time
        day_count = 0
        while now < start_time + 1 * u.day:
            # Find which site is observing, if any
            # TODO: This relies on there only being one site observing at once...
            current_site = None
            sun = get_sun(now)
            for site in sites:
                altaz_frame = AltAz(obstime=now, location=site)
                if sun.transform_to(altaz_frame).alt < -12 * u.deg:
                    current_site = site
                    break

            # If no domes are currently observing then skip forward 5 minutes
            if current_site is None:
                # print('  {}: dome closed'.format(now.iso))
                now += 5 * 60 * u.s
                continue

            # Find which tiles are visible
            tiles_alt = grid.coords.transform_to(altaz_frame).alt
            visible_tiles_mask = tiles_alt > 30 * u.deg

            # Find the minimum obs count of all the visible tiles
            min_obs_count = min(obs_count[visible_tiles_mask])

            # We need to select targets for all telescopes
            unscheduled_telescopes = telescopes
            target_tiles_mask = np.full(grid.ntiles, False)
            while True:
                # Find which of the visible tiles have been observed the minimum number of times
                pending_tiles_mask = visible_tiles_mask & (obs_count == min_obs_count)

                # Find the highest altitude pending tiles
                sorted_alts = sorted(tiles_alt[pending_tiles_mask], reverse=True)
                if sum(pending_tiles_mask) >= unscheduled_telescopes:
                    # We limit to find the top X, where X is the number of telescopes
                    alt_select = sorted_alts[unscheduled_telescopes - 1]
                elif sum(pending_tiles_mask) > 0:
                    # There aren't enough pending tiles for the telescopes
                    alt_select = sorted_alts[-1]
                else:
                    # There aren't any pending tiles at this min_obs_count
                    # This can happen if we've already looped once
                    min_obs_count += 1
                    continue

                # Select the tiles
                alt_mask = tiles_alt >= alt_select
                target_tiles_mask |= (pending_tiles_mask & alt_mask)

                # If that's enough for all our telescopes we can exit
                unscheduled_telescopes = telescopes - sum(target_tiles_mask)
                if unscheduled_telescopes == 0:
                    break
                else:
                    # Increase the min_obs_count and loop again
                    min_obs_count += 1

            # Add one to these tiles obs count
            obs_count[target_tiles_mask] += 1

            # Increase the day count too
            day_count += sum(target_tiles_mask)

            # Print which tiles we observed
            # print('  {}: observed {}'.format(now.iso,
            #       ', '.join(['{} ({:.0f})'.format(i, j)
            #                  for i, j in zip(np.array(grid.tilenames)[target_tiles_mask],
            #                                  obs_count[target_tiles_mask])])))

            # Add on the exposure duration, with a bit of readout time and slew time
            duration = 3 * (60 + 10) + 20
            now += duration * u.s

        # Print results
        result = '{} tiles observed'.format(day_count)
        dt = (Time.now() - sim_start_time).to(u.s).value
        result += ' :: t={:.1f}'.format(dt)
        print(result)

    print('Observed {:.0f} tiles'.format(sum(obs_count)))
    print('Max times observed: {:.0f}'.format(max(obs_count)))
    print('Min times observed (>0): {:.0f}'.format(min(obs_count[obs_count > 0])))
    for i in sorted(set(obs_count)):
        print('  Observed {:.0f} tiles {:.0f} times'.format(sum(obs_count == i), i))

    grid.plot(color={grid.tilenames[i]: obs_count[i] for i in range(grid.ntiles)},
              discrete_colorbar=True)


if __name__ == "__main__":
    def date_validator(date):
        """Validate dates."""
        try:
            date = Time(date)
        except ValueError:
            msg = "invalid date: '{}' not a recognised format".format(date)
            raise ArgumentTypeError(msg)
        return date

    parser = ArgumentParser(description='Simulate observations of the all-sky survey')
    parser.add_argument('date', type=date_validator, nargs='?',
                        help='simulation start date (default=now)',
                        )
    parser.add_argument('system', type=str, choices=['GOTO-4', 'GOTO-8'],
                        help='which telescope system to simulate',
                        )
    parser.add_argument('-d', '--duration', type=int, default=1,
                        help='number of days to simulate (default=1)'
                        )
    parser.add_argument('-s', '--sites', type=str, choices=['N', 'S', 'K', 'NS', 'NK'], default='N',
                        help=('which sites to simulate observing from '
                              '(N=La Palma, S=Siding Spring, K=Mt Kent, default=N)'),
                        )
    parser.add_argument('-t', '--telescopes', type=str, default='1',
                        help=('number of telescopes to observe with at each site '
                              '(e.g. "1", "2", "2,1", default=1)'),
                        )
    args = parser.parse_args()

    date = args.date
    system = args.system
    duration = args.duration
    sites = args.sites
    if ',' in args.telescopes:
        telescopes = [int(telescope) for telescope in args.telescopes.split(',')]
    else:
        telescopes = int(args.telescopes)

    run(date, system, duration, sites, telescopes)
