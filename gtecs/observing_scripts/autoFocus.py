"""
Script to autofocus the telescopes.

Image quality is measured via the half-flux-diameter (HFD).

Half flux diameter vs focus position should be linear relationship,
with opposite slopes either side of the best focus. This function should
be fairly stable, so once you know which side of best focus you are
on, and the current HFD, you can in principle move straight to focus.

The routine searches for a target HFD known as the near focus value,
and hops to the best focus from there.
"""
from __future__ import absolute_import
from __future__ import print_function

import time
import argparse

import numpy as np
import pandas as pd

from astropy.time import Time
from astropy.io import fits
from astropy.stats.sigma_clipping import sigma_clipped_stats
from astropy.stats import gaussian_fwhm_to_sigma
from astropy.convolution import Gaussian2DKernel

import sep

from gtecs.tecs_modules import params
from gtecs.tecs_modules.misc import execute_command as cmd, neatCloser
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          last_written_image, goto,
                                          get_current_focus, set_new_focus,
                                          wait_for_focuser,
                                          prepare_for_images,
                                          wait_for_telescope)
from gtecs.catalogs import gliese


def take_frame(expT, current_filter, name):
    cmd('exq image {} {} 1 "{}" FOCUS'.format(
        expT, current_filter, name
    ))
    time.sleep(0.1)
    wait_time = 1.5*(expT + 30)
    wait_for_exposure_queue(wait_time)
    time.sleep(0.1)
    fnames = last_written_image()
    return fnames


class RestoreFocus(neatCloser):
    def __init__(self, focusVals):
        super(RestoreFocus, self).__init__('autofocus')
        self.focusVals = focusVals

    def tidyUp(self):
        print('Restoring original focus')
        set_new_focus(self.focusVals)


def set_focus_carefully(new_focus_values, orig_focus, timeout=30):
    """
    Move to focus, but restore old values if we fail
    """
    try:
        set_new_focus(new_focus_values)
        wait_for_focuser(timeout)
    except:
        set_new_focus(orig_focus)
        raise


def measure_focus_carefully(expT, filt, name, orig_focus, **kwargs):
    try:
        fnames = take_frame(expT, filt, name)
        return get_hfd(fnames, **kwargs)['median']
    except:
        set_new_focus(orig_focus)
        raise


def find_best_focus(m1, m2, delta, xval, yval):
    """
    Given two lines with gradients m1, m2 whose intercepts differ by delta.

    Now find the point where the lines cross, given a location xval, yval on
    the line with gradient m2.
    """
    c2 = yval-m2*xval
    c1 = m1*(-delta + c2/m2)
    meeting_point = ((c1-c2)/(m2-m1))
    return meeting_point


def estimate_focus(targetHFD, currentHFD, currentPos, slope):
    return currentPos + (targetHFD-currentHFD)/slope


def measure_hfd(fname, filter_width=3, threshold=5, **kwargs):
    """
    Crude measure of half-flux-diameter.

    Parameters
    ----------
    fname : string
        filename to analyse
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

    data = fits.getdata(fname).astype('float')
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
                                40*np.ones_like(objects['x']),
                                0.5, normflux=objects['cflux'])
    mask = np.logical_and(mask == 0, objects['peak'] < 40000)
    #mask = np.logical_and(mask, objects['peak'] > 100)

    hfd = 2*hfr[mask]
    fwhm = 2 * np.sqrt(np.log(2) * (objects['a']**2 + objects['b']**2))
    fwhm = fwhm[mask]
    if hfd.size > 3:
        mean_hfd, median_hfd, std_hfd = sigma_clipped_stats(hfd, sigma=2.5, iters=10)
        mean_fwhm, median_fwhm, std_fwhm = sigma_clipped_stats(fwhm, sigma=2.5, iters=10)
        return median_hfd, std_hfd, median_fwhm, std_fwhm
    return 0.0, 0.0, 0.0, 0.0


def get_hfd(fnames, filter_width=3, threshold=5, **kwargs):
    """
    Measure the HFD diameter from multiple files.

    Returns a Pandas dataframe with an index of telescope ID
    and columns of HFD and std dev

    Parameters are passed straight to `measure_hfd`
    """
    median_dict = {}
    std_dict = {}
    fwhm_dict = {}
    stdf_dict = {}
    for tel_key in fnames:
        try:
            median, std, fwhm, f_std = measure_hfd(fnames[tel_key],
                                                   filter_width, threshold, **kwargs)
        except Exception as err:
            print('HFD measurement for file {} errored: {}'.format(fnames[tel_key], str(err)))
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


def run(filt):
    bigstep = 5000
    smallstep = 300
    expT = 30
    nfv = 7

    xslice = slice(3300, 5100)
    yslice = slice(2800, 4100)
    kwargs = {'xslice': xslice, 'yslice': yslice,
              'filter_width': 20, 'threshold': 5}

    # make sure hardware is ready
    prepare_for_images()

    print('Starting focus routine')
    star = gliese.focus_star(Time.now())
    print('Slewing to star', star)
    name = star.name

    coordinate = star.coord_now()
    goto(coordinate.ra.deg, coordinate.dec.deg)
    time.sleep(10)
    wait_for_telescope(120)  # 120s timeout

    # get the parameters of the focus curves. Should be arrays, one entry per OTA
    m2 = pd.Series(params.FOCUS_SLOPE_ABOVE, dtype='float')
    m1 = pd.Series(params.FOCUS_SLOPE_BELOW, dtype='float')
    delta = pd.Series(params.FOCUS_INTERCEPT_DIFFERENCE, dtype='float')

    # start where we are now.
    fnames = take_frame(expT, filt, star.name)
    hfd_values = get_hfd(fnames, **kwargs)['median']
    orig_focus = pd.Series(get_current_focus())
    print('Previous focus:\n{!r}'.format(orig_focus))
    print('Half-flux-diameters:\n{!r}'.format(hfd_values))

    # from here any exception or attempt to close should move to old focus
    close_signal_handler = RestoreFocus(orig_focus)

    # move to +ve side of best focus position
    print('Move focus OUT')
    set_focus_carefully(orig_focus + bigstep, orig_focus)

    # hfd should have increased substantially
    # if it hasn't focus measurement is not reliable (dominated by CRs or BPs)?
    old_hfd = hfd_values
    hfd_values = measure_focus_carefully(expT, filt, name, orig_focus, **kwargs)
    print('Focus: {!r}'.format(get_current_focus()))
    print('Half-flux-diameters:\n{!r}'.format(hfd_values))

    # use pandas to perform maths based on keys
    ratio = hfd_values / old_hfd
    if np.any(ratio < 1.2):
        print('Current HFD:\n{!r}'.format(hfd_values))
        print('Original HFD:\n{!r}'.format(old_hfd))
        set_new_focus(orig_focus)
        raise Exception('image quality estimate not changing with focus position')

    # check we are actually on the +ve side of best focus by moving towards best focus
    set_focus_carefully(pd.Series(get_current_focus()) - smallstep, orig_focus)

    # check the IQ has got better
    old_hfd = hfd_values
    hfd_values = measure_focus_carefully(expT, filt, name, orig_focus, **kwargs)
    print('Focus: {!r}'.format(get_current_focus()))
    print('Half-flux-diameters:\n{!r}'.format(hfd_values))
    if np.any(old_hfd < hfd_values):
        set_new_focus(orig_focus)
        raise Exception('cannot be sure we are on the correct side of best focus')

    # while we are > than twice the near focus value, keep halving the hfd_values
    while np.any(hfd_values > nfv):
        print('stepping towards near focus')
        mask = hfd_values > nfv
        # move the focusers that need it
        target_hfds = (0.5*hfd_values).where(mask, hfd_values)
        new_focus_values = estimate_focus(target_hfds, hfd_values,
                                          pd.Series(get_current_focus()), m2)
        set_focus_carefully(new_focus_values, orig_focus)
        hfd_values = pd.Series(measure_focus_carefully(expT, filt, name, orig_focus, **kwargs))
        print('Focus: {!r}'.format(get_current_focus()))
        print('Half-flux-diameters: {!r}'.format(hfd_values))

    # close enough. Make the step to the focus position that should give HFV
    print('Starting near focus measurements')
    near_focus_pos = estimate_focus(nfv, hfd_values,
                                    pd.Series(get_current_focus()), m2)
    set_focus_carefully(near_focus_pos, orig_focus)
    print('Focus: {!r}'.format(near_focus_pos))
    # measure NFV five times and take average
    hfd_measurements = None
    for i in range(5):
        hfd_values = measure_focus_carefully(expT, filt, name, orig_focus, **kwargs)
        if hfd_measurements is not None:
            hfd_measurements = hfd_measurements.append(hfd_values)
        else:
            hfd_measurements = hfd_values
        print('Half-flux-diameters:\n{!r}'.format(hfd_values))
    hfd_measurements = hfd_measurements.groupby(level=0)
    hfd_values = hfd_measurements.mean()
    hfd_stddev = hfd_measurements.std()

    # find best focus
    hfd_samples = pd.DataFrame()
    for key in hfd_values.keys():
        hfd_samples[key] = np.random.normal(size=10**4,
                                            loc=hfd_values[key],
                                            scale=hfd_stddev[key])

    best_focus = find_best_focus(m1, m2, delta, near_focus_pos, hfd_samples)
    best_focus_mean = best_focus.mean(axis=0)
    best_focus_std = best_focus.std(axis=0)
    df = pd.DataFrame({'mean': best_focus_mean, 'std_dev': best_focus_std})
    print("Best focus at\n{!r}".format(df))

    set_focus_carefully(best_focus_mean, orig_focus)
    best_focus_values = measure_focus_carefully(expT, filt, name, orig_focus, **kwargs)
    print('HFD at best focus =\n{!r}'.format(best_focus_values))

    print("Done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('filt')
    args = parser.parse_args()

    if args.filt not in params.FILTER_LIST:
        raise ValueError('filter not one of {!r}'.format(params.FILTER_LIST))

    run(args.filt)
