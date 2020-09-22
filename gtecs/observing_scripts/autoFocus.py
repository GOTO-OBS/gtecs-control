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

from gtecs import params
from gtecs.analysis import measure_image_hfd
from gtecs.catalogs import focus_star
from gtecs.misc import NeatCloser
from gtecs.observing import (get_analysis_image, get_focuser_positions, get_focuser_temperatures,
                             prepare_for_images, set_focuser_positions, slew_to_radec)

import numpy as np

import pandas as pd


class RestoreFocus(NeatCloser):
    """Restore the origional focus positions if anything goes wrong."""

    def __init__(self, positions):
        super(RestoreFocus, self).__init__('Script')
        self.positions = positions

    def tidy_up(self):
        """Restore the original focus."""
        print('Interrupt caught: Restoring original focus positions...')
        set_focuser_positions(self.positions)


def get_best_focus_position(m_l, m_r, delta_x, xval, yval):
    """Find the best focus position by fitting to the V-curve.

    Given two lines with gradients m_l and m_r (left and right halves of the V-curve) with
    x-intercepts that differ by delta_x, find the point where the lines cross,
    given a location xval, yval on the right-hand line.
    """
    c2 = yval - m_r * xval
    c1 = m_l * (-delta_x + c2 / m_r)
    meeting_point = ((c1 - c2) / (m_r - m_l))
    return meeting_point


def get_hfd_position(target_hfd, current_hfd, current_position, slope):
    """Estimate the focuser position producing the target HFD."""
    return current_position + (target_hfd - current_hfd) / slope


def measure_focus(num_exp=1, exptime=30, filt='L', target_name='Focus test image', uts=None):
    """Take a set of images and measure the median half-flux diameters.

    Parameters
    ----------
    num_exp : int, default=1
        Number of exposures to take.
        If > 1 the smallest of the measured HFD values will be returned for each UT.
    exptime : float, default=30
        Image exposure time.
    filt : str, default='L'
       Filter to use for the exposures.
    target_name : str, default='Focus test image'
        Name of the target being observed.
    uts : list of int, default=params.UTS_WITH_FOCUSERS (all UTs with focusers)
        UTs to measure focus for.

    Returns
    -------
    foc_data : `pandas.DataFrame`
        A Pandas dataframe with an index of unit telescope ID.

    """
    if uts is None:
        uts = params.UTS_WITH_FOCUSERS
    else:
        uts = [ut for ut in uts if ut in params.UTS_WITH_FOCUSERS]

    hfd_arrs = {}
    hfd_std_arrs = {}
    for i in range(num_exp):
        print('Taking exposure {}/{}...'.format(i + 1, num_exp))
        # Take a set of images
        image_data = get_analysis_image(exptime, filt, target_name, 'FOCUS', glance=False, uts=uts)

        # Measure the median HFDs in each image
        for ut in image_data:
            try:
                # Extract median HFD and std values from the image data
                # Note filter_width is 15, this deals much better with out-of-focus images
                hfd, hfd_std = measure_image_hfd(image_data[ut], filter_width=15)

                # Check for invalid values
                if hfd_std <= 0.0 <= 0.0:
                    raise ValueError

            except Exception as err:
                print('HFD measurement for UT{} errored: {}'.format(ut, str(err)))
                hfd = np.nan
                hfd_std = np.nan

            # Add to main arrays
            if ut in hfd_arrs:
                hfd_arrs[ut].append(hfd)
                hfd_std_arrs[ut].append(hfd_std)
            else:
                hfd_arrs[ut] = [hfd]
                hfd_std_arrs[ut] = [hfd_std]

        # Delete the image data for good measure, to save memory
        del image_data

        print('HFDs:', {ut: np.round(hfd_arrs[ut][i], 1) for ut in hfd_arrs})

    # Take the smallest of the HFD values measured as the best estimate for this position.
    # The reasoning is that we already average the HFD over many stars in each frame,
    # so across multiple frames we only sample external fluctuations, usually windshake,
    # which will always make the HFD worse, never better.
    # We also want to make sure we get the std associated with that image.
    best_hfd = {}
    best_hfd_std = {}
    for ut in hfd_arrs:
        hfds = np.array(hfd_arrs[ut])
        stds = np.array(hfd_std_arrs[ut])

        try:
            min_i = np.where(hfds == np.nanmin(hfds))[0][0]
            best_hfd[ut] = hfds[min_i]
            best_hfd_std[ut] = stds[min_i]
        except IndexError:
            # This UT had no non-NaN measurements
            best_hfd[ut] = np.nan
            best_hfd_std[ut] = np.nan

    data = {'pos': pd.Series(get_focuser_positions()),
            'hfd': pd.Series(best_hfd),
            'hfd_std': pd.Series(best_hfd_std),
            }

    # Also store the temperatures of the last focuser move
    _, temp = get_focuser_temperatures()
    data['temp'] = pd.Series(temp)

    # Make into a dataframe
    df = pd.DataFrame(data)
    df.index.name = 'UT'

    return df


def run(uts, big_step, small_step, nfv, m_l, m_r, delta_x, num_exp=3, exptime=30, filt='L',
        no_slew=False):
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

    # Try to focus all given UTs that have focusers
    uts = [ut for ut in uts if ut in params.UTS_WITH_FOCUSERS]
    active_uts = uts.copy()
    bad_uts = []

    # With the focusers where they are now, take images to get a baseline HFD.
    print('~~~~~~')
    initial_positions = get_focuser_positions(active_uts)
    print('Initial positions:', initial_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name, active_uts)
    initial_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', initial_hfds.round(1).to_dict())

    # Move to the positive side of the best focus position and measure HFD.
    # Assume the starting value is close to best, and a big step should be far enough out.
    print('~~~~~~')
    print('Moving focusers out...')
    new_positions = {ut: initial_positions[ut] + big_step[ut] for ut in active_uts}
    set_focuser_positions(new_positions, timeout=120)  # longer timeout for big step
    current_positions = get_focuser_positions(active_uts)
    print('New positions:', current_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name, active_uts)
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
        moving_uts = sorted(initial_hfds.index[mask])
        moving_uts = [ut for ut in active_uts if ut in moving_uts]
        print('UTs to move: {}'.format(','.join([str(ut) for ut in moving_uts])))

        print('Moving focusers out again...')
        new_positions = {ut: current_positions[ut] + big_step[ut] / 2 for ut in moving_uts}
        set_focuser_positions(new_positions, timeout=60)
        current_positions = get_focuser_positions(active_uts)
        print('New positions:', current_positions)

        print('Taking {} focus measurements...'.format(num_exp))
        foc_data = measure_focus(num_exp, exptime, filt, target_name, active_uts)
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
            bad_uts = sorted(initial_hfds.index[mask])
            print('Bad UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))

            # Remove bad UTs from the active list
            active_uts = sorted(ut for ut in active_uts if ut not in bad_uts)

    # Now move back towards where best focus position should be.
    # This should confirm we're actually on the right-hand (positive) side of the V-curve.
    print('~~~~~~')
    print('Moving focusers back in...')
    new_positions = {ut: current_positions[ut] - small_step[ut] for ut in active_uts}
    set_focuser_positions(new_positions, timeout=60)
    current_positions = get_focuser_positions(active_uts)
    print('New positions:', current_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name, active_uts)
    in_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', in_hfds.round(1).to_dict())

    # The HDFs should have all decreased.
    # If they haven't we can't continue, because we might not be on the correct side.
    if np.any(in_hfds > out_hfds):
        print('~~~~~~')
        print('Can not be sure we are on the correct side of best focus')
        print('Far out HFDs:', out_hfds.round(1).to_dict())
        print('Back in HFDs:', in_hfds.round(1).to_dict())

        mask = in_hfds > out_hfds
        bad_uts = sorted(in_hfds.index[mask])
        print('Bad UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))

        # Remove bad UTs from the main list
        active_uts = sorted(ut for ut in active_uts if ut not in bad_uts)

    # We're on the curve, so we can estimate the focuser positions for given HFDs.
    # Keep reducing the target HFDs while we are greater than twice the near-focus HFD value.
    # Note we only move the focusers that need it, by masking.
    print('~~~~~~')
    print('Moving towards near-focus position...')
    hfds = in_hfds
    while np.any(hfds > 2 * nfv):
        mask = hfds > 2 * nfv
        moving_uts = sorted(hfds.index[mask])
        moving_uts = [ut for ut in active_uts if ut in moving_uts]
        print('UTs to move: {}'.format(','.join([str(ut) for ut in moving_uts])))

        print('Moving focusers in...')
        target_hfds = (hfds / 4).where(mask, hfds)
        new_positions = get_hfd_position(target_hfds, hfds, pd.Series(current_positions), m_r)
        new_positions = {ut: int(new_positions.to_dict()[ut]) for ut in moving_uts}
        set_focuser_positions(new_positions, timeout=60)
        current_positions = get_focuser_positions(active_uts)
        print('New positions:', current_positions)

        print('Taking {} focus measurements...'.format(num_exp))
        foc_data = measure_focus(num_exp, exptime, filt, target_name, active_uts)
        hfds = foc_data['hfd']
        if num_exp > 1:
            print('Best HFDs:', hfds.round(1).to_dict())

    # We're close enough to the near-focus HFD to estimate the distance
    # and move directly to that position.
    print('~~~~~~')
    print('Calculating near-focus positions...')
    nf_positions = get_hfd_position(nfv, hfds, pd.Series(current_positions), m_r)
    nf_positions_dict = {ut: int(nf_positions.to_dict()[ut]) for ut in active_uts}
    print('Near-focus positions:', nf_positions_dict)

    print('Moving focusers to near-focus position...')
    set_focuser_positions(nf_positions_dict, timeout=60)
    current_positions = get_focuser_positions(active_uts)
    print('New positions:', current_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name, active_uts)
    nf_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', nf_hfds.round(1).to_dict())

    # Now we have the near-focus HFDs, find the best focus position and move there.
    print('~~~~~~')
    print('Calculating best focus positions...')
    bf_positions = get_best_focus_position(m_l, m_r, delta_x, nf_positions, nf_hfds)
    bf_positions_dict = {ut: int(bf_positions.to_dict()[ut]) for ut in active_uts}
    print('Best focus positions:', bf_positions_dict)

    print('Moving focusers to best focus position...')
    set_focuser_positions(bf_positions_dict, timeout=60)
    current_positions = get_focuser_positions(active_uts)
    print('New positions:', current_positions)

    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name, active_uts)
    bf_hfds = foc_data['hfd']
    if num_exp > 1:
        print('Best HFDs:', nf_hfds.round(1).to_dict())

    print('Focus data at best focus position:\n', foc_data.round(1))

    # Compare to initial values
    print('~~~~~~')
    print('Initial positions:', initial_positions)
    print('Best focus positions:', current_positions)
    print('Initial HFDs:', initial_hfds.round(1).to_dict())
    print('Final HFDs:  ', bf_hfds.round(1).to_dict())
    if np.any(bf_hfds > initial_hfds + 1):
        print('Final focus values are worse than initial values')

        mask = bf_hfds > initial_hfds + 1
        bad_uts = sorted(bf_hfds.index[mask])
        print('Bad UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))

        # Remove bad UTs from the main list
        active_uts = sorted(ut for ut in active_uts if ut not in bad_uts)

    # Reset any bad UTs to initial positions
    bad_uts = sorted(ut for ut in uts if ut not in active_uts)
    if len(bad_uts) > 0:
        print('~~~~~~')
        print('Focusing failed for UTs: {}'.format(','.join([str(ut) for ut in bad_uts])))

        print('Moving focusers back to initial positions...')
        new_positions = {ut: initial_positions[ut] for ut in bad_uts}
        set_focuser_positions(new_positions, timeout=60)
        current_positions = get_focuser_positions(bad_uts)
        print('New positions:', current_positions)

    if params.FOCUS_SLACK_REPORTS:
        # Send Slack report
        print('~~~~~~')
        print('Sending best focus measurements to Slack...')
        from gtecs.slack import send_slack_msg
        s = '*Autofocus results*\n'
        s += 'Focus data at best position:\n'
        s += '```' + repr(foc_data.round(1)) + '```\n'
        if len(bad_uts) > 0:
            s += 'Focusing failed for UTs: {}'.format(','.join([str(ut) for ut in bad_uts]))
        send_slack_msg(s)

    # Store the best focus data in a database
    foc_data['ts'] = Time.now().iso
    path = os.path.join(params.FILE_PATH, 'focus_data')
    with sqlite3.connect(os.path.join(path, 'focus.db')) as db_con:
        foc_data.to_sql(name='best_focus', con=db_con, if_exists='append')

    print('Done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Autofocus the telescopes.')
    parser.add_argument('-n', '--numexp', type=int, default=3,
                        help=('number of exposures to take at each position (default=3)')
                        )
    parser.add_argument('-t', '--exptime', type=float, default=30,
                        help=('exposure time to use (default=30s)')
                        )
    parser.add_argument('-f', '--filter', type=str, choices=params.FILTER_LIST, default='L',
                        help=('filter to use (default=L)')
                        )
    parser.add_argument('--no-slew', action='store_true',
                        help=('do not slew to a focus star (stay at current position)')
                        )
    args = parser.parse_args()

    num_exp = args.numexp
    exptime = args.exptime
    filt = args.filter
    no_slew = args.no_slew

    # Get the autofocus parameters
    # TODO: Could have param keys linked to OTA serial number, not UT number?
    #       Would probably need a conversion dict
    uts = sorted(params.AUTOFOCUS_PARAMS.keys())
    big_step = {ut: params.AUTOFOCUS_PARAMS[ut]['BIG_STEP'] for ut in uts}
    small_step = {ut: params.AUTOFOCUS_PARAMS[ut]['SMALL_STEP'] for ut in uts}
    nfv = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE'] for ut in uts})
    m_l = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['SLOPE_LEFT'] for ut in uts})
    m_r = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['SLOPE_RIGHT'] for ut in uts})
    delta_x = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['DELTA_X'] for ut in uts})

    # If something goes wrong we need to restore the origional focus
    initial_positions = get_focuser_positions()
    try:
        RestoreFocus(initial_positions)
        run(uts, big_step, small_step, nfv, m_l, m_r, delta_x, num_exp, exptime, filt, no_slew)
    except Exception:
        print('Error caught: Restoring original focus positions...')
        set_focuser_positions(initial_positions)
        raise
