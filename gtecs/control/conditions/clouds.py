"""Conditions functions for reading cloud coverage."""

import os
import urllib.request

import cv2

import numpy as np

from .. import params


def get_satellite_clouds(site):
    """Download the Eumetsat IR image from sat24.com, and use it to judge clouds over the site.

    Returns a value between 0 and 1, representing the median pixel illumination.
    """
    valid_sites = ['La Palma', 'Siding Spring']
    if site not in valid_sites:
        raise ValueError(f'Invalid site: {site} (must be one of {valid_sites}))')

    if site == 'La Palma':
        # Download image
        image_url = 'https://en.sat24.com/image?type=infraPolair&region=ce'
        try:
            with urllib.request.urlopen(image_url, timeout=2) as url:
                arr = np.asarray(bytearray(url.read()), dtype='uint8')
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

            # Crop La Palma area
            img_crop = img[205:225, 310:330]

            # Get standard deviation between the channels to mask out the coastline
            std = np.std(cv2.split(img_crop), axis=0)
            mask = std < 20
            # Mask image
            img_masked = img_crop[mask]
        except Exception:
            raise

    elif site == 'Siding Spring':
        # Download image
        image_url = 'http://www.bom.gov.au/gms/IDE00005.gif'
        outfile = os.path.join(params.FILE_PATH, 'SSO_Satellite_Image.gif')
        fake_agent = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'  # Might cause issues
        req = urllib.request.Request(image_url, headers={'User-Agent': fake_agent})
        try:
            with urllib.request.urlopen(req, timeout=5) as url:
                data = url.read()
            with open(outfile, 'wb') as f:
                f.write(data)
        except Exception:
            pass

        try:
            ret, img = cv2.VideoCapture(outfile).read()
            # Crop SSO area
            img_crop = img[268:292, 450:475]
            # Mask lat/long lines
            img_masked = np.ma.masked_equal(img_crop, 127)
        except Exception:
            raise

    # Average over colour channels
    img_av = np.ma.mean(img_masked, axis=1)

    # Measure the median pixel value, and scale by the pixel range (0-255)
    median = np.ma.median(img_av) / 255
    return median
