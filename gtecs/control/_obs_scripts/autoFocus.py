#!/usr/bin/env python3
"""Script to autofocus the telescopes.

Image quality is measured via the half-flux-diameter (HFD).

Half flux diameter vs focus position should be linear relationship,
with opposite slopes either side of the best focus. This function should
be fairly stable, so once you know which side of best focus you are
on, and the current HFD, you can in principle move straight to focus.

The routine searches for a target HFD known as the near focus value,
and hops to the best focus from there.
"""

import os
import sqlite3
from argparse import ArgumentParser

from astropy.time import Time

from gtecs.control import params
from gtecs.control.analysis import get_focus_region
from gtecs.control.catalogs import focus_star
from gtecs.control.focusing import (RestoreFocusCloser, get_best_focus_position,
                                    get_focus_params, get_focuser_positions, get_hfd_position,
                                    measure_focus, set_focuser_positions)
from gtecs.control.observing import prepare_for_images, slew_to_radec

import numpy as np

import pandas as pd


def run(num_exp=3, exptime=5, filt='L', binning=1,
        no_slew=False, no_report=False,
        use_annulus_region=True):
    """Run the autofocus routine.

    This routine is based on the HFD V-curve method used by FocusMax,
    see Weber & Brady "Fast auto-focus method and software for ccd-based telescopes" (2001).
    """
    # make sure hardware is ready
    prepare_for_images()

    print('~~~~~~')
    print('Starting focus routine')

    # Slew to a focus star
    if not no_slew:
        print('~~~~~~')
        star = focus_star(Time.now())
        print('Slewing to target {}...'.format(star))
        target_name = star.name
        coordinate = star.coord_now()
        slew_to_radec(coordinate.ra.deg, coordinate.dec.deg, timeout=120)
        print('Reached target')
    else:
        target_name = 'Autofocus'

    # Get the focus parameters defined in params
    foc_params = get_focus_params()

    # Try to focus all UTs that have focusers, as long as they have params
    all_uts = sorted(foc_params.index)
    active_uts = all_uts.copy()
    failed_uts = {}

    # Define measurement region
    if use_annulus_region:
        # Measure sources in an annulus around the centre
        region = get_focus_region(binning)
    else:
        # Stick to the default central region
        region = (slice(2500 // binning, 6000 // binning),
                  slice(1500 // binning, 4500 // binning))
    regions = [region]  # measure_focus takes a list of regions

    # With the focusers where they are now, take images to get a baseline HFD.
    print('~~~~~~')
    initial_positions = get_focuser_positions(active_uts)
    print('Initial positions:', initial_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, active_uts, regions)
    initial_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', initial_hfds.round(1).to_dict())

    # First a simple sanity check that we're getting any measurements
    if np.any(np.isnan(initial_hfds)):
        print('~~~~~~')
        print('Unable to measure image HFDs')

        mask = np.isnan(initial_hfds)
        bad_uts = sorted(initial_hfds[mask].index)
        print('Bad UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))
        failed_uts.update({ut: 'Unable to measure image HFDs' for ut in bad_uts})
        active_uts = sorted(ut for ut in active_uts if ut not in failed_uts)
        if len(active_uts) == 0:
            raise ValueError('All UTs have failed')

    # The focusers should be reasonably close to best focus.
    # If they are super far out then this method isn't going to work.
    if np.any(initial_hfds > 5 * foc_params['nfv']):
        print('~~~~~~')
        print('Focusers are already too far from best focus')

        mask = initial_hfds > 5 * foc_params['nfv']
        bad_uts = sorted(initial_hfds[mask].index)
        print('Bad UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))
        failed_uts.update({ut: 'Started too far from best focus' for ut in bad_uts})
        active_uts = sorted(ut for ut in active_uts if ut not in failed_uts)
        if len(active_uts) == 0:
            raise ValueError('All UTs have failed')

    # Move to the positive side of the best focus position and measure HFD.
    # Assume the starting value is close to best, and a big step should be far enough out.
    print('~~~~~~')
    print('Moving focusers out...')
    new_positions = {ut: initial_positions[ut] + foc_params['big_step'][ut] for ut in active_uts}
    set_focuser_positions(new_positions, timeout=120)  # longer timeout for big step
    current_positions = get_focuser_positions(active_uts)
    print('New positions:', current_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, active_uts, regions)
    out_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', out_hfds.round(1).to_dict())

    # The HFDs should have increased substantially.
    # If they haven't then something is wrong, we might have started too far over.
    # Try moving out a little more, but only the ones that need it (by masking).
    if np.any(out_hfds < initial_hfds + 1):
        print('~~~~~~')
        print('HFDs have not increased as expected')
        print('Initial HFDs:', initial_hfds.round(1).to_dict())
        print('Current HFDs:', out_hfds.round(1).to_dict())

        mask = out_hfds < initial_hfds + 2  # stricter mask
        moving_uts = sorted(initial_hfds[mask].index)
        moving_uts = [ut for ut in active_uts if ut in moving_uts]
        print('UTs to move: {}'.format(','.join([str(ut) for ut in moving_uts])))

        print('Moving focusers out again...')
        new_positions = {ut: int(current_positions[ut] + foc_params['big_step'][ut] / 2)
                         for ut in moving_uts}
        set_focuser_positions(new_positions, timeout=60)
        current_positions = get_focuser_positions(active_uts)
        print('New positions:', current_positions)

        print('Taking {} focus measurements...'.format(num_exp))
        foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, active_uts, regions)
        out_hfds = foc_data['hfd']
        if num_exp > 1:
            print('Best HFDs:', out_hfds.round(1).to_dict())

        # Now hopefully they should all be far enough from the starting position.
        # If not then they might need manually adjusting, or else the focusers aren't moving at all.
        if np.any(out_hfds < initial_hfds + 1):
            print('~~~~~~')
            print('HFDs are not changing with focuser position')
            print('Initial HFDs:', initial_hfds.round(1).to_dict())
            print('Current HFDs:', out_hfds.round(1).to_dict())

            mask = out_hfds < initial_hfds + 1
            bad_uts = sorted(initial_hfds[mask].index)
            print('Bad UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))
            failed_uts.update({ut: 'HFDs did not change with focuser position' for ut in bad_uts})
            active_uts = sorted(ut for ut in active_uts if ut not in failed_uts)
            if len(active_uts) == 0:
                raise ValueError('All UTs have failed')

    # Now move back towards where best focus position should be.
    # This should confirm we're actually on the right-hand (positive) side of the V-curve.
    print('~~~~~~')
    print('Moving focusers back in...')
    new_positions = {ut: current_positions[ut] - foc_params['small_step'][ut] for ut in active_uts}
    set_focuser_positions(new_positions, timeout=60)
    current_positions = get_focuser_positions(active_uts)
    print('New positions:', current_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, active_uts, regions)
    in_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', in_hfds.round(1).to_dict())

    # The HFDs should have all decreased.
    # If they haven't we can't continue, because we might not be on the correct side.
    if np.any(in_hfds > out_hfds - 1):
        print('~~~~~~')
        print('HFDs are not decreasing as expected')
        print('Far out HFDs:', out_hfds.round(1).to_dict())
        print('Back in HFDs:', in_hfds.round(1).to_dict())

        mask = in_hfds > out_hfds - 1
        bad_uts = sorted(in_hfds[mask].index)
        print('Bad UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))
        failed_uts.update({ut: 'HFDs did not decrease as expected' for ut in bad_uts})
        active_uts = sorted(ut for ut in active_uts if ut not in failed_uts)
        if len(active_uts) == 0:
            raise ValueError('All UTs have failed')

    # We're on the curve, so we can estimate the focuser positions for given HFDs.
    # Keep reducing the target HFDs while we are greater than twice the near-focus HFD value.
    # Note we only move the focusers that need it, by masking.
    # Also we limit the number of attempts, so if one gets stuck it doesn't go on forever.
    print('~~~~~~')
    print('Moving towards near-focus position...')
    current_hfds = in_hfds
    attempts = 3
    while np.any(current_hfds > 2 * foc_params['nfv']):
        mask = current_hfds > 2 * foc_params['nfv']
        moving_uts = sorted(current_hfds[mask].index)
        moving_uts = [ut for ut in active_uts if ut in moving_uts]
        print('UTs to move: {}'.format(','.join([str(ut) for ut in moving_uts])))

        if attempts <= 0:
            print('Number of attempts exceeded')
            # Remove bad UTs from the main list
            active_uts = sorted(ut for ut in active_uts if ut not in moving_uts)
            break
        else:
            attempts -= 1

        print('Moving focusers in...')
        # We move in by half the distance to the NFV, so we shouldn't overshoot
        target_hfds = current_hfds - ((current_hfds - foc_params['nfv']) / 2)
        target_hfds = target_hfds.where(mask, current_hfds)
        target_dict = {ut: target_hfds.to_dict()[ut] for ut in active_uts}
        print('Target HFD values:', target_dict)
        new_positions = get_hfd_position(target_hfds,
                                         pd.Series(current_positions),
                                         current_hfds,
                                         foc_params['m_r'],
                                         )
        new_positions = {ut: int(new_positions.to_dict()[ut]) for ut in moving_uts}
        set_focuser_positions(new_positions, timeout=60)
        current_positions = get_focuser_positions(active_uts)
        print('New positions:', current_positions)

        print('Taking {} focus measurements...'.format(num_exp))
        foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, active_uts, regions)
        current_hfds = foc_data['hfd']
        if num_exp > 1:
            print('Best HFDs:', current_hfds.round(1).to_dict())

    # We're close enough to the near-focus HFD to estimate the distance
    # and move directly to that position.
    print('~~~~~~')
    print('Calculating near-focus positions...')
    nfv_dict = {ut: int(foc_params['nfv'].to_dict()[ut]) for ut in active_uts}
    print('Near-focus HFD values:', nfv_dict)
    nf_positions = get_hfd_position(foc_params['nfv'],
                                    pd.Series(current_positions),
                                    current_hfds,
                                    foc_params['m_r'],
                                    )
    nf_positions_dict = {ut: int(nf_positions.to_dict()[ut]) for ut in active_uts}
    print('Near-focus positions:', nf_positions_dict)

    print('Moving focusers to near-focus position...')
    set_focuser_positions(nf_positions_dict, timeout=60)
    current_positions = get_focuser_positions(active_uts)
    print('New positions:', current_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, active_uts, regions)
    nf_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', nf_hfds.round(1).to_dict())

    # Now we have the near-focus HFDs, find the best focus position and move there.
    print('~~~~~~')
    print('Calculating best focus positions...')
    bf_positions = get_best_focus_position(nf_positions,
                                           nf_hfds,
                                           foc_params['m_l'],
                                           foc_params['m_r'],
                                           foc_params['delta_x'],
                                           )
    bf_positions_dict = {ut: int(bf_positions.to_dict()[ut]) for ut in active_uts}
    print('Best focus positions:', bf_positions_dict)

    print('Moving focusers to best focus position...')
    set_focuser_positions(bf_positions_dict, timeout=60)
    current_positions = get_focuser_positions(active_uts)
    print('New positions:', current_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, active_uts, regions)
    final_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', final_hfds.round(1).to_dict())

    print('Focus data at best focus position:\n', foc_data.round(1))

    # Compare to initial values
    print('~~~~~~')
    print('Initial positions:', initial_positions)
    print('Best focus positions:', current_positions)
    print('Initial HFDs:', initial_hfds.round(1).to_dict())
    print('Final HFDs:  ', final_hfds.round(1).to_dict())
    if np.any(final_hfds > initial_hfds + 1):
        print('Final HFDs are worse than initial values')

        mask = final_hfds > initial_hfds + 1
        bad_uts = sorted(final_hfds[mask].index)
        print('Bad UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))
        failed_uts.update({ut: 'Final HFDs were worse than initial values' for ut in bad_uts})
        active_uts = sorted(ut for ut in active_uts if ut not in failed_uts)
        if len(active_uts) == 0:
            raise ValueError('All UTs have failed')

    # Reset any bad UTs to initial positions
    if len(failed_uts) > 0:
        print('~~~~~~')
        print('Failed to focus:')
        for ut in sorted(failed_uts):
            print('- UT{} ("{}")'.format(ut, failed_uts[ut]))

        print('Moving focusers back to initial positions...')
        new_positions = {ut: initial_positions[ut] for ut in failed_uts}
        set_focuser_positions(new_positions, timeout=60)
        current_positions = get_focuser_positions()
        print('New positions:', current_positions)

        # Take final measurements again
        print('Taking {} focus measurements...'.format(num_exp))
        foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, all_uts, regions)
        final_hfds = foc_data['hfd']
        if num_exp > 1:
            print('Best HFDs:', final_hfds.round(1).to_dict())

        print('Focus data at best focus position:\n', foc_data.round(1))

        print('~~~~~~')
        print('Initial positions:', initial_positions)
        print('Final positions:', current_positions)
        print('Initial HFDs:', initial_hfds.round(1).to_dict())
        print('Final HFDs:  ', final_hfds.round(1).to_dict())

    if not no_report:
        # Send Slack report
        print('~~~~~~')
        print('Sending best focus measurements to Slack...')
        from gtecs.control.slack import send_slack_msg
        msg = '*Autofocus results*\n'
        msg += 'Focus data at final position:\n'
        msg += '```' + repr(foc_data.round(1)) + '```\n'
        if len(failed_uts) > 0:
            msg += 'Failed to focus:\n'
            for ut in sorted(failed_uts):
                msg += '- UT{}: {}\n'.format(ut, failed_uts[ut])
        send_slack_msg(msg)

    # Store the best focus data in a database
    foc_data['ts'] = Time.now().iso
    direc = os.path.join(params.FILE_PATH, 'focus_data')
    if not os.path.exists(direc):
        os.mkdir(direc)
    with sqlite3.connect(os.path.join(direc, 'focus.db')) as db_con:
        foc_data.to_sql(name='best_focus', con=db_con, if_exists='append')

    print('Done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Autofocus the telescopes.')
    # Optional arguments
    parser.add_argument('-n', '--numexp',
                        type=int, default=1,
                        help=('number of exposures to take at each position'
                              ' (default=%(default)d)')
                        )
    parser.add_argument('-t', '--exptime',
                        type=float, default=5,
                        help=('exposure time, in seconds'
                              ' (default=%(default)d)')
                        )
    parser.add_argument('-f', '--filter',
                        type=str, default='L',
                        help=('filter to use'
                              ' (default=%(default)s)')
                        )
    parser.add_argument('-b', '--binning',
                        type=int, default=1,
                        help=('image binning factor'
                              ' (default=%(default)d)')
                        )
    # Flags
    parser.add_argument('--no-slew', action='store_true',
                        help=('do not slew to a focus star (stay at current position)')
                        )
    parser.add_argument('--no-report', action='store_true',
                        help=('do not send final focus positions to Slack')
                        )

    args = parser.parse_args()
    num_exp = args.numexp
    exptime = args.exptime
    filt = args.filter
    binning = args.binning
    no_slew = args.no_slew
    no_report = args.no_report

    # If something goes wrong we need to restore the original focus
    initial_positions = get_focuser_positions()
    try:
        RestoreFocusCloser(initial_positions)
        run(num_exp, exptime, filt, binning, no_slew, no_report)
    except Exception:
        print('Error caught: Restoring original focus positions...')
        set_focuser_positions(initial_positions)
        raise
