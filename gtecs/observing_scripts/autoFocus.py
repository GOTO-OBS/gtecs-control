#!/usr/bin/env python
"""Script to autofocus the telescopes.

autoFocus

Image quality is measured via the half-flux-diameter (HFD).

Half flux diameter vs focus position should be linear relationship,
with opposite slopes either side of the best focus. This function should
be fairly stable, so once you know which side of best focus you are
on, and the current HFD, you can in principle move straight to focus.

The routine searches for a target HFD known as the near focus value,
and hops to the best focus from there.
"""

import time

from astropy.convolution import Gaussian2DKernel
from astropy.stats import gaussian_fwhm_to_sigma
from astropy.stats.sigma_clipping import sigma_clipped_stats
from astropy.time import Time

from gtecs import params
from gtecs.catalogs import focus_star
from gtecs.misc import NeatCloser
from gtecs.observing import (get_analysis_image, get_current_focus, goto, prepare_for_images,
                             set_new_focus, wait_for_focuser, wait_for_telescope)

import numpy as np

import pandas as pd

import sep


class RestoreFocus(NeatCloser):
    """Restore the focus values if anything goes wrong."""

    def __init__(self, focus_vals):
        super(RestoreFocus, self).__init__('autofocus')
        self.focus_vals = focus_vals

    def tidy_up(self):
        """Restore the original focus."""
        print('Restoring original focus')
        set_new_focus(self.focus_vals)


def set_focus_carefully(new_focus_values, orig_focus, timeout=30):
    """Move to focus, but restore old values if we fail."""
    try:
        set_new_focus(new_focus_values)
        wait_for_focuser(timeout)
    except Exception:
        set_new_focus(orig_focus)
        raise


def measure_focus_carefully(target_name, orig_focus, **kwargs):
    """Take an image, measure the HFDs and return them."""
    try:
        data = get_analysis_image(params.AUTOFOCUS_EXPTIME, params.AUTOFOCUS_FILTER,
                                  target_name, 'FOCUS', glance=False)
        return get_hfd(data, **kwargs)['median']
    except Exception:
        set_new_focus(orig_focus)
        raise


def find_best_focus(m1, m2, delta, xval, yval):
    """Given two lines with gradients m1, m2 whose intercepts differ by delta.

    Now find the point where the lines cross, given a location xval, yval on
    the line with gradient m2.

    """
    c2 = yval - m2 * xval
    c1 = m1 * (-delta + c2 / m2)
    meeting_point = ((c1 - c2) / (m2 - m1))
    return meeting_point


def estimate_focus(target_hfd, current_hfd, current_focus, slope):
    """Estimate the current focus from the slope of the V curve."""
    return current_focus + (target_hfd - current_hfd) / slope


def measure_hfd(data, filter_width=3, threshold=5, **kwargs):
    """Crude measure of half-flux-diameter.

    Parameters
    ----------
    data : `numpy.array`
        image data to analyse
    filter_width : int
        before detection, the image is filtered. This is the filter width in pixels.
        For optimal source detection, this should roughly match the expected FWHM
    threshold : float
        if set to, e.g. 5, objects 5sigma above the background are detected
    kwargs : dict
        all remaining keyword arguments are passed to SEP's `extract` method,
        apart from `xslice` and `yslice` which can be used to select a subset
        of the data.

    Returns
    -------
    median : float
        median HFD
    std : float
        standard deviation of measurements

    """
    xslice = kwargs.pop('xslice', slice(None))
    yslice = kwargs.pop('yslice', slice(None))

    data = np.ascontiguousarray(data[yslice, xslice])

    # measure spatially varying background
    bkg = sep.Background(data)
    bkg.subfrom(data)
    # make a Gaussian kernel for smoothing before detection
    sigma = filter_width * gaussian_fwhm_to_sigma
    size = int(filter_width) if filter_width <= 15 else 15
    kernel = Gaussian2DKernel(sigma, x_size=size, y_size=size)
    kernel.normalize()
    # find sources
    objects = sep.extract(data, threshold, bkg.globalrms, clean=True,
                          filter_kernel=kernel.array, **kwargs)
    # get half flux radius
    hfr, mask = sep.flux_radius(data, objects['x'], objects['y'],
                                40 * np.ones_like(objects['x']),
                                0.5, normflux=objects['cflux'])
    mask = np.logical_and(mask == 0, objects['peak'] < 40000)
    # mask = np.logical_and(mask, objects['peak'] > 100)

    hfd = 2 * hfr[mask]
    fwhm = 2 * np.sqrt(np.log(2) * (objects['a']**2 + objects['b']**2))
    fwhm = fwhm[mask]

    if hfd.size <= 3:
        raise ValueError('Not enough objects ({}) found for HFD measurement'.format(hfd.size))
    else:
        print('Found {} objects with measurable HFDs'.format(hfd.size))
        mean_hfd, median_hfd, std_hfd = sigma_clipped_stats(hfd, sigma=2.5, iters=10)
        mean_fwhm, median_fwhm, std_fwhm = sigma_clipped_stats(fwhm, sigma=2.5, iters=10)
        return median_hfd, std_hfd, median_fwhm, std_fwhm


def get_hfd(data, filter_width=3, threshold=5, **kwargs):
    """Measure the HFD diameter from multiple files.

    Returns a Pandas dataframe with an index of telescope ID
    and columns of HFD and std dev

    Parameters are passed straight to `measure_hfd`
    """
    median_dict = {}
    std_dict = {}
    fwhm_dict = {}
    stdf_dict = {}
    for tel_key in data:
        try:
            median, std, fwhm, f_std = measure_hfd(data[tel_key],
                                                   filter_width, threshold, **kwargs)
        except Exception as error:
            print('HFD measurement for UT{} errored: {}'.format(tel_key, str(error)))
            std = -1.0
            median = -1.0
            f_std = -1
            fwhm = -1

        if std > 0.0:
            median_dict[tel_key] = median
            std_dict[tel_key] = std
        else:
            median_dict[tel_key] = np.nan
            std_dict[tel_key] = np.nan
        if f_std > 0.0:
            fwhm_dict[tel_key] = fwhm
            stdf_dict[tel_key] = f_std
        else:
            fwhm_dict[tel_key] = np.nan
            stdf_dict[tel_key] = np.nan
    return pd.DataFrame({'median': median_dict, 'std': std_dict,
                         'fwhm': fwhm_dict, 'fwhm_std': stdf_dict})


def run():
    """Run the autofocus routine.

    This routine is based on the HFD V-curve method used by FocusMax
        (https://www.ccdware.com/products/focusmax/)

    See Weber & Brady "Fast auto-focus method and software for ccd-based telescopes" (2001)
    """
    xslice = slice(3300, 5100)
    yslice = slice(1400, 4100)
    kwargs = {'xslice': xslice, 'yslice': yslice,
              'filter_width': 20, 'threshold': 5}

    # get the parameters of the focus curves. Should be arrays, one entry per OTA
    m2 = pd.Series(params.FOCUS_SLOPE_ABOVE, dtype='float')
    m1 = pd.Series(params.FOCUS_SLOPE_BELOW, dtype='float')
    delta = pd.Series(params.FOCUS_INTERCEPT_DIFFERENCE, dtype='float')

    # make sure hardware is ready
    prepare_for_images()

    ##########
    # STEP 0
    # Slew to a focus star
    print('Starting focus routine')
    star = focus_star(Time.now())
    print('Slewing to star', star)
    target_name = star.name
    coordinate = star.coord_now()
    goto(coordinate.ra.deg, coordinate.dec.deg)
    time.sleep(10)
    wait_for_telescope(120)  # 120s timeout

    ##########
    # STEP 1
    # With focus where it is now, take an image to get a baseline HFD.
    # Also store the current focus, so we can revert if there's any errors.
    orig_focus = pd.Series(get_current_focus())
    RestoreFocus(orig_focus)
    hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
    print('Previous focus:\n{!r}'.format(orig_focus))
    print('Half-flux-diameters:\n{!r}'.format(hfd_values))

    ##########
    # STEP 2
    # Move to the positive side of the best focus position and measure HFD.
    # Assume the starting value is close to best, and a big step should be far enough out.
    print('Moveing focus OUT')
    set_focus_carefully(orig_focus + params.AUTOFOCUS_BIGSTEP, orig_focus)
    old_hfd = hfd_values
    hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
    print('Focus: {!r}'.format(get_current_focus()))
    print('Half-flux-diameters:\n{!r}'.format(hfd_values))

    # The HFDs should have increased substantially.
    # If they haven't focus measurement is not reliable, so we can't continue.
    ratio = hfd_values / old_hfd
    if np.any(ratio < 1.2):
        print('Current HFD:\n{!r}'.format(hfd_values))
        print('Original HFD:\n{!r}'.format(old_hfd))
        set_new_focus(orig_focus)
        raise Exception('HFD not changing with focus position')

    ##########
    # STEP 3
    # Move back towards where best focus should be.
    # This should confirm we're actually on the positive side of the V-curve.
    set_focus_carefully(pd.Series(get_current_focus()) - params.AUTOFOCUS_SMALLSTEP, orig_focus)
    old_hfd = hfd_values
    hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
    print('Focus: {!r}'.format(get_current_focus()))
    print('Half-flux-diameters:\n{!r}'.format(hfd_values))

    # The HDFs should have all decreased.
    # If they haven't we can't continue, because we might not be on the positive side.
    if np.any(old_hfd < hfd_values):
        set_new_focus(orig_focus)
        raise Exception('Cannot be sure we are on the correct side of best focus')

    ##########
    # STEP 4
    # While we are greater than twice the near focus value, keep halving the hfd_values.
    # Note we only move the focusers that need it, by masking.
    nfv = params.AUTOFOCUS_NEARFOCUSVALUE
    while np.any(hfd_values > nfv):
        print('Stepping towards near focus')
        mask = hfd_values > nfv
        target_hfds = (0.5 * hfd_values).where(mask, hfd_values)
        new_focus_values = estimate_focus(target_hfds, hfd_values,
                                          pd.Series(get_current_focus()), m2)

        set_focus_carefully(new_focus_values, orig_focus)
        hfd_values = pd.Series(measure_focus_carefully(target_name, orig_focus, **kwargs))
        print('Focus: {!r}'.format(get_current_focus()))
        print('Half-flux-diameters:\n{!r}'.format(hfd_values))

    ##########
    # STEP 5
    # Now we're close enough to the near focus value.
    # Estimate the distance to the NFV and move to that position.
    print('Starting near focus measurements')
    near_focus_pos = estimate_focus(nfv, hfd_values, pd.Series(get_current_focus()), m2)
    set_focus_carefully(near_focus_pos, orig_focus)
    print('Focus:\n{!r}'.format(near_focus_pos))

    # Measure the HFD at the NFV five times.
    hfd_measurements = None
    for _ in range(5):
        hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
        if hfd_measurements is not None:
            hfd_measurements = hfd_measurements.append(hfd_values)
        else:
            hfd_measurements = hfd_values
        print('Half-flux-diameters:\n{!r}'.format(hfd_values))

    # Assume the measurements are normally distributed, use them as a sample.
    # ARGUBLY we want to use the minimum here instead, for the same reasons as step 7.
    hfd_measurements = hfd_measurements.groupby(level=0)
    hfd_values = hfd_measurements.mean()
    hfd_std = hfd_measurements.std()
    hfd_samples = pd.DataFrame()
    for key in hfd_values.keys():
        hfd_samples[key] = np.random.normal(size=10**4, loc=hfd_values[key], scale=hfd_std[key])

    ##########
    # STEP 6
    # Now we have the near-focus HFDs, find the best focus using `find_best_focus` and move there.
    best_focus = find_best_focus(m1, m2, delta, near_focus_pos, hfd_samples)
    best_focus_mean = best_focus.mean(axis=0)
    best_focus_std = best_focus.std(axis=0)
    df = pd.DataFrame({'mean': best_focus_mean, 'std_dev': best_focus_std})
    print("Best focus at\n{!r}".format(df))
    set_focus_carefully(best_focus_mean, orig_focus)

    ##########
    # STEP 7
    # Measure the final value 3 times, then take the smallest as the best focus value.
    # We average the HFD over many stars in each frame so across multiple frames we sample
    #     external fluctuations, usually windshake, which always make the hfd worse, never better.
    best_hfd_measurements = None
    for _ in range(3):
        best_hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
        if best_hfd_measurements is not None:
            best_hfd_measurements = best_hfd_measurements.append(best_hfd_values)
        else:
            best_hfd_measurements = best_hfd_values
        print('Half-flux-diameters:\n{!r}'.format(best_hfd_values))
    best_hfd_measurements = best_hfd_measurements.groupby(level=0)
    best_hfd = best_hfd_measurements.min()
    best_hfd_std = best_hfd_measurements.std()
    best_hfd_df = pd.DataFrame({'min': best_hfd, 'std_dev': best_hfd_std})
    print('HFD at best focus =\n{!r}'.format(best_hfd_df))

    print('Done')


if __name__ == "__main__":
    run()
