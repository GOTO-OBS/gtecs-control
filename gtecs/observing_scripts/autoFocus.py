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


def find_best_focus(m1, m2, delta, xval, yval):
    """Find the best focus by fitting to the V-curve.

    Given two lines with gradients m1 (positive) and m2 (negative) with intercepts differ by delta,
    find the point where the lines cross, given a location xval, yval on the line with gradient m2.
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


def get_hfd(image_data, filter_width=3, threshold=5, **kwargs):
    """Measure the HFD diameter from multiple files.

    Returns a Pandas dataframe with an index of telescope ID
    and columns of HFD and std dev

    Parameters are passed straight to `measure_hfd`
    """
    median_dict = {}
    std_dict = {}
    fwhm_dict = {}
    stdf_dict = {}
    for tel in image_data:
        try:
            median, std, fwhm, f_std = measure_hfd(image_data[tel],
                                                   filter_width, threshold, **kwargs)
        except Exception as error:
            print('HFD measurement for UT{} errored: {}'.format(tel, str(error)))
            std = -1.0
            median = -1.0
            f_std = -1
            fwhm = -1

        if std > 0.0:
            median_dict[tel] = median
            std_dict[tel] = std
        else:
            median_dict[tel] = np.nan
            std_dict[tel] = np.nan
        if f_std > 0.0:
            fwhm_dict[tel] = fwhm
            stdf_dict[tel] = f_std
        else:
            fwhm_dict[tel] = np.nan
            stdf_dict[tel] = np.nan
    return pd.DataFrame({'median': median_dict, 'std': std_dict,
                         'fwhm': fwhm_dict, 'fwhm_std': stdf_dict})


def measure_focus_carefully(target_name, orig_focus, **kwargs):
    """Take an image, measure the HFDs and return them."""
    try:
        image_data = get_analysis_image(params.AUTOFOCUS_EXPTIME, params.AUTOFOCUS_FILTER,
                                        target_name, 'FOCUS', glance=False)
        return get_hfd(image_data, **kwargs)['median']
    except Exception:
        set_new_focus(orig_focus)
        raise


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
    print('~~~~~~')
    print('Starting focus routine')
    star = focus_star(Time.now())
    print('Slewing to target', star)
    target_name = star.name
    coordinate = star.coord_now()
    goto(coordinate.ra.deg, coordinate.dec.deg)
    time.sleep(10)
    wait_for_telescope(120)  # 120s timeout
    print('Reached target')

    ##########
    # STEP 1
    # With focus where it is now, take an image to get a baseline HFD.
    # Also store the current focus, so we can revert if there's any errors.
    print('Taking initial focus measurement')
    orig_focus = pd.Series(get_current_focus())
    RestoreFocus(orig_focus)
    hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
    print('Previous focus:\n{!r}'.format(orig_focus))
    print('Half-flux-diameters:\n{!r}'.format(hfd_values))

    ##########
    # STEP 2
    # Move to the positive side of the best focus position and measure HFD.
    # Assume the starting value is close to best, and a big step should be far enough out.
    print('~~~~~~')
    print('Moving focus OUT by {:.0f}'.format(params.AUTOFOCUS_BIGSTEP))
    set_focus_carefully(orig_focus + params.AUTOFOCUS_BIGSTEP, orig_focus)
    print('New focus:\n{!r}'.format(get_current_focus()))

    # Measure the final value 3 times, then take the smallest as the HFD value.
    print('Taking 3 measurements at new position')
    old_hfd = hfd_values
    hfd_measurements = None
    for i in range(3):
        hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
        if hfd_measurements is not None:
            hfd_measurements = hfd_measurements.append(hfd_values)
        else:
            hfd_measurements = hfd_values
        print('Measurement {:.0f}/3\n Half-flux-diameters:\n{!r}'.format(i, hfd_values))
    hfd_measurements = hfd_measurements.groupby(level=0)
    hfd_values = hfd_measurements.min()
    print('Best measurement:\n Half-flux-diameters:\n{!r}'.format(hfd_values))

    # The HFDs should have increased substantially.
    # If they haven't focus measurement is not reliable, so we can't continue.
    ratio = hfd_values / old_hfd
    if np.any(ratio < 1.2):
        print('Current HFDs:\n{!r}'.format(hfd_values))
        print('Original HFDs:\n{!r}'.format(old_hfd))
        set_new_focus(orig_focus)
        raise Exception('HFD not changing with focus position')

    ##########
    # STEP 3
    # Move back towards where best focus should be.
    # This should confirm we're actually on the positive side of the V-curve.
    print('~~~~~~')
    print('Moving focus back in by {:.0f}'.format(params.AUTOFOCUS_SMALLSTEP))
    set_focus_carefully(pd.Series(get_current_focus()) - params.AUTOFOCUS_SMALLSTEP, orig_focus)
    print('New focus:\n{!r}'.format(get_current_focus()))

    # Measure the final value 3 times, then take the smallest as the HFD value.
    print('Taking 3 measurements at new position')
    old_hfd = hfd_values
    hfd_measurements = None
    for i in range(3):
        hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
        if hfd_measurements is not None:
            hfd_measurements = hfd_measurements.append(hfd_values)
        else:
            hfd_measurements = hfd_values
        print('Measurement {:.0f}/3\n Half-flux-diameters:\n{!r}'.format(i, hfd_values))
    hfd_measurements = hfd_measurements.groupby(level=0)
    hfd_values = hfd_measurements.min()
    print('Best measurement:\n Half-flux-diameters:\n{!r}'.format(hfd_values))

    # The HDFs should have all decreased.
    # If they haven't we can't continue, because we might not be on the positive side.
    if np.any(old_hfd < hfd_values):
        print('Far out HFDs:\n{!r}'.format(hfd_values))
        print('Back in HFDs:\n{!r}'.format(old_hfd))
        set_new_focus(orig_focus)
        raise Exception('Cannot be sure we are on the correct side of best focus')

    ##########
    # STEP 4
    # While we are greater than twice the near focus value, keep halving the hfd_values.
    # Note we only move the focusers that need it, by masking.
    print('~~~~~~')
    print('Moving towards near focus value ({:.0f})'.format(params.AUTOFOCUS_NEARFOCUSVALUE))
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
    # Now we're close enough to the near-focus value (NFV).
    # Estimate the distance to the NFV and move to that position.
    print('~~~~~~')
    print('Moving to near focus position')
    near_focus_pos = estimate_focus(nfv, hfd_values, pd.Series(get_current_focus()), m2)
    set_focus_carefully(near_focus_pos, orig_focus)
    print('Focus:\n{!r}'.format(near_focus_pos))

    print('Taking near focus measurements')
    # Measure the HFD at the near-focus position three times.
    nf_hfd_measurements = None
    for i in range(3):
        hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
        if nf_hfd_measurements is not None:
            nf_hfd_measurements = nf_hfd_measurements.append(hfd_values)
        else:
            nf_hfd_measurements = hfd_values
        print('Measurement {:.0f}/3\n Half-flux-diameters:\n{!r}'.format(i, hfd_values))

    # Take the smallest value of the 5 as the best estimate for the HFD at the near-focus position.
    # The reasoning is that we already average the HFD over many stars in each frame,
    # so across multiple frames we only sample external fluctuations, usually windshake,
    # which will always make the hfd worse, never better.
    nf_hfd_measurements = nf_hfd_measurements.groupby(level=0)
    nf_hfd = nf_hfd_measurements.min()
    nf_hfd_std = nf_hfd_measurements.std()
    nf_hfd_df = pd.DataFrame({'min': nf_hfd, 'std_dev': nf_hfd_std})
    print('HFD at near-focus position =\n{!r}'.format(nf_hfd_df))

    ##########
    # STEP 6
    # Now we have the near-focus HFDs, find the best focus using `find_best_focus` and move there.
    print('~~~~~~')
    print('Finding best focus...')
    best_focus = find_best_focus(m1, m2, delta, near_focus_pos, nf_hfd)
    print("Best focus at\n{!r}".format(best_focus))
    set_focus_carefully(best_focus, orig_focus)

    ##########
    # STEP 7
    # Measure the final value 3 times, then take the smallest as the best focus value.
    print('~~~~~~')
    print('Taking best focus measurements')
    best_hfd_measurements = None
    for i in range(3):
        best_hfd_values = measure_focus_carefully(target_name, orig_focus, **kwargs)
        if best_hfd_measurements is not None:
            best_hfd_measurements = best_hfd_measurements.append(best_hfd_values)
        else:
            best_hfd_measurements = best_hfd_values
        print('Measurement {:.0f}/3\n Half-flux-diameters:\n{!r}'.format(i, best_hfd_values))
    best_hfd_measurements = best_hfd_measurements.groupby(level=0)
    best_hfd = best_hfd_measurements.min()
    best_hfd_std = best_hfd_measurements.std()
    best_hfd_df = pd.DataFrame({'min': best_hfd, 'std_dev': best_hfd_std})
    print('HFD at best focus =\n{!r}'.format(best_hfd_df))

    print('Done')


if __name__ == "__main__":
    run()
