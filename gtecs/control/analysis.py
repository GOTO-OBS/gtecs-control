"""Functions for image analysis."""

from concurrent.futures import ProcessPoolExecutor

from astropy.convolution import Gaussian2DKernel
from astropy.stats import gaussian_fwhm_to_sigma

import numpy as np

import sep


def get_focus_region(binning=1):
    """Define an annulus region used when measuring the focus position."""
    xlen = 8304 // binning
    ylen = 6220 // binning
    width = 1000 // binning
    region = [(slice(int(xlen / 6), int(xlen / 6) + width),
               slice(int(ylen / 6), int(5 * ylen / 6))),
              (slice(int(5 * xlen / 6) - width, int(5 * xlen / 6)),
               slice(int(ylen / 6), int(5 * ylen / 6))),
              (slice(int(xlen / 6) + width, int(5 * xlen / 6) - width),
               slice(int(5 * ylen / 6) - width, int(5 * ylen / 6))),
              (slice(int(xlen / 6) + width, int(5 * xlen / 6) - width),
               slice(int(ylen / 6), int(ylen / 6) + width))
              ]
    return region


def crop_image(data, region):
    """Crop the given image data to the provided region.

    Parameters
    ----------
    data : `numpy.array`
        The image data to analyse.
    region : 2-tuple of slice
        If given, crop data to given slices in x/y axes.
        For example: `(slice(2500, 6000), slice(1500, 4500))`.
        Note the region limits must be in BINNED pixels to match the data.

    """
    if region is None:
        # Return the uncropped data
        return data
    if len(region) != 2 or not isinstance(region[0], slice) or not isinstance(region[1], slice):
        raise TypeError('Invalid image region: {}'.format(region))

    x_slice, y_slice = region
    if (x_slice.start > data.shape[1] or x_slice.stop > data.shape[1] or
            y_slice.start > data.shape[0] or y_slice.stop > data.shape[0]):
        raise ValueError('Region {} exceeds data range, is it in unbinned pixels?'.format(region))

    data = np.ascontiguousarray(data[y_slice, x_slice])  # Note numpy takes X and Y "backwards"
    return data


def extract_image_sources(data, filter_width=15, threshold=5):
    """Extract sources from an image using `sep.extract`.

    Parameters
    ----------
    data : `numpy.array`
        The image data to analyse.
    filter_width : int, default=5
        Before detection, the image is filtered. This is the filter width in BINNED pixels.
        For optimal source detection, this should roughly match the expected FWHM.
    threshold : float, default=5
        If set to, e.g. 5, objects 5 sigma above the background are detected.

    Returns
    -------
    objects : list
        objects extracted by sep
    data : array
        background-subtracted data

    """
    # Measure spatially varying background and subtract from the data
    background = sep.Background(data)
    background.subfrom(data)

    # Make a Gaussian kernel for smoothing before detection
    sigma = filter_width * gaussian_fwhm_to_sigma
    size = int(filter_width)
    if filter_width > 15:
        # Limit size
        size = 15
    kernel = Gaussian2DKernel(sigma, x_size=size, y_size=size)
    kernel.normalize()

    # Extract sources
    objects = sep.extract(data,
                          threshold,
                          background.globalrms,
                          filter_kernel=kernel.array,
                          clean=True,
                          )

    return objects, data


def extract_fwhms(data, region=None, *args, **kwargs):
    """Extract the FWHMs of sources within the given image data.

    Parameters
    ----------
    data : `numpy.array`
        The image data to analyse.
    region : 2-tuple of slice or None, default=None
        If given, crop data to given slices in x/y axes (eg slice(2500, 6000), slice(1500, 4500)).
        Note the region limits given here must be in BINNED pixels to match the data.
    Other parameters are passed to `extract_image_sources`

    NOTE: This is just an estimate, since `sep` doesn't currently include FWHM measurement.
          See https://github.com/kbarbary/sep/issues/34

    """
    # Crop data to the given region
    if region is not None:
        data = crop_image(data, region)

    # Extract sources
    objects, data = extract_image_sources(data, *args, **kwargs)

    # Calculate FWHMs
    fwhms = 2 * np.sqrt(np.log(2) * (objects['a']**2 + objects['b']**2))

    # Mask any objects with high peak counts
    mask = objects['peak'] < 40000

    return fwhms[mask]


def extract_hfds(data, region=None, *args, **kwargs):
    """Extract the half-flux-diameters of sources within the given image data.

    Parameters
    ----------
    data : `numpy.array`
        The image data to analyse.
    region : 2-tuple of slice or None, default=None
        If given, crop data to given slices in x/y axes (eg slice(2500, 6000), slice(1500, 4500)).
        Note the region limits given here must be in BINNED pixels to match the data.
    Other parameters are passed to `extract_image_sources`

    """
    # Crop data to the given region
    if region is not None:
        data = crop_image(data, region)

    # Extract sources
    objects, data = extract_image_sources(data, *args, **kwargs)

    # Measure Half-Flux Radius to find HFDs
    hfrs, flags = sep.flux_radius(data, objects['x'], objects['y'],
                                  rmax=40 * np.ones_like(objects['x']),
                                  frac=0.5,
                                  normflux=objects['cflux'],
                                  )
    hfds = 2 * hfrs

    # Mask any objects with high peak counts or non-zero flags
    mask = np.logical_and(objects['peak'] < 40000, flags == 0)

    return hfds[mask]


def measure_image_fwhm(data, region=None, filter_width=15, threshold=5, verbose=True):
    """Measure the median FWHM of sources in an image.

    Parameters
    ----------
    data : `numpy.array`
        The image data to analyse
    region : 2-tuple of slice or None, default=None
        If given, crop data to given slices in x/y axes (eg slice(2500, 6000), slice(1500, 4500)).
        Note the region limits given here must be in BINNED pixels to match the data.
    filter_width : int, default=15
        The Gaussian filter width in BINNED pixels.
        See `gtecs.control.analysis.extract_image_sources()`
    threshold : int, default=5
        The sigma threshold for source detection.
        See `gtecs.control.analysis.extract_image_sources()`
    verbose : bool, default=True
        If False, suppress printout

    Returns
    -------
    median : float
        Median FWHM value of all detected sources, in binned pixels
    std : float
        standard deviation of FWHM measurements

    """
    # Extract FWHMs from the data
    fwhms = extract_fwhms(data, region, filter_width, threshold)

    # Check that enough sources were detected
    if len(fwhms) <= 3:
        raise ValueError('Not enough objects ({}) found for FWHM measurement'.format(len(fwhms)))
    if verbose:
        print('Found {} objects with measurable FWHMs'.format(len(fwhms)))

    # Get median and standard deviation over all extracted objects
    # NB Used to use astropy.stats.sigma_clipping.sigma_clipped_stats, but it's slow
    #    (see https://stackoverflow.com/questions/56563544)
    # mean_fwhm, median_fwhm, std_fwhm = sigma_clipped_stats(fwhms, sigma=2.5, maxiters=10)
    median_fwhm = np.median(fwhms)
    std_fwhm = fwhms[fwhms - fwhms.mean() < 2.5 * fwhms.std()].std()

    return median_fwhm, std_fwhm


def measure_image_hfd(data, region=None, filter_width=15, threshold=5,
                      parallel=False, verbose=True):
    """Measure the median half-flux-diameter of sources in an image.

    Parameters
    ----------
    data : `numpy.array`
        The image data to analyse
    region : 2-tuple of slice, or list of 2-tuple of slice, or None, default=None
        If given, crop data to given slices in x/y axes (eg (slice(2500, 6000), slice(1500, 4500))).
        If a list of regions is given, the HFDs of sources in each region will be measured
        before taking the median over all regions combined.
        Note the region limits given here must be in BINNED pixels to match the data.
    filter_width : int, default=15
        See `gtecs.control.analysis.extract_image_sources()`
    threshold : int, default=5
        See `gtecs.control.analysis.extract_image_sources()`
    parallel : bool, default=False
        If True, and multiple regions are given, then extract HFDs from each in parallel
    verbose : bool, default=True
        If False, suppress printout

    Returns
    -------
    median : float
        Median HFD value of all detected sources, in binned pixels
    std : float
        Standard deviation of HFD measurements

    """
    if region is None:
        regions = [None]
    elif len(region) == 2 and isinstance(region[0], slice) and isinstance(region[1], slice):
        regions = [region]
    else:
        regions = region

    # Extract HFDs from the data
    if not parallel:
        all_hfds = []
        for region in regions:
            hfds = extract_hfds(data, region, filter_width, threshold)
            all_hfds.append(hfds)
        all_hfds = np.concatenate(all_hfds)
    else:
        with ProcessPoolExecutor() as executor:
            futures = [executor.submit(extract_hfds, data, region, filter_width, threshold)
                       for region in regions]
            results = [future.result() for future in futures]
        all_hfds = np.concatenate(results).ravel().tolist()

    # Check that enough sources were detected
    if len(all_hfds) <= 3:
        raise ValueError('Not enough objects ({}) found for HFD measurement'.format(len(all_hfds)))
    if verbose:
        print('Found {} objects with measurable HFDs'.format(len(all_hfds)))

    # Get median and standard deviation over all extracted objects
    # NB Used to use astropy.stats.sigma_clipping.sigma_clipped_stats, but it's slow
    #    (see https://stackoverflow.com/questions/56563544)
    # mean_hfd, median_hfd, std_hfd = sigma_clipped_stats(all_hfds, sigma=2.5, maxiters=10)
    median_hfd = np.median(all_hfds)
    std_hfd = all_hfds[all_hfds - all_hfds.mean() < 2.5 * all_hfds.std()].std()

    return median_hfd, std_hfd
