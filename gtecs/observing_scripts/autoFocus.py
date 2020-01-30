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
from gtecs.observing import (get_analysis_image, get_current_focus, get_focus_limit,
                             prepare_for_images, set_new_focus, slew_to_radec, wait_for_focuser,
                             wait_for_mount)

import numpy as np

import pandas as pd

import sep


class RestoreFocus(NeatCloser):
    """Restore the focus values if anything goes wrong."""

    def __init__(self, focus_vals):
        super(RestoreFocus, self).__init__('autofocus')
        if type(focus_vals) == pd.Series:
            focus_vals = focus_vals.to_dict()
        self.focus_vals = focus_vals

    def tidy_up(self):
        """Restore the original focus."""
        print('Restoring original focus...')
        set_new_focus(self.focus_vals)


def get_focus():
    """Find the current focus positions, and return as a Pandas dataframe."""
    return pd.Series(get_current_focus())


def set_focus_carefully(new_focus, orig_focus, timeout=60):
    """Move to focus, but restore old values if we fail."""
    try:
        # The set_new_focus function doesn't like series
        if type(new_focus) == pd.Series:
            new_focus = new_focus.to_dict()

        # Check if the position is outside of the limit of the focuser
        limits = get_focus_limit()
        for ut in new_focus:
            if new_focus[ut] < 0:
                print('  WARNING: UT{} position is below minimum (0)'.format(ut))
                new_focus[ut] = 0
            if new_focus[ut] > limits[ut]:
                print('  WARNING: UT{} position is above maximum ({})'.format(ut, limits[ut]))
                new_focus[ut] = limits[ut]

        # Set the new position and wait until it returns (or times out)
        set_new_focus(new_focus)
        wait_for_focuser(new_focus, timeout)
    except Exception:
        print('Restoring original focus...')
        if type(orig_focus) == pd.Series:
            orig_focus = orig_focus.to_dict()
        set_new_focus(orig_focus)
        raise


def find_best_focus(m_l, m_r, delta_x, xval, yval):
    """Find the best focus by fitting to the V-curve.

    Given two lines with gradients m_l and m_r (left and right halves of the V-curve) with
    x-intercepts that differ by delta_x, find the point where the lines cross,
    given a location xval, yval on the right-hand line.
    """
    c2 = yval - m_r * xval
    c1 = m_l * (-delta_x + c2 / m_r)
    meeting_point = ((c1 - c2) / (m_r - m_l))
    return meeting_point


def estimate_focus(target_hfd, current_hfd, current_focus, slope):
    """Estimate the current focus from the slope of the V curve."""
    return current_focus + (target_hfd - current_hfd) / slope


def measure_image_hfd(data, filter_width=3, threshold=5, xslice=None, yslice=None, **kwargs):
    """Measure of half-flux-diameter and full-width at half-maximum of an image.

    Parameters
    ----------
    data : `numpy.array`
        image data to analyse
    filter_width : int, default=3
        before detection, the image is filtered. This is the filter width in pixels.
        For optimal source detection, this should roughly match the expected FWHM
    threshold : float, default=5
        if set to, e.g. 5, objects 5sigma above the background are detected
    xslice : `slice`, default=None
        slice in x axis
    yslice : `slice`, default=None
        slice in y axis
    kwargs : dict
        all other keyword arguments are passed to SEP's `extract` method

    Returns
    -------
    hfd : float
        median HFD value
    hfd_std : float
        standard deviation of HFD measurements
    fwhm : float
        median FWHM
    fwhm_std : float
        standard deviation of FWHM measurements

    """
    # Slice the data
    if xslice is None:
        xslice = slice(None)
    if yslice is None:
        yslice = slice(None)
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

    # Calculate FWHMs
    fwhms = 2 * np.sqrt(np.log(2) * (objects['a']**2 + objects['b']**2))
    fwhms = fwhms[mask]

    # Get median and standard deviation over all extracted objects
    mean_hfd, median_hfd, std_hfd = sigma_clipped_stats(hfds, sigma=2.5, maxiters=10)
    mean_fwhm, median_fwhm, std_fwhm = sigma_clipped_stats(fwhms, sigma=2.5, maxiters=10)

    return median_hfd, std_hfd, median_fwhm, std_fwhm


def get_hfds(image_data, **kwargs):
    """Measure the half-flux diameter of multiple images.

    Parameters
    ----------
    data : dict of `numpy.array`
        Image data to analyse.
    kwargs : dict
        Any other parameters are passed to `measure_image_hfd`.

    Returns
    -------
    foc_data : `pandas.DataFrame`
        A Pandas dataframe with an index of unit telescope ID.

    """
    hfd_dict = {}
    hfd_std_dict = {}
    fwhm_dict = {}
    fwhm_std_dict = {}

    for ut in image_data:
        try:
            # Extract mean and std values from the image data
            hfd, hfd_std, fwhm, fwhm_std = measure_image_hfd(image_data[ut], **kwargs)

            # Check for invalid values
            if hfd_std <= 0.0 or fwhm_std <= 0.0:
                raise ValueError

        except Exception as err:
            print('HFD measurement for UT{} errored: {}'.format(ut, str(err)))
            hfd_std = np.nan
            hfd = np.nan
            fwhm_std = np.nan
            fwhm = np.nan

        # Add to dicts
        hfd_dict[ut] = hfd
        hfd_std_dict[ut] = hfd_std
        fwhm_dict[ut] = fwhm
        fwhm_std_dict[ut] = fwhm_std

    # Return a combined data frame
    data = {'hfd': hfd_dict, 'hfd_std': hfd_std_dict, 'fwhm': fwhm_dict, 'fwhm_std': fwhm_std_dict}
    return pd.DataFrame(data)


def measure_hfd_carefully(orig_focus, num_exp=1, exptime=30, filt='L',
                          target_name='Focus test image', **kwargs):
    """Take a set of images and measure the mean half-flux diameters.

    Parameters
    ----------
    orig_focus : dict of float
        Original focus valuse to return to in case of errors.

    num_exp : int, default=1
        Number of exposures to take.
        If > 1 the smallest of the measured HFD values will be returned for each UT.
    exptime : float, default=30
        Image exposure time.
    filt : str, default='L'
       Filter to use for the exposures.
    target_name : str, default='Focus test image'
        Name of the target being observed.
    kwargs : dict
        Any other parameters are passed to `measure_image_hfd`.

    Returns
    -------
    foc_data : `pandas.DataFrame`
        A Pandas dataframe with an index of unit telescope ID.

    """
    hfds = None
    fwhms = None
    try:
        for i in range(num_exp):
            print('Taking exposure {}/{}...'.format(i + 1, num_exp))
            # Take a set of images
            image_data = get_analysis_image(exptime, filt, target_name, 'FOCUS', glance=False)
            # Measure the median HFDs in each image
            foc_data = get_hfds(image_data, **kwargs)
            # Add to set
            if hfds is not None:
                hfds = hfds.append(foc_data['hfd'])
                fwhms = fwhms.append(foc_data['fwhm'])
            else:
                hfds = foc_data['hfd']
                fwhms = foc_data['fwhm']
            print('HFDs:', foc_data['hfd'].to_dict())
            print('~~~~~~')

        # Take the smallest value of the set as the best estimate for the HFD at this position.
        # The reasoning is that we already average the HFD over many stars in each frame,
        # so across multiple frames we only sample external fluctuations, usually windshake,
        # which will always make the HFD worse, never better.
        hfds = hfds.groupby(level=0)
        fwhms = fwhms.groupby(level=0)
        data = {'pos': get_focus(),
                'hfd': hfds.min(),
                'hfd_std': hfds.std().fillna(0.0),
                'fwhm': fwhms.min(),
                'fwhm_std': fwhms.std().fillna(0.0),
                }
        return pd.DataFrame(data)

    except Exception:
        print('Restoring original focus...')
        set_new_focus(orig_focus)
        raise


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
        slew_to_radec(coordinate.ra.deg, coordinate.dec.deg)
        wait_for_mount(coordinate.ra.deg, coordinate.dec.deg, timeout=120)
        print('Reached target')
    else:
        target_name = 'Focus run'

    # Set exposure params
    exp_args = {'exptime': exptime, 'filt': filt, 'target_name': target_name}

    # Set extraction params
    sep_args = {'filter_width': 20, 'threshold': 5,
                'xslice': slice(3300, 5100), 'yslice': slice(1400, 4100)}

    # With focus where it is now, take images to get a baseline HFD.
    # Also store the current focus, so we can revert if there's any errors.
    orig_focus = get_focus()
    RestoreFocus(orig_focus)
    print('~~~~~~')
    print('Initial focus:', orig_focus.to_dict())
    print('Taking {} measurements at initial focus position...'.format(num_exp))
    foc_data = measure_hfd_carefully(orig_focus, num_exp, **exp_args, **sep_args)
    hfds = foc_data['hfd']
    print('Best HFDs:', hfds.to_dict())

    # Move to the positive side of the best focus position and measure HFD.
    # Assume the starting value is close to best, and a big step should be far enough out.
    print('~~~~~~')
    print('Moving focus out...')
    set_focus_carefully(orig_focus + big_step, orig_focus)
    print('New focus:', get_current_focus())
    print('Taking {} measurements at new focus position...'.format(num_exp))
    old_hfds = hfds
    foc_data = measure_hfd_carefully(orig_focus, num_exp, **exp_args, **sep_args)
    hfds = foc_data['hfd']
    print('Best HFDs:', hfds.to_dict())

    # The HFDs should have increased substantially.
    # If they haven't focus measurement is not reliable, so we can't continue.
    ratio = hfds / old_hfds
    if np.any(ratio < 1.2):
        print('~~~~~~')
        print('Initial HFDs:', old_hfds.to_dict())
        print('Current HFDs:', hfds.to_dict())
        set_new_focus(orig_focus.to_dict())
        raise Exception('HFD not changing with focus position')

    # Now move back towards where best focus should be.
    # This should confirm we're actually on the right-hand (positive) side of the V-curve.
    print('~~~~~~')
    print('Moving focus back in...')
    set_focus_carefully(get_focus() - small_step, orig_focus)
    print('New focus:', get_current_focus())
    print('Taking {} measurements at new focus position...'.format(num_exp))
    old_hfds = hfds
    foc_data = measure_hfd_carefully(orig_focus, num_exp, **exp_args, **sep_args)
    hfds = foc_data['hfd']
    print('Best HFDs:', hfds.to_dict())

    # The HDFs should have all decreased.
    # If they haven't we can't continue, because we might not be on the correct side.
    if np.any(hfds > old_hfds):
        print('~~~~~~')
        print('Far out HFDs:', old_hfds.to_dict())
        print('Back in HFDs:', hfds.to_dict())
        set_new_focus(orig_focus.to_dict())
        raise Exception('Can not be sure we are on the correct side of best focus')

    # We're on the curve, so we can estimate the focus position for given HFDs
    # Keep halving the target HFDs while we are greater than twice the near-focus HFD value.
    # Note we only move the focusers that need it, by masking.
    print('~~~~~~')
    print('Moving towards near-focus position...')
    while np.any(hfds > nfv):
        print('Moving focus in...')
        mask = hfds > nfv
        moving_uts = sorted(hfds.index)[mask]
        print('UTs above near-focus position: {}'.format(','.join(moving_uts)))
        target_hfds = (0.5 * hfds).where(mask, hfds)
        new_focus = estimate_focus(target_hfds, hfds, get_focus(), m_r)
        new_focus = {ut: new_focus.to_dict()[ut] for ut in moving_uts}
        set_focus_carefully(new_focus, orig_focus)
        print('New focus:', get_current_focus())

        foc_data = measure_hfd_carefully(orig_focus, num_exp, **exp_args, **sep_args)
        hfds = foc_data['hfd']
        print('Best HFDs:', hfds.to_dict())

    # We're close enough to the near-focus HFD to estimate the distance
    # and move directly to that position.
    print('~~~~~~')
    print('Moving to near-focus position...')
    nfp_focus = estimate_focus(nfv, hfds, get_focus(), m_r)
    print('Near-focus position focus:', nfp_focus.to_dict())
    set_focus_carefully(nfp_focus, orig_focus)
    print('Taking {} measurements at near-focus position...'.format(num_exp))
    foc_data = measure_hfd_carefully(orig_focus, num_exp, **exp_args, **sep_args)
    nfp_hfds = foc_data['hfd']
    print('Best HFDs at near-focus position:\n', foc_data[['hfd', 'hfd_std']])

    # Now we have the near-focus HFDs, find the best focus using `find_best_focus` and move there.
    print('~~~~~~')
    print('Finding best focus...')
    best_focus = find_best_focus(m_l, m_r, delta_x, nfp_focus, nfp_hfds)
    print('Best focus position:', best_focus.to_dict())
    set_focus_carefully(best_focus, orig_focus)
    print('Taking {} measurements at best focus position...'.format(num_exp))
    foc_data = measure_hfd_carefully(orig_focus, num_exp, **exp_args, **sep_args)
    print('Best HFDs at best focus position:\n', foc_data[['pos', 'hfd', 'hfd_std']])

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
    ut_params = params.AUTOFOCUS_PARAMS
    uts = list(params.AUTOFOCUS_PARAMS.keys())
    big_step = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['BIG_STEP'] for ut in uts})
    small_step = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['SMALL_STEP'] for ut in uts})
    nfv = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE'] for ut in uts})
    m_l = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['SLOPE_LEFT'] for ut in uts})
    m_r = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['SLOPE_RIGHT'] for ut in uts})
    delta_x = pd.Series({ut: params.AUTOFOCUS_PARAMS[ut]['DELTA_X'] for ut in uts})

    # If something goes wrong we need to restore the origional focus
    try:
        orig_focus = get_current_focus()
        run(big_step, small_step, nfv, m_l, m_r, delta_x,
            num_exp, exptime, filt, no_slew)
    except Exception:
        print('Restoring original focus...')
        set_new_focus(orig_focus)
        raise
