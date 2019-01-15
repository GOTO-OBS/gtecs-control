#!/usr/bin/env python
"""Script to make sure astropy IERS tables are up-to-date.

update_IERS
"""

import traceback

from astropy.utils.data import clear_download_cache, download_file

IERS_A_URL = 'http://maia.usno.navy.mil/ser7/finals2000A.all'
IERS_A_URL_MIRROR = 'http://toshi.nofs.navy.mil/ser7/finals2000A.all'

try:
    print('Downloading IERS_A table...')
    clear_download_cache(IERS_A_URL)  # This astropy command makes sure nothing's using them
    download_file(IERS_A_URL, cache=True, show_progress=False)
    print('IERS A table updated')

except Exception:
    # Server is down, try the backup
    try:
        print('Normal URL failed, attempting to use backup...')
        clear_download_cache(IERS_A_URL_MIRROR)
        download_file(IERS_A_URL_MIRROR, cache=True, show_progress=False)
        print('IERS A table updated')
    except Exception:
        print('Error: could not download IERS A tables')
        traceback.print_exc()
