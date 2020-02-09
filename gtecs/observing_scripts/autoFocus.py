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

from argparse import ArgumentParser

from astropy.convolution import Gaussian2DKernel
from astropy.stats import gaussian_fwhm_to_sigma
from astropy.stats.sigma_clipping import sigma_clipped_stats
from astropy.time import Time

from gtecs import params
from gtecs.catalogs import focus_star
from gtecs.misc import NeatCloser
from gtecs.observing import (get_analysis_image, get_focuser_positions, get_focuser_temperatures,
                             prepare_for_images, set_focuser_positions, slew_to_radec)

import numpy as np

import pandas as pd

import sep


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


def get_position(target_hfd, current_hfd, current_position, slope):
    """Estimate the focuser position producing the target HFD."""
    return current_position + (target_hfd - current_hfd) / slope


def measure_image_hfd(data, filter_width=5, threshold=5, xslice=None, yslice=None):
    """Measure the half-flux-diameter of an image.

    Parameters
    ----------
    data : `numpy.array`
        image data to analyse
    filter_width : int, default=5
        before detection, the image is filtered. This is the filter width in pixels.
        For optimal source detection, this should roughly match the expected FWHM
    threshold : float, default=5
        if set to, e.g. 5, objects 5sigma above the background are detected
    xslice : `slice`, default=slice(2500, 6000)
        slice in x axis
    yslice : `slice`, default=slice(1500, 4500)
        slice in y axis

    Returns
    -------
    median : float
        median HFD value
    std : float
        standard deviation of HFD measurements

    """
    # Slice the data
    if xslice is None:
        xslice = slice(2500, 6000)
    if yslice is None:
        yslice = slice(1500, 4500)
    data = np.ascontiguousarray(data[yslice, xslice])

    # Measure spatially varying background and subtract from the data
    background = sep.Background(data)
    background.subfrom(data)

    # Make a Gaussian kernel for smoothing before detection
    sigma = filter_width * gaussian_fwhm_to_sigma
    if filter_width > 15:
        size = 15
    else:
        size = int(filter_width)
    kernel = Gaussian2DKernel(sigma, x_size=size, y_size=size)
    kernel.normalize()

    # Extract sources
    objects = sep.extract(data, threshold, background.globalrms,
                          filter_kernel=kernel.array, clean=True)

    # Measure Half-Flux Radius to find HFDs
    hfrs, flags = sep.flux_radius(data, objects['x'], objects['y'],
                                  rmax=40 * np.ones_like(objects['x']),
                                  frac=0.5, normflux=objects['cflux'])
    hfds = 2 * hfrs

    # Mask any objects with non-zero flags or high peak counts
    mask = np.logical_and(flags == 0, objects['peak'] < 40000)
    hfds = hfds[mask]
    if len(hfds) <= 3:
        raise ValueError('Not enough objects ({}) found for focus measurement'.format(len(hfds)))
    else:
        print('Found {} objects with measurable HFDs'.format(len(hfds)))

    # Get median and standard deviation over all extracted objects
    mean_hfd, median_hfd, std_hfd = sigma_clipped_stats(hfds, sigma=2.5, maxiters=10)

    return median_hfd, std_hfd


def measure_focus(num_exp=1, exptime=30, filt='L', target_name='Focus test image'):
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

    Returns
    -------
    foc_data : `pandas.DataFrame`
        A Pandas dataframe with an index of unit telescope ID.

    """
    hfd_arrs = {}
    hfd_std_arrs = {}
    for i in range(num_exp):
        print('Taking exposure {}/{}...'.format(i + 1, num_exp))
        # Take a set of images
        image_data = get_analysis_image(exptime, filt, target_name, 'FOCUS', glance=False)

        # Restrict to UTs with focusers
        image_data = {ut: image_data[ut] for ut in params.UTS_WITH_FOCUSERS if ut in image_data}

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

            # Add to main arrays
            if ut in hfd_arrs:
                hfd_arrs[ut].append(hfd)
                hfd_std_arrs[ut].append(hfd_std)
            else:
                hfd_arrs[ut] = [hfd]
                hfd_std_arrs[ut] = [hfd_std]

        print('HFDs:', {ut: hfd_arrs[ut][i] for ut in hfd_arrs})

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

        min_i = np.where(hfds == min(hfds))[0][0]
        best_hfd[ut] = hfds[min_i]
        best_hfd_std[ut] = stds[min_i]

    data = {'pos': pd.Series(get_focuser_positions()),
            'hfd': pd.Series(best_hfd),
            'hfd_std': pd.Series(best_hfd_std),
            }

    # Also store the temperatures of the last focuser move
    _, temp = get_focuser_temperatures()
    data['temp'] = pd.Series(temp)

    return pd.DataFrame(data)


def run(big_step, small_step, nfv, m_l, m_r, delta_x, num_exp=3, exptime=30, filt='L',
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
        star = focus_star(Time.now())
        print('~~~~~~')
        print('Slewing to target {}...'.format(star))
        target_name = star.name
        coordinate = star.coord_now()
        slew_to_radec(coordinate.ra.deg, coordinate.dec.deg, timeout=120)
        print('Reached target')
    else:
        target_name = 'Focus run'

    # With the focusers where they are now, take images to get a baseline HFD.
    initial_positions = get_focuser_positions()
    print('~~~~~~')
    print('Initial positions:', initial_positions)
    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name)
    initial_hfds = foc_data['hfd']
    print('Best HFDs:', initial_hfds.to_dict())

    # Move to the positive side of the best focus position and measure HFD.
    # Assume the starting value is close to best, and a big step should be far enough out.
    print('~~~~~~')
    print('Moving focusers out...')
    new_positions = {ut: initial_positions[ut] + big_step[ut] for ut in initial_positions}
    set_focuser_positions(new_positions, timeout=120)  # longer timeout for big step
    print('New positions:', get_focuser_positions())
    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name)
    out_hfds = foc_data['hfd']
    print('Best HFDs:', out_hfds.to_dict())

    # The HFDs should have increased substantially.
    # If they haven't then the focus measurement isn't reliable, so we can't continue.
    ratio = out_hfds / initial_hfds
    if np.any(ratio < 1.2):
        print('~~~~~~')
        print('Initial HFDs:', initial_hfds.to_dict())
        print('Current HFDs:', out_hfds.to_dict())
        raise Exception('HFD not changing with focuser position')

    # Now move back towards where best focus position should be.
    # This should confirm we're actually on the right-hand (positive) side of the V-curve.
    print('~~~~~~')
    print('Moving focusers back in...')
    current_positions = get_focuser_positions()
    new_positions = {ut: current_positions[ut] - small_step[ut] for ut in current_positions}
    set_focuser_positions(new_positions, timeout=60)
    print('New positions:', get_focuser_positions())
    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name)
    in_hfds = foc_data['hfd']
    print('Best HFDs:', in_hfds.to_dict())

    # The HDFs should have all decreased.
    # If they haven't we can't continue, because we might not be on the correct side.
    if np.any(in_hfds > out_hfds):
        print('~~~~~~')
        print('Far out HFDs:', out_hfds.to_dict())
        print('Back in HFDs:', in_hfds.to_dict())
        raise Exception('Can not be sure we are on the correct side of best focus')

    # We're on the curve, so we can estimate the focuser positions for given HFDs.
    # Keep reducing the target HFDs while we are greater than twice the near-focus HFD value.
    # Note we only move the focusers that need it, by masking.
    print('~~~~~~')
    print('Moving towards near-focus position...')
    hfds = in_hfds
    while np.any(hfds > 2 * nfv):
        print('Moving focusers in...')
        mask = hfds > 2 * nfv
        moving_uts = hfds.index[mask]
        print('UTs to move: {}'.format(','.join([str(ut) for ut in moving_uts])))
        target_hfds = (hfds / 4).where(mask, hfds)
        current_positions = pd.Series(get_focuser_positions())
        new_positions = get_position(target_hfds, hfds, current_positions, m_r)
        new_positions = {ut: new_positions.to_dict()[ut] for ut in moving_uts}
        set_focuser_positions(new_positions, timeout=60)
        print('New positions:', get_focuser_positions())
        print('Taking {} focus measurements...'.format(num_exp))
        foc_data = measure_focus(num_exp, exptime, filt, target_name)
        hfds = foc_data['hfd']
        print('Best HFDs:', hfds.to_dict())

    # We're close enough to the near-focus HFD to estimate the distance
    # and move directly to that position.
    print('~~~~~~')
    print('Moving to near-focus position...')
    current_positions = pd.Series(get_focuser_positions())
    nf_positions = get_position(nfv, hfds, current_positions, m_r)
    print('Near-focus positions:', nf_positions.to_dict())
    set_focuser_positions(nf_positions.to_dict(), timeout=60)
    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name)
    nf_hfds = foc_data['hfd']
    print('Best HFDs at near-focus position:', nf_hfds.to_dict())

    # Now we have the near-focus HFDs, find the best focus position and move there.
    print('~~~~~~')
    print('Finding best focus...')
    bf_positions = get_best_focus_position(m_l, m_r, delta_x, nf_positions, nf_hfds)
    print('Best focus positions:', bf_positions.to_dict())
    set_focuser_positions(bf_positions.to_dict(), timeout=60)
    print('Taking {} focus measurements...'.format(num_exp))
    foc_data = measure_focus(num_exp, exptime, filt, target_name)
    bf_hfds = foc_data['hfd']
    print('Best HFDs at best focus position:\n', foc_data[['pos', 'hfd', 'hfd_std']])
    print('Temperature:', foc_data['temp'].to_dict())
    print('~~~~~~')
    print('Best HFDs at initial position:', initial_hfds.to_dict())
    print('Best HFDs at best focus position:', bf_hfds.to_dict())
    diff = initial_hfds - bf_hfds
    print('Difference :', diff.to_dict())

    if params.FOCUS_SLACK_REPORTS:
        # Send Slack report
        print('~~~~~~')
        print('Sending best focus measurements to Slack...')
        from gtecs.slack import send_slack_msg
        s = '*Autofocus results*\nFocus data at best position:```' + repr(foc_data) + '```'
        send_slack_msg(s)

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
    uts = sorted(params.AUTOFOCUS_PARAMS.keys())
    big_step = {ut: params.AUTOFOCUS_PARAMS[ut]['BIG_STEP'] for ut in uts}
    small_step = {ut: params.AUTOFOCUS_PARAMS[ut]['SMALL_STEP'] for ut in uts}
    nfv = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE'] for ut in uts})
    m_l = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['SLOPE_LEFT'] for ut in uts})
    m_r = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['SLOPE_RIGHT'] for ut in uts})
    delta_x = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['DELTA_X'] for ut in uts})

    # If something goes wrong we need to restore the origional focus
    try:
        initial_positions = get_focuser_positions()
        RestoreFocus(initial_positions)
        run(big_step, small_step, nfv, m_l, m_r, delta_x, num_exp, exptime, filt, no_slew)
    except Exception:
        print('Error caught: Restoring original focus positions...')
        set_focuser_positions(initial_positions)
        raise
