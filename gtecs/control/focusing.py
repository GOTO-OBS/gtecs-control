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


def measure_focus(num_exp=1, exptime=30, filt='L', binning=1, target_name='Focus test image',
                  uts=None, regions=None):
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
    binning : int, default=1
       Binning factor to use for the exposures.
    target_name : str, default='Focus test image'
        Name of the target being observed.
    uts : list of int, default=params.UTS_WITH_FOCUSERS (all UTs with focusers)
        UTs to measure focus for.
    regions : 2-tuple of slice, or list of 2-tuple of slice, or None, default=None
        image region(s) to measure the focus within, in UNBINNED pixels
        if None then use the default central region from
        `gtecs.control.analysis.extract_image_sources()`

    Returns
    -------
    foc_data : `pandas.DataFrame` or list of `pandas.DataFrame`s
        A Pandas dataframe with an index of unit telescope ID.
        If more than one region is given then the list will be len(regions)

    """
    if uts is None:
        uts = params.UTS_WITH_FOCUSERS
    else:
        uts = [ut for ut in uts if ut in params.UTS_WITH_FOCUSERS]
    if regions is None:
        regions = [None]
    elif len(regions) == 2 and isinstance(regions[0], slice):
        # A single 2-tuple region
        regions = [regions]

    # Get the current focuser positions and the temperature the last time they moved
    current_focus = get_focuser_positions()
    _, last_temps = get_focuser_temperatures()
    all_uts = sorted(current_focus.keys())

    all_data = [{ut: [] for ut in all_uts} for _ in range(len(regions))]
    for i in range(num_exp):
        print('Taking exposure {}/{}...'.format(i + 1, num_exp))
        # Take a set of images
        image_data = get_analysis_image(exptime, filt, binning, target_name, 'FOCUS',
                                        glance=False, uts=uts)

        # Measure the median HFDs in each image
        for ut in all_uts:
            for j, region in enumerate(regions):
                # The "region" can be a list, in which case they are combined
                if len(region) == 2 and isinstance(region[0], slice):
                    region = [region]
                # We need to correct the region limits if binning, since they're in unbinned pixels
                if region[0] is not None:
                    region = [(slice(r[0].start // binning, r[0].stop // binning),
                               slice(r[1].start // binning, r[1].stop // binning))
                              for r in region]

                if ut in image_data:
                    # Measure focus within each given region
                    try:
                        # Extract median HFD and std values from the image data
                        # Note filter_width is 15, this deals much better with out-of-focus images
                        hfd, hfd_std = measure_image_hfd(image_data[ut],
                                                         filter_width=15 // binning,
                                                         region=region,
                                                         verbose=False)

                        # HFDs are in binned pixels, convert to unbinned
                        hfd *= binning
                        hfd_std *= binning

                        # Check for invalid values
                        if hfd_std <= 0.0:
                            raise ValueError

                    except Exception as err:
                        print('HFD measurement for UT{}{} errored: {}'.format(ut,
                              ' region {}'.format(j) if len(regions) > 1 else '', str(err)))
                        hfd = np.nan
                        hfd_std = np.nan
                else:
                    # We're ignoring this UT, but still add NaNs
                    hfd = np.nan
                    hfd_std = np.nan

                # Add to main arrays
                data_dict = {'UT': ut,
                             # 'exposure': i,
                             'pos': current_focus[ut],
                             'region': j,
                             'hfd': hfd,
                             'hfd_std': hfd_std,
                             'temp': last_temps[ut],
                             }
                all_data[j][ut].append(data_dict)

        # Delete the image data for good measure, to save memory
        del image_data

        if len(regions) == 1:
            print('HFDs:', {ut: np.round(all_data[0][ut][i]['hfd'], 1) for ut in uts})
        else:
            s = 'HFDs:\n'
            for j, data in enumerate(all_data):
                s += 'region {}: {}\n'.format(j, {ut: np.round(data[ut][i]['hfd'], 1)
                                                  for ut in uts})
            print(s[:-1])

    all_dfs = []
    s = 'Best HFDs:{}'.format('\n' if len(all_data) > 1 else ' ')
    for j, region_data in enumerate(all_data):
        # Make into dataframes
        region_dfs = {ut: pd.DataFrame(region_data[ut]) for ut in region_data}

        # Take the smallest of the HFD values measured as the best estimate for this position.
        # The reasoning is that we already average the HFD over many stars in each frame,
        # so across multiple frames we only sample external fluctuations, usually windshake,
        # which will always make the HFD worse, never better.
        # We also want to make sure we get the std associated with that image.
        best_dfs = [region_dfs[ut][region_dfs[ut]['hfd'] == region_dfs[ut]['hfd'].min()]
                    if not np.isnan(region_dfs[ut]['hfd'].min())  # will return NaN if are all NaNs
                    else region_dfs[ut].iloc[[0]]                 # just take the first row
                    for ut in region_dfs]

        # Make into a single dataframe
        df = pd.concat(best_dfs)
        df.set_index('UT', inplace=True)
        all_dfs.append(df)

        # Print best HFDs if more than one exp was taken
        if len(all_data) > 1:
            s += 'region {}: '.format(j)
        s += '{}\n'.format(df['hfd'].round(1).to_dict())

    if num_exp > 1:
        print(s[:-1])

    if len(all_dfs) == 1:
        # backwards compatibility if there's only one region
        df = all_dfs[0]
        df.drop('region', axis=1, inplace=True)
        return df
    return all_dfs


def focus_temp_compensation(take_images=False, verbose=False):
    """Apply any needed temperature compensation to the focusers."""
    # Find the change in temperature since the last move
    curr_temp, prev_temp = get_focuser_temperatures()
    deltas = {ut: np.round(curr_temp[ut] - prev_temp[ut], 1)
              if (curr_temp[ut] is not None and prev_temp[ut] is not None) else 0
              for ut in params.AUTOFOCUS_PARAMS}
    if verbose:
        print('Checking focuser temperatures...')
        print('Current temp:', curr_temp)
        print('Previous temp:', prev_temp)
        print('Difference:', deltas)

    # Check if the change is greater than the minimum to refocus
    min_change = {ut: params.AUTOFOCUS_PARAMS[ut]['TEMP_MINCHANGE']
                  for ut in params.AUTOFOCUS_PARAMS}
    deltas = {ut: deltas[ut]
              if abs(deltas[ut]) > min_change[ut] else 0
              for ut in deltas}

    # Find the gradients (in steps/degree C)
    gradients = {ut: params.AUTOFOCUS_PARAMS[ut]['TEMP_GRADIENT']
                 for ut in params.AUTOFOCUS_PARAMS}

    # Calculate the focus offset
    offsets = {ut: int(deltas[ut] * gradients[ut]) for ut in params.AUTOFOCUS_PARAMS}
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
                    for ut in after_data.index}
            print('Change in HFDs:', diff)
