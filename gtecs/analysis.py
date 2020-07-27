"""Functions for image analysis."""

from astropy.convolution import Gaussian2DKernel
from astropy.stats import gaussian_fwhm_to_sigma
from astropy.stats.sigma_clipping import sigma_clipped_stats

import numpy as np

import sep


def measure_image_hfd(data, filter_width=15, threshold=5, xslice=None, yslice=None, verbose=True):
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
    verbose : bool, default=True
        if False, supress printout

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
        if verbose:
            print('Found {} objects with measurable HFDs'.format(len(hfds)))

    # Get median and standard deviation over all extracted objects
    mean_hfd, median_hfd, std_hfd = sigma_clipped_stats(hfds, sigma=2.5, maxiters=10)

    return median_hfd, std_hfd
