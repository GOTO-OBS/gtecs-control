"""Functions for image analysis."""

from astropy.convolution import Gaussian2DKernel
from astropy.stats import gaussian_fwhm_to_sigma
from astropy.stats.sigma_clipping import sigma_clipped_stats

import numpy as np

import sep


def extract_image_sources(data, filter_width=15, threshold=5, region=None):
    """Extract sources from an image using `sep.extract`.

    Parameters
    ----------
    data : `numpy.array`
        image data to analyse
    filter_width : int, default=5
        before detection, the image is filtered. This is the filter width in (binned) pixels.
        For optimal source detection, this should roughly match the expected FWHM
    threshold : float, default=5
        if set to, e.g. 5, objects 5sigma above the background are detected
    region : 2-tuple of slice, default=(slice(2500, 6000), slice(1500, 4500))
        region slices in x/y axes

    Returns
    -------
    objects : list
        objects extracted by sep
    data : array
        cropped and background-subtracted data

    """
    # Slice the data
    if region is None:
        region = (slice(2500, 6000), slice(1500, 4500))
    xslice = region[0]
    yslice = region[1]
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

    return objects, data


def measure_image_fwhm(data, filter_width=15, threshold=5, region=None, verbose=True):
    """Measure the median FWHM of sources in an image.

    NOTE this is just an estimate, since `sep` doesn't currently include FWHM measurement.
         See https://github.com/kbarbary/sep/issues/34

    Parameters
    ----------
    verbose : bool, default=True
        if False, supress printout

    For other parameters see `gtecs.control.analysis.extract_image_sources()`

    Returns
    -------
    median : float
        median FWHM value, in binned pixels
    std : float
        standard deviation of FWHM measurements

    """
    # Extract sources
    objects, data = extract_image_sources(data, filter_width, threshold, region)

    # Calculate FWHMs
    fwhms = 2 * np.sqrt(np.log(2) * (objects['a']**2 + objects['b']**2))

    # Mask any objects with high peak counts
    mask = objects['peak'] < 40000
    fwhms = fwhms[mask]
    if len(fwhms) <= 3:
        raise ValueError('Not enough objects ({}) found for FWHM measurement'.format(len(fwhms)))
    else:
        if verbose:
            print('Found {} objects with measurable FWHMs'.format(len(fwhms)))

    # Get median and standard deviation over all extracted objects
    mean_fwhm, median_fwhm, std_fwhm = sigma_clipped_stats(fwhms, sigma=2.5, maxiters=10)

    return median_fwhm, std_fwhm


def measure_image_hfd(data, filter_width=15, threshold=5, region=None, verbose=True):
    """Measure the median half-flux-diameter of sources in an image.

    Parameters
    ----------
    verbose : bool, default=True
        if False, suppress printout

    For other parameters see `gtecs.control.analysis.extract_image_sources()`

    Returns
    -------
    median : float
        median HFD value, in binned pixels
    std : float
        standard deviation of HFD measurements

    """
    # Allow multiple regions to be measured
    regions = region
    if len(regions) == 2 and isinstance(regions[0], slice) and isinstance(regions[1], slice):
        regions = [regions]

    all_hfds = []
    for region in regions:
        # Extract sources
        objects, region_data = extract_image_sources(data, filter_width, threshold, region)

        # Measure Half-Flux Radius to find HFDs
        hfrs, flags = sep.flux_radius(region_data, objects['x'], objects['y'],
                                      rmax=40 * np.ones_like(objects['x']),
                                      frac=0.5, normflux=objects['cflux'])
        hfds = 2 * hfrs

        # Mask any objects with non-zero flags or high peak counts
        mask = np.logical_and(flags == 0, objects['peak'] < 40000)
        all_hfds.append(hfds[mask])
    all_hfds = np.concatenate(all_hfds)

    if len(all_hfds) <= 3:
        raise ValueError('Not enough objects ({}) found for HFD measurement'.format(len(all_hfds)))
    else:
        if verbose:
            print('Found {} objects with measurable HFDs'.format(len(all_hfds)))

    # Get median and standard deviation over all extracted objects
    mean_hfd, median_hfd, std_hfd = sigma_clipped_stats(all_hfds, sigma=2.5, maxiters=10)

    return median_hfd, std_hfd
