"""Conditions utility functions."""

import os
import ssl
import urllib.request

from .. import params


def download_data_from_url(url, outfile, timeout=5, encoding='utf-8', verify=True):
    """Fetch data from a URL, store it in a file and return the contents."""
    if not verify:
        context = ssl._create_unverified_context()
    else:
        context = None

    outfile = os.path.join(params.FILE_PATH, outfile)

    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as r:
            data = r.read().decode(encoding)
        with open(outfile, 'w', encoding=encoding) as f:
            f.write(data)
    except Exception:
        pass

    with open(outfile, 'r', encoding=encoding) as f:
        data = f.read()
    return data
