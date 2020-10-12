"""Focusing utilities."""

import numpy as np

import pandas as pd

from . import params
from .analysis import measure_image_hfd
from .observing import (get_analysis_image, get_focuser_positions, get_focuser_temperatures,
                        move_focusers)


def get_hfd_position(target_hfd, current_position, current_hfd, gradient):
    """Estimate the focuser position producing the target HFD.

    Uses the gradient of the right-hand wing of the V-curve, given a known position on that line
    it's just a simple linear fit.

    """
    return current_position + (target_hfd - current_hfd) / gradient


def get_best_focus_position(xval, yval, m_l, m_r, delta_x):
    """Find the best focus position by fitting to the V-curve.

    Given two lines with gradients m_l and m_r (left and right wings of the V-curve) with
    x-intercepts that differ by delta_x, find the x-position at the point where the lines cross,
    given a location xval, yval on the right-hand line.

    """
    c2 = yval - m_r * xval
    c1 = m_l * (-delta_x + c2 / m_r)
    meeting_point = ((c1 - c2) / (m_r - m_l))
    return meeting_point


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


def refocus(take_images=False, verbose=False):
    """Apply any needed temperature compensation to the focusers."""
    # Find the change in temperature since the last move
    curr_temp, prev_temp = get_focuser_temperatures()
    deltas = {ut: np.round(curr_temp[ut] - prev_temp[ut], 1)
              if (curr_temp[ut] is not None and prev_temp[ut] is not None) else 0
              for ut in params.UTS_WITH_FOCUSERS}
    if verbose:
        print('Checking focuser temperatures...')
        print('Current temp:', curr_temp)
        print('Previous temp:', prev_temp)
        print('Difference:', deltas)

    # Check if the change is greater than the minimum to refocus
    min_change = {ut: params.AUTOFOCUS_PARAMS[ut]['TEMP_MINCHANGE']
                  for ut in params.UTS_WITH_FOCUSERS}
    deltas = {ut: deltas[ut]
              if abs(deltas[ut]) > min_change[ut] else 0
              for ut in deltas}

    # Find the gradients (in steps/degree C)
    gradients = {ut: params.AUTOFOCUS_PARAMS[ut]['TEMP_GRADIENT']
                 for ut in params.UTS_WITH_FOCUSERS}

    # Calculate the focus offset
    offsets = {ut: int(deltas[ut] * gradients[ut]) for ut in params.UTS_WITH_FOCUSERS}
    if verbose:
        print('Offsets:', offsets)

    # Ignore any UTs which do not need changing
    offsets = {ut: offsets[ut] for ut in offsets if offsets[ut] != 0}

    if len(offsets) > 0:
        print('Applying temperature compensation to focusers')

        if not take_images:
            # Just move
            move_focusers(offsets, timeout=None)
        else:
            before_data = measure_focus(exptime=5)
            if verbose:
                print('Before data:\n', before_data.round(1))

            move_focusers(offsets, timeout=None)

            after_data = measure_focus(exptime=5)
            if verbose:
                print('After data:\n', after_data.round(1))

            diff = {ut: np.round(after_data['hfd'][ut] - before_data['hfd'][ut], 1)
                    for ut in after_data}
            print('Change in HFDs:', diff)
