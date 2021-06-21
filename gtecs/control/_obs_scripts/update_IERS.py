#!/usr/bin/env python3
"""Script to make sure astropy IERS tables are up-to-date."""

import traceback

from astropy.utils.data import clear_download_cache, download_file

from gtecs.control import params


def run():
    """Update the system IERS table."""
    try:
        print('Downloading IERS_A table...')
        clear_download_cache(params.IERS_A_URL)  # This makes sure nothing's using them
        download_file(params.IERS_A_URL, cache=True, show_progress=False)
        print('IERS A table updated')

    except Exception:
        # Server is down, try the backup
        try:
            print('Normal URL failed, attempting to use backup...')
            clear_download_cache(params.IERS_A_URL_BACKUP)
            download_file(params.IERS_A_URL_BACKUP, cache=True, show_progress=False)
            print('IERS A table updated')
        except Exception:
            print('Error: could not download IERS A tables')
            traceback.print_exc()


if __name__ == '__main__':
    run()
