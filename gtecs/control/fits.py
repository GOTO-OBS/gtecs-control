"""Functions to write FITS image files."""

import glob
import math
import os
import threading
import time
import warnings

import astropy.units as u
from astropy.coordinates import Angle
from astropy.io import fits
from astropy.time import Time

import numpy as np

from . import misc
from . import params
from .analysis import get_focus_region, measure_image_hfd
from .astronomy import get_lst, night_startdate
from .daemons import daemon_proxy
from .flags import Status
from .scheduling import get_pointing_info


def fits_filename(tel_number, run_number, ut_number):
    """Construct the FITS file name."""
    if run_number is not None:
        return f't{tel_number:d}_r{run_number:07d}_ut{ut_number:d}.fits'
    else:
        return f't{tel_number:d}_glance_ut{ut_number:d}.fits'


def image_filename(tel_number, run_number, ut_number):
    """Construct the FITS image file name."""
    return fits_filename(tel_number, run_number, ut_number)


def glance_filename(tel_number, ut_number):
    """Construct the FITS glance file name."""
    return fits_filename(tel_number, None, ut_number)


def image_location(run_number, ut_number, tel_number=None):
    """Construct the image file location."""
    # Use the default tel number if not given
    if tel_number is None:
        tel_number = params.TELESCOPE_NUMBER

    # Find the directory, using the date the observing night began (the previous local midday)
    night = night_startdate()
    direc = os.path.join(params.IMAGE_PATH, night)
    if not os.path.exists(direc):
        os.mkdir(direc)

    # Find the file name, using the telescope, run and UT numbers
    filename = image_filename(tel_number, run_number, ut_number)
    return os.path.join(direc, filename)


def glance_location(ut_number, tel_number=None):
    """Construct the glance file location."""
    # Use the default tel number if not given
    if tel_number is None:
        tel_number = params.TELESCOPE_NUMBER

    # Find the directory
    direc = params.IMAGE_PATH
    if not os.path.exists(direc):
        os.mkdir(direc)

    # Find the file name, using the telescope and UT numbers
    filename = glance_filename(tel_number, ut_number)
    return os.path.join(direc, filename)


def clear_glance_files(tel_number=None):
    """Delete any existing glance files."""
    # Use the default tel number if not given
    if tel_number is None:
        tel_number = params.TELESCOPE_NUMBER

    # Find the directory
    direc = params.IMAGE_PATH
    if not os.path.exists(direc):
        os.mkdir(direc)

    # Remove glances for ALL UTs
    for ut in params.UTS_WITH_CAMERAS:
        filename = glance_location(ut, tel_number)
        if os.path.exists(filename):
            os.remove(filename)


def make_fits(image_data,
              header_cards=None, compress=False,
              include_stats=True, measure_hfds=False, hfd_regions=None,
              log=None):
    """Format and update a FITS HDU for the image."""
    # Create the hdu
    if compress:
        hdu = fits.CompImageHDU(image_data)
    else:
        hdu = fits.PrimaryHDU(image_data)

    # Update the header with any provided cards
    if header_cards is not None:
        hdu.header.update(header_cards)

    # Add basic image statistics to the header if requested
    if include_stats:
        hdu.header['MEANCNTS'] = (np.mean(image_data),
                                  'Mean image counts')
        hdu.header['MEDCNTS '] = (np.median(image_data),
                                  'Median image counts')
        hdu.header['STDCNTS '] = (np.std(image_data),
                                  'Std of image counts')

    # Measure HFDs and add values to the header if requested
    if measure_hfds:
        binning = int(hdu.header['XBINNING'])  # should always be the same as YBINNING
        if hfd_regions is None:
            hfd_regions = [get_focus_region(binning)]
        if len(hfd_regions) > 10:
            log.warning('Too many image regions ({}), restricting to 10.'.format(len(hfd_regions)))
        for i, region in enumerate(hfd_regions):
            try:
                hfd, hfd_std = measure_image_hfd(image_data.astype('int32'),
                                                 region=region,
                                                 filter_width=15 // binning,
                                                 verbose=False)
                # NB HFDs are returned in binned pixels
                hfd *= binning
                hfd_std *= binning
                log.debug(f'Measured image HFD: {hfd:.2f} +/- {hfd_std:.2f}')
                hdu.header['MEDHFD{}'.format(i)] = hfd
                hdu.header['STDHFD{}'.format(i)] = hfd_std
            except Exception:
                log.exception('Could not measure image HFDs')

    return hdu


def save_fits(hdu, filename, log=None, log_debug=False, fancy_log=True):
    """Save a FITS HDU to a file."""
    # Remove any existing file
    try:
        os.remove(filename)
        if log and log_debug:
            log.debug(f'Removed {filename} as it already existed')
    except FileNotFoundError:
        pass

    # Create the hdulist
    if not isinstance(hdu, fits.PrimaryHDU):
        if log and log_debug:
            log.debug('Creating HDUList as Primary HDU with no PrimaryHDU instance')
        hdulist = fits.HDUList([fits.PrimaryHDU(), hdu])
    else:
        if log and log_debug:
            log.debug('Creating HDUList as standard HDU with built-in PrimaryHDU instance')
        hdulist = fits.HDUList([hdu])

    # Write to a tmp file, then move it once it's finished (removes the need for .done files)
    try:
        hdulist.writeto(filename + '.tmp')
        if log and log_debug:
            log.debug(f'Wrote file to {filename+".tmp"}')
    except Exception:
        if log is None:
            raise
        log.error('Failed to write hdulist to file')
        log.debug('', exc_info=True)
    else:
        os.rename(filename + '.tmp', filename)
        if log and log_debug:
            log.debug(f'Moved file to {filename}')

    if fancy_log:
        # Log image being saved
        if not hdu.header['GLANCE']:
            expstr = 'Exposure r{:07d}'.format(int(hdu.header['RUN']))
        else:
            expstr = 'Glance'
        if log:
            log.info('{}: Saved exposure from camera {}'.format(expstr, hdu.header['UT']))
        else:
            print('{}: Saved exposure from camera {}'.format(expstr, hdu.header['UT']))


def get_daemon_info(cam_info=None, timeout=60, log=None, log_debug=False):
    """Get all info dicts from the running daemons, and other common info."""
    info_time = Time.now()
    daemon_info = {}
    bad_daemons = []

    daemons = ['ota', 'foc', 'filt', 'dome', 'mnt', 'conditions']
    if cam_info is None:
        daemons.append('cam')
    else:
        # Use the given camera info, if we're already calling this from the cam daemon then
        # we don't want to call it again
        daemon_info['cam'] = cam_info

    # Get the info from the daemons in parallel to save time
    def daemon_info_thread(daemon_id, timeout=60, log=None, log_debug=False):
        try:
            if log and log_debug:
                log.debug(f'Fetching "{daemon_id}" info')
            force_update = bool(daemon_id != 'conditions')
            with daemon_proxy(daemon_id, timeout=timeout) as daemon:
                daemon_info[daemon_id] = daemon.get_info(force_update)
            if log and log_debug:
                log.debug(f'Fetched "{daemon_id}" info')
        except Exception:
            if log is None:
                raise
            log.error(f'Failed to fetch "{daemon_id}" info')
            log.debug('', exc_info=True)
            daemon_info[daemon_id] = None
            bad_daemons.append(daemon_id)

    threads = [threading.Thread(target=daemon_info_thread,
                                args=(daemon_id, timeout, log, log_debug))
               for daemon_id in daemons]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Check that there is a current exposure, otherwise we can't get all the info
    # (like exposure time times for the history)
    if ('current_exposure' not in daemon_info['cam'] or
            daemon_info['cam']['current_exposure'] is None):
        raise ValueError('No current exposure details in camera info dict')

    # Mount history
    if daemon_info['mnt'] is not None:
        try:
            exptime = daemon_info['cam']['current_exposure']['exptime']

            # Position error history
            poserr_info = {}
            poserr_info['hist_time'] = -999
            poserr_info['ra_max'] = 'NA'
            poserr_info['ra_mean'] = 'NA'
            poserr_info['ra_std'] = 'NA'
            poserr_info['dec_max'] = 'NA'
            poserr_info['dec_mean'] = 'NA'
            poserr_info['dec_std'] = 'NA'
            if daemon_info['mnt']['position_error_history'] is not None:
                # Get lookback time
                max_hist = info_time.unix - daemon_info['mnt']['position_error_history'][0][0]
                hist_time = params.MIN_HEADER_HIST_TIME
                if exptime > hist_time:
                    hist_time = exptime
                if hist_time > max_hist:
                    hist_time = max_hist
                poserr_info['hist_time'] = hist_time
                # Get RA history values
                ra_hist = [h[1]['ra'] for h in daemon_info['mnt']['position_error_history']
                           if info_time.unix - h[0] <= hist_time]
                if len(ra_hist) > 0:
                    poserr_info['ra_hist'] = ra_hist
                    poserr_info['ra_max'] = np.max(ra_hist)
                    poserr_info['ra_mean'] = np.mean(ra_hist)
                    poserr_info['ra_std'] = np.std(ra_hist)
                # Get Dec history values
                dec_hist = [h[1]['dec'] for h in daemon_info['mnt']['position_error_history']
                            if info_time.unix - h[0] <= hist_time]
                if len(dec_hist) > 0:
                    poserr_info['dec_hist'] = dec_hist
                    poserr_info['dec_max'] = np.max(dec_hist)
                    poserr_info['dec_mean'] = np.mean(dec_hist)
                    poserr_info['dec_std'] = np.std(dec_hist)
            daemon_info['mnt']['position_error_info'] = poserr_info

            # Tracking error history
            trackerr_info = {}
            trackerr_info['hist_time'] = -999
            trackerr_info['ra_max'] = 'NA'
            trackerr_info['ra_mean'] = 'NA'
            trackerr_info['ra_std'] = 'NA'
            trackerr_info['dec_max'] = 'NA'
            trackerr_info['dec_mean'] = 'NA'
            trackerr_info['dec_std'] = 'NA'
            if daemon_info['mnt']['tracking_error_history'] is not None:
                # Get lookback time
                max_hist = info_time.unix - daemon_info['mnt']['tracking_error_history'][0][0]
                hist_time = params.MIN_HEADER_HIST_TIME
                if exptime > hist_time:
                    hist_time = exptime
                if hist_time > max_hist:
                    hist_time = max_hist
                trackerr_info['hist_time'] = hist_time
                # Get RA history values
                ra_hist = [h[1]['ra'] for h in daemon_info['mnt']['tracking_error_history']
                           if info_time.unix - h[0] <= hist_time]
                if len(ra_hist) > 0:
                    trackerr_info['ra_hist'] = ra_hist
                    trackerr_info['ra_max'] = np.max(ra_hist)
                    trackerr_info['ra_mean'] = np.mean(ra_hist)
                    trackerr_info['ra_std'] = np.std(ra_hist)
                # Get Dec history values
                dec_hist = [h[1]['dec'] for h in daemon_info['mnt']['tracking_error_history']
                            if info_time.unix - h[0] <= hist_time]
                if len(dec_hist) > 0:
                    trackerr_info['dec_hist'] = dec_hist
                    trackerr_info['dec_max'] = np.max(dec_hist)
                    trackerr_info['dec_mean'] = np.mean(dec_hist)
                    trackerr_info['dec_std'] = np.std(dec_hist)
            daemon_info['mnt']['tracking_error_info'] = trackerr_info

            # Motor current history
            current_info = {}
            current_info['hist_time'] = -999
            current_info['ra_max'] = 'NA'
            current_info['ra_mean'] = 'NA'
            current_info['ra_std'] = 'NA'
            current_info['dec_max'] = 'NA'
            current_info['dec_mean'] = 'NA'
            current_info['dec_std'] = 'NA'
            if daemon_info['mnt']['motor_current_history'] is not None:
                # Get lookback time
                max_hist = info_time.unix - daemon_info['mnt']['motor_current_history'][0][0]
                hist_time = params.MIN_HEADER_HIST_TIME
                if exptime > hist_time:
                    hist_time = exptime
                if hist_time > max_hist:
                    hist_time = max_hist
                current_info['hist_time'] = hist_time
                # Get RA history values
                ra_hist = [h[1]['ra'] for h in daemon_info['mnt']['motor_current_history']
                           if info_time.unix - h[0] <= hist_time]
                if len(ra_hist) > 0:
                    current_info['ra_hist'] = ra_hist
                    current_info['ra_max'] = np.max(ra_hist)
                    current_info['ra_mean'] = np.mean(ra_hist)
                    current_info['ra_std'] = np.std(ra_hist)
                # Get Dec history values
                dec_hist = [h[1]['dec'] for h in daemon_info['mnt']['motor_current_history']
                            if info_time.unix - h[0] <= hist_time]
                if len(dec_hist) > 0:
                    current_info['dec_hist'] = dec_hist
                    current_info['dec_max'] = np.max(dec_hist)
                    current_info['dec_mean'] = np.mean(dec_hist)
                    current_info['dec_std'] = np.std(dec_hist)
            daemon_info['mnt']['motor_current_info'] = current_info

        except Exception:
            if log is None:
                raise
            log.error('Failed to calculate mount history info')
            log.debug('', exc_info=True)
            daemon_info['mnt']['position_error_info'] = None
            daemon_info['mnt']['tracking_error_info'] = None
            daemon_info['mnt']['motor_current_info'] = None
            bad_daemons.append('mnt_history')

    # Conditions sources
    if daemon_info['conditions'] is not None:
        try:
            # Select external source
            ext_source = params.VAISALA_URI_PRIMARY[5:].split('_')[0]
            ext_weather = daemon_info['conditions']['weather'][ext_source].copy()
            daemon_info['conditions']['weather_ext'] = ext_weather

            # Select internal source
            int_weather = daemon_info['conditions']['internal'].copy()
            daemon_info['conditions']['weather_int'] = int_weather

        except Exception:
            if log is None:
                raise
            log.error('Failed to find conditions sources')
            log.debug('', exc_info=True)
            daemon_info['conditions']['weather_ext'] = None
            daemon_info['conditions']['weather_int'] = None
            bad_daemons.append('conditions_sources')

    # Conditions history
    if (daemon_info['conditions'] is not None and
            daemon_info['conditions']['weather_ext'] is not None):
        try:
            exptime = daemon_info['cam']['current_exposure']['exptime']

            # Wind gust history
            hist_info = {}
            hist_info['hist_time'] = -999
            hist_info['max'] = 'NA'
            hist_info['mean'] = 'NA'
            hist_info['std'] = 'NA'
            history = daemon_info['conditions']['weather_ext']['windgust_history']
            if history != -999:
                # Get lookback time
                max_hist = info_time.unix - history[0][0]
                hist_time = params.MIN_HEADER_HIST_TIME
                if exptime > hist_time:
                    hist_time = exptime
                if hist_time > max_hist:
                    hist_time = max_hist
                hist_info['hist_time'] = hist_time
                # Get gust history values
                gust_hist = [h[1] for h in history if info_time.unix - h[0] <= hist_time]
                if len(gust_hist) > 0:
                    hist_info['hist'] = gust_hist
                    hist_info['max'] = np.max(gust_hist)
                    hist_info['mean'] = np.mean(gust_hist)
                    hist_info['std'] = np.std(gust_hist)
            daemon_info['conditions']['weather_ext']['windgust_history_info'] = hist_info

        except Exception:
            if log is None:
                raise
            log.error('Failed to calculate conditions history info')
            log.debug('', exc_info=True)
            daemon_info['conditions']['weather_ext']['windgust_history_info'] = None
            bad_daemons.append('conditions_history')

    # Database
    if daemon_info['cam']['current_exposure']['set_id'] is not None:
        try:
            if log and log_debug:
                log.debug('Fetching database info')
            db_info = {}
            db_info['from_database'] = True
            db_info['expset_id'] = daemon_info['cam']['current_exposure']['set_id']
            db_info['pointing_id'] = daemon_info['cam']['current_exposure']['pointing_id']

            # Get Pointing info from the scheduler
            pointing_info = get_pointing_info(db_info['pointing_id'])
            db_info.update(pointing_info)

            # Check IDs match
            if db_info['pointing_id'] != pointing_info['id']:
                raise ValueError('Pointing ID {} does not match {}'.format(
                    db_info['pointing_id'], pointing_info['id']))
            else:
                del db_info['id']

            daemon_info['db'] = db_info
            if log and log_debug:
                log.debug('Fetched database info')
        except Exception:
            if log is None:
                raise
            log.error('Failed to fetch database info')
            log.debug('', exc_info=True)
            daemon_info['db'] = None
            bad_daemons.append('database')
    else:
        db_info = {}
        db_info['from_database'] = False
        daemon_info['db'] = db_info

    # Other params (do this here to ensure they're the same for all UTs)
    params_info = {}
    params_info['version'] = params.VERSION
    params_info['org_name'] = params.ORG_NAME
    params_info['site_name'] = params.SITE_NAME
    params_info['site_lat'] = params.SITE_LATITUDE
    params_info['site_lon'] = params.SITE_LONGITUDE
    params_info['site_alt'] = params.SITE_ALTITUDE
    params_info['tel_name'] = params.TELESCOPE_NAME
    params_info['tel_number'] = params.TELESCOPE_NUMBER

    params_info['ut_dict'] = params.UT_DICT
    ut_mask = misc.ut_list_to_mask(daemon_info['cam']['current_exposure']['uts'])
    params_info['ut_mask'] = ut_mask
    params_info['ut_string'] = misc.ut_mask_to_string(ut_mask)
    params_info['uts_with_covers'] = params.UTS_WITH_COVERS
    params_info['uts_with_focusers'] = params.UTS_WITH_FOCUSERS
    params_info['uts_with_filterwheels'] = params.UTS_WITH_FILTERWHEELS

    status = Status()
    params_info['system_mode'] = status.mode
    params_info['observer'] = status.observer

    daemon_info['params'] = params_info

    return daemon_info, bad_daemons


def make_header(ut, daemon_info=None):
    """Generate FITS header cards containing all observation, exposure and hardware info."""
    # Get daemon info if not provided
    if daemon_info is None:
        daemon_info = get_daemon_info()

    # Store header cards in a list
    header = []
    # NB: FITS standard keywords can only be 8 columns (bytes, essentially characters),
    #     and 80 total for the keyword, value and any comment.
    # We will verify all cards at the end.

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Observation info and basic parameters
    if daemon_info.get('cam') is None:
        raise ValueError('No camera info provided')
    if daemon_info['cam'].get(ut) is None:
        raise ValueError(f'No camera info provided for UT{ut}')
    if daemon_info['cam'].get('current_exposure') is None:
        raise ValueError('No exposure info provided')

    # Run numbers
    if not daemon_info['cam']['current_exposure']['glance']:
        run_number = daemon_info['cam']['current_exposure']['run_number']
        run_number_str = f'r{run_number:07d}'
    else:
        run_number = 'NA'
        run_number_str = 'NA'
    header.append(('RUN     ', run_number,
                   'GOTO run number'))
    header.append(('RUN-ID  ', run_number_str,
                   'Padded run ID string'))

    # Origin
    header.append(('DATE    ', Time.now().isot,
                   'Date HDU created'))
    header.append(('ORIGIN  ', daemon_info['params']['org_name'],
                   'Origin organisation'))
    header.append(('SITE    ', daemon_info['params']['site_name'],
                   'Site location'))
    header.append(('SITE-LAT', daemon_info['params']['site_lat'],
                   'Site latitude, degrees +N'))
    header.append(('SITE-LON', daemon_info['params']['site_lon'],
                   'Site longitude, degrees +E'))
    header.append(('SITE-ALT', daemon_info['params']['site_alt'],
                   'Site elevation, m above sea level'))
    header.append(('TELESCOP', daemon_info['params']['tel_name'],
                   'Origin telescope name'))
    header.append(('TEL     ', daemon_info['params']['tel_number'],
                   'Origin telescope ID number'))

    # UT
    header.append(('INSTRUME', f'UT{ut}',
                   'Origin unit telescope'))
    header.append(('UT      ', ut,
                   'Integer UT number'))
    if 'HW_VERSION' in daemon_info['params']['ut_dict'][ut]:
        ut_hw_version = daemon_info['params']['ut_dict'][ut]['HW_VERSION']
    else:
        ut_hw_version = 'NA'
    header.append(('UT-VERS ', ut_hw_version,
                   'UT hardware version number'))
    header.append(('UTMASK  ', daemon_info['params']['ut_mask'],
                   'Run UT mask integer'))
    header.append(('UTMASKBN', daemon_info['params']['ut_string'],
                   'Run UT mask binary string'))

    # Software
    header.append(('SWVN    ', daemon_info['params']['version'],
                   'Software version number'))
    header.append(('SYS-MODE', daemon_info['params']['system_mode'],
                   'Current telescope system mode'))

    # Observation
    header.append(('OBSERVER', daemon_info['params']['observer'],
                   'Who started the exposure'))
    header.append(('OBJECT  ', daemon_info['cam']['current_exposure']['target'],
                   'Observed object name'))

    # Set info
    set_number = daemon_info['cam']['current_exposure']['set_num']
    if set_number is None:
        set_number = 'NA'
    header.append(('SET     ', set_number,
                   'GOTO set number'))
    header.append(('SET-POS ', daemon_info['cam']['current_exposure']['set_pos'],
                   'Position of this exposure in this set'))
    header.append(('SET-TOT ', daemon_info['cam']['current_exposure']['set_tot'],
                   'Total number of exposures in this set'))

    # Exposure times
    header.append(('EXPTIME ', daemon_info['cam']['current_exposure']['exptime'],
                   'Exposure time, seconds'))
    start_time = Time(daemon_info['cam'][ut]['exposure_start_time'], format='unix')
    mid_time = start_time + (daemon_info['cam']['current_exposure']['exptime'] * u.second) / 2.
    header.append(('DATE-OBS', start_time.isot,
                   'Exposure start time, UTC'))
    header.append(('DATE-MID', mid_time.isot,
                   'Exposure midpoint, UTC'))
    header.append(('JD      ', mid_time.jd,
                   'Exposure midpoint, Julian Date'))
    header.append(('LST     ', '{:02.0f}:{:02.0f}:{:06.3f}'.format(*get_lst(mid_time).hms),
                   'Exposure midpoint, Local Sidereal Time'))

    # Frame info
    header.append(('FRMTYPE ', daemon_info['cam']['current_exposure']['frametype'],
                   'Frame type (shutter open/closed)'))
    header.append(('IMGTYPE ', daemon_info['cam']['current_exposure']['imgtype'],
                   'Image type'))
    header.append(('GLANCE  ', daemon_info['cam']['current_exposure']['glance'],
                   'Is this a glance frame?'))
    # Following section info is depreciated:
    header.append(('FULLSEC ', '[1:8304,1:6220]',
                   'Size of the full frame'))
    header.append(('TRIMSEC ', '[65:8240,46:6177]',
                   'Central data region (both channels)'))
    header.append(('TRIMSEC1', '[65:4152,46:6177]',
                   'Data section for left channel'))
    header.append(('TRIMSEC2', '[4153:8240,46:6177]',
                   'Data section for right channel'))
    header.append(('BIASSEC1', '[3:10,3:6218]',
                   'Recommended bias section for left channel'))
    header.append(('BIASSEC2', '[8295:8302,3:6218]',
                   'Recommended bias section for right channel'))
    header.append(('DARKSEC1', '[26:41,500:5721]',
                   'Recommended dark section for left channel'))
    header.append(('DARKSEC2', '[8264:8279,500:5721]',
                   'Recommended dark section for right channel'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Database info
    if daemon_info.get('db') is None:
        raise ValueError('No database info provided')

    # Was this pointing linked to the database (darks, flats etc are False)?
    header.append(('FROMDB  ', daemon_info['db']['from_database'],
                   'Exposure linked to database pointing?'))

    # DB ExposureSet properties
    if daemon_info['db']['from_database']:
        expset_id = daemon_info['db']['expset_id']  # from Exposure
    else:
        expset_id = 'NA'
    header.append(('DB-EXPS ', expset_id,
                   'Database ExposureSet ID'))

    # DB Pointing properties
    if daemon_info['db']['from_database']:
        pointing_id = daemon_info['db']['pointing_id']
        if daemon_info['db']['rank'] is not None:
            rank = daemon_info['db']['rank']
        else:
            rank = 'inf'
        if daemon_info['db']['start_time'] is not None:
            start_time = daemon_info['db']['start_time']
        else:
            start_time = 'NA'
        if daemon_info['db']['stop_time'] is not None:
            stop_time = daemon_info['db']['stop_time']
        else:
            stop_time = 'NA'
    else:
        pointing_id = 'NA'
        rank = 'NA'
        start_time = 'NA'
        stop_time = 'NA'
    header.append(('DB-PNT  ', pointing_id,
                   'Database Pointing ID'))
    header.append(('RANK    ', rank,
                   'Rank of this pointing when observed'))
    header.append(('LIM-STRT', start_time,
                   'Valid start time limit for this pointing'))
    header.append(('LIM-STOP', stop_time,
                   'Valid stop time limit for this pointing'))

    # DB Target properties
    if daemon_info['db']['from_database']:
        target_id = daemon_info['db']['target_id']
        if daemon_info['db']['start_rank'] is not None:
            initial_rank = daemon_info['db']['start_rank']
        else:
            initial_rank = 'inf'
        weight = daemon_info['db']['weight']
        num_observed = daemon_info['db']['num_completed']
        is_template = daemon_info['db']['is_template']
    else:
        target_id = 'NA'
        initial_rank = 'NA'
        weight = 'NA'
        num_observed = 'NA'
        is_template = 'NA'
    header.append(('DB-TARG ', target_id,
                   'Database Target ID'))
    header.append(('BASERANK', initial_rank,
                   'Initial rank of this Target'))
    header.append(('WEIGHT  ', weight,
                   'Target weighting'))
    header.append(('OBSNUM  ', num_observed,
                   'Count of times this Target has been observed'))
    header.append(('IS-TMPL ', is_template,
                   'Is this Pointing a template observation?'))

    # DB Strategy properties
    if daemon_info['db']['from_database']:
        strategy_id = daemon_info['db']['strategy_id']
        infinite = daemon_info['db']['infinite']
        if daemon_info['db']['min_time'] is not None:
            min_time = daemon_info['db']['min_time']
        else:
            min_time = 'NA'
        too = daemon_info['db']['too']
        requires_template = daemon_info['db']['requires_template']
        min_alt = daemon_info['db']['min_alt']
        max_sunalt = daemon_info['db']['max_sunalt']
        max_moon = daemon_info['db']['max_moon']
        min_moonsep = daemon_info['db']['min_moonsep']
    else:
        strategy_id = 'NA'
        infinite = 'NA'
        min_time = 'NA'
        too = 'NA'
        requires_template = 'NA'
        min_alt = 'NA'
        max_sunalt = 'NA'
        max_moon = 'NA'
        min_moonsep = 'NA'
    header.append(('DB-STRAT', strategy_id,
                   'Database Strategy ID'))
    header.append(('INFINITE', infinite,
                   'Is this an infinitely repeating pointing?'))
    header.append(('LIM-TIME', min_time,
                   'Minimum observing time for this pointing'))
    header.append(('TOO     ', too,
                   'Is this Pointing a Target of Opportunity?'))
    header.append(('REQ-TMPL', requires_template,
                   'Did this Pointing require a template?'))
    header.append(('LIM-ALT ', min_alt,
                   'Minimum altitude limit for this pointing'))
    header.append(('LIM-SALT', max_sunalt,
                   'Maximum Sun altitude limit for this pointing'))
    header.append(('LIM-MPHS', max_moon,
                   'Maximum Moon phase limit for this pointing'))
    header.append(('LIM-MDIS', min_moonsep,
                   'Minimum Moon distance limit for this pointing'))

    # DB TimeBlock properties
    if daemon_info['db']['from_database']:
        time_block_id = daemon_info['db']['time_block_id']
        block_num = daemon_info['db']['block_num']
        if daemon_info['db']['wait_time'] is not None:
            wait_time = daemon_info['db']['wait_time']
        else:
            wait_time = 'NA'
        if daemon_info['db']['valid_time'] is not None:
            valid_time = daemon_info['db']['valid_time']
        else:
            valid_time = 'NA'
    else:
        time_block_id = 'NA'
        block_num = 'NA'
        wait_time = 'NA'
        valid_time = 'NA'
    header.append(('DB-TIMBK', time_block_id,
                   'Database TimeBlock ID'))
    header.append(('TIMBKNUM', block_num,
                   'Number of this time block'))
    header.append(('TIMEVALD', wait_time,
                   'How long this Pointing is valid in the queue'))
    header.append(('TIMEWAIT', valid_time,
                   'How long between Pointings for this Target'))

    # DB User properties
    if daemon_info['db']['from_database']:
        user_id = daemon_info['db']['user_id']
        user_name = daemon_info['db']['user_name']
        user_fullname = daemon_info['db']['user_fullname']
    else:
        user_id = 'NA'
        user_name = 'NA'
        user_fullname = 'NA'
    header.append(('DB-USER ', user_id,
                   'Database User ID who submitted this pointing'))
    header.append(('USERNAME', user_name,
                   'Username that submitted this pointing'))
    header.append(('USERFULL', user_fullname,
                   'User who submitted this pointing'))

    # DB Grid properties (optional)
    if daemon_info['db']['from_database'] and daemon_info['db']['grid_id'] is not None:
        grid_id = daemon_info['db']['grid_id']
        grid_name = daemon_info['db']['grid_name']
        tile_id = daemon_info['db']['tile_id']
        tile_name = daemon_info['db']['tile_name']
    else:
        grid_id = 'NA'
        grid_name = 'NA'
        tile_id = 'NA'
        tile_name = 'NA'
    header.append(('DB-GRID ', grid_id,
                   'Database Grid ID'))
    header.append(('GRID    ', grid_name,
                   'Sky grid name'))
    header.append(('DB-GTILE', tile_id,
                   'Database GridTile ID'))
    header.append(('TILENAME', tile_name,
                   'Name of this grid tile'))

    # DB Survey properties (optional)
    if daemon_info['db']['from_database'] and daemon_info['db']['survey_id'] is not None:
        survey_id = daemon_info['db']['survey_id']
        survey_name = daemon_info['db']['survey_name']
    else:
        survey_id = 'NA'
        survey_name = 'NA'
    header.append(('DB-SURVY', survey_id,
                   'Database Survey ID'))
    header.append(('SURVEY  ', survey_name,
                   'Name of this survey'))

    # DB Notice/Event properties (optional)
    if daemon_info['db']['from_database'] and daemon_info['db']['notice_id'] is not None:
        notice_id = daemon_info['db']['notice_id']
        notice_ivorn = daemon_info['db']['notice_ivorn']
        if daemon_info['db']['notice_time'] is not None:
            notice_time = daemon_info['db']['notice_time']
        else:
            notice_time = 'NA'
        event_id = daemon_info['db']['event_id']
        event_name = daemon_info['db']['event_name']
        event_type = daemon_info['db']['event_type']
        event_origin = daemon_info['db']['event_origin']
        if daemon_info['db']['event_time'] is not None:
            event_time = daemon_info['db']['event_time']
        else:
            event_time = 'NA'
    else:
        notice_id = 'NA'
        notice_ivorn = 'NA'
        notice_time = 'NA'
        event_id = 'NA'
        event_name = 'NA'
        event_type = 'NA'
        event_origin = 'NA'
        event_time = 'NA'
    header.append(('DB-NOTIC', notice_id,
                   'Database Notice ID'))
    header.append(('IVORN   ', notice_ivorn,
                   'GCN Notice IVORN'))
    header.append(('RCVTIME ', notice_time,
                   'Time the GCN Notice was received'))
    header.append(('DB-EVENT', event_id,
                   'Database Event ID'))
    header.append(('EVENT   ', event_name,
                   'Event name for this pointing'))
    header.append(('EVNTTYPE', event_type,
                   'Type of event'))
    header.append(('SOURCE  ', event_origin,
                   'Source of this event'))
    header.append(('EVNTTIME', event_time,
                   'Recorded time of the event'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Camera info (already checked that it's included above)

    # Hardware
    header.append(('CAMERA  ', daemon_info['cam'][ut]['serial_number'],
                   'Camera serial number'))
    header.append(('CAMCLS  ', daemon_info['cam'][ut]['hw_class'],
                   'Camera hardware class'))

    # Binning
    bin_fac = daemon_info['cam']['current_exposure']['binning']
    header.append(('XBINNING', bin_fac,
                   'CCD x binning factor'))
    header.append(('YBINNING', bin_fac,
                   'CCD y binning factor'))
    header.append(('XPIXSZ  ', daemon_info['cam'][ut]['x_pixel_size'] * bin_fac,
                   'Binned x pixel size, m'))
    header.append(('YPIXSZ  ', daemon_info['cam'][ut]['y_pixel_size'] * bin_fac,
                   'Binned y pixel size, m'))

    # Frame regions
    full_area = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(*daemon_info['cam'][ut]['full_area'])
    header.append(('FULLAREA', full_area,
                   'Full frame area in unbinned pixels (x,y,dx,dy)'))
    active_area = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(*daemon_info['cam'][ut]['active_area'])
    header.append(('ACTVAREA', active_area,
                   'Active area in unbinned pixels (x,y,dx,dy)'))
    window_area = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(*daemon_info['cam'][ut]['window_area'])
    header.append(('WINDOW  ', window_area,
                   'Windowed region in unbinned pixels (x,y,dx,dy)'))
    header.append(('CHANNELS', 2,
                   'Number of CCD channels'))  # TODO: this should come from the camera

    # Temperature
    header.append(('CCDTEMP ', daemon_info['cam'][ut]['ccd_temp'],
                   'CCD temperature, C'))
    header.append(('CCDTEMPS', daemon_info['cam'][ut]['target_temp'],
                   'Requested CCD temperature, C'))
    header.append(('BASETEMP', daemon_info['cam'][ut]['base_temp'],
                   'Peltier base temperature, C'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # OTA info
    if daemon_info.get('ota') is None:
        raise ValueError('No OTA info provided')
    if daemon_info['ota'].get(ut) is None:
        raise ValueError(f'No OTA info provided for UT{ut}')

    # Hardware
    header.append(('OTA     ', daemon_info['ota'][ut]['serial_number'],
                   'OTA serial number'))
    header.append(('OTACLS  ', daemon_info['ota'][ut]['hw_class'],
                   'OTA hardware class'))

    # Mirror covers
    if ut in daemon_info['params']['uts_with_covers']:
        cover_position = daemon_info['ota'][ut]['position']
        cover_open = daemon_info['ota'][ut]['position'] == 'full_open'
        if daemon_info['ota'][ut]['last_move_time'] is not None:
            cover_move_time = Time(daemon_info['ota'][ut]['last_move_time'], format='unix').isot
        else:
            cover_move_time = 'NA'
    else:
        cover_position = 'NA'
        cover_open = 'NA'
        cover_move_time = 'NA'
    header.append(('COVSTAT ', cover_position,
                   'Mirror cover position'))
    header.append(('COVOPEN ', cover_open,
                   'Mirror cover is open'))
    header.append(('COVMVT  ', cover_move_time,
                   'Mirror cover latest move time'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Focuser info
    if daemon_info.get('foc') is None:
        raise ValueError('No focuser info provided')
    if ut in daemon_info['params']['uts_with_focusers'] and daemon_info['foc'].get(ut) is None:
        raise ValueError(f'No focuser info provided for UT{ut}')

    # Hardware
    if ut in daemon_info['params']['uts_with_focusers']:
        foc_serial = daemon_info['foc'][ut]['serial_number']
        foc_class = daemon_info['foc'][ut]['hw_class']
    else:
        foc_serial = 'None'
        foc_class = 'NA'
    header.append(('FOCUSER ', foc_serial,
                   'Focuser serial number'))
    header.append(('FOCCLS  ', foc_class,
                   'Focuser hardware class'))

    # Position
    if ut in daemon_info['params']['uts_with_focusers']:
        foc_pos = daemon_info['foc'][ut]['current_pos']
        if daemon_info['foc'][ut]['last_move_time'] is not None:
            foc_move_time = Time(daemon_info['foc'][ut]['last_move_time'], format='unix').isot
        else:
            foc_move_time = 'NA'
    else:
        foc_pos = 'NA'
        foc_move_time = 'NA'
    header.append(('FOCPOS  ', foc_pos,
                   'Focuser motor position'))
    header.append(('FOCMVT  ', foc_move_time,
                   'Focuser latest move time'))

    # Temperature
    if ut in daemon_info['params']['uts_with_focusers']:
        if daemon_info['foc'][ut]['int_temp'] is not None:
            foc_temp_int = daemon_info['foc'][ut]['int_temp']
        else:
            foc_temp_int = 'NA'
        if daemon_info['foc'][ut]['ext_temp'] is not None:
            foc_temp_ext = daemon_info['foc'][ut]['ext_temp']
        else:
            foc_temp_ext = 'NA'
    else:
        foc_temp_int = 'NA'
        foc_temp_ext = 'NA'
    header.append(('FOCTEMPI', foc_temp_int,
                   'Focuser internal temperature, C'))
    header.append(('FOCTEMPX', foc_temp_ext,
                   'Focuser external temperature, C'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Filter wheel info
    if daemon_info.get('filt') is None:
        raise ValueError('No filter wheel info provided')
    if ut in daemon_info['params']['uts_with_filterwheels'] and daemon_info['filt'].get(ut) is None:
        raise ValueError(f'No filter wheel info provided for UT{ut}')

    # Hardware
    if ut in daemon_info['params']['uts_with_filterwheels']:
        filt_serial = daemon_info['filt'][ut]['serial_number']
        filt_class = daemon_info['filt'][ut]['hw_class']
    else:
        filt_serial = 'None'
        filt_class = 'NA'
    header.append(('FLTWHEEL', filt_serial,
                   'Filter wheel serial number'))
    header.append(('FILTCLS ', filt_class,
                   'Filter wheel hardware class'))

    # Filter
    if ut in daemon_info['params']['uts_with_filterwheels']:
        if not daemon_info['filt'][ut]['homed']:
            filt_filter = 'UNHOMED'
        else:
            filt_filter = daemon_info['filt'][ut]['current_filter']
        filt_filters = ','.join(daemon_info['params']['ut_dict'][ut]['FILTERS'])
    else:
        filt_filter = daemon_info['params']['ut_dict'][ut]['FILTERS'][0]
        filt_filters = ','.join(daemon_info['params']['ut_dict'][ut]['FILTERS'])  # Only one?
    header.append(('FILTER  ', filt_filter,
                   'Filter used for exposure'))
    header.append(('FILTERS ', filt_filters,
                   'Filters in filter wheel'))

    # Position
    if ut in daemon_info['params']['uts_with_filterwheels']:
        filt_num = daemon_info['filt'][ut]['current_filter_num']
        filt_pos = daemon_info['filt'][ut]['current_pos']
        if daemon_info['filt'][ut]['last_move_time'] is not None:
            filt_move_time = Time(daemon_info['filt'][ut]['last_move_time'], format='unix').isot
        else:
            filt_move_time = 'NA'
    else:
        filt_num = 'NA'
        filt_pos = 'NA'
        filt_move_time = 'NA'
    header.append(('FILTNUM ', filt_num,
                   'Filter wheel position number'))
    header.append(('FILTPOS ', filt_pos,
                   'Filter wheel motor position'))
    header.append(('FILTMVT ', filt_move_time,
                   'Filter wheel latest move time'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Dome info
    if daemon_info.get('dome') is None:
        raise ValueError('No dome info provided')

    # Poisition
    a_side = daemon_info['dome']['a_side']
    b_side = daemon_info['dome']['b_side']
    if a_side == 'ERROR' or b_side == 'ERROR':
        dome_status = 'ERROR'
    elif a_side == 'closed' and b_side == 'closed':
        dome_status = 'closed'
    elif a_side == 'full_open' and b_side == 'full_open':
        dome_status = 'full_open'
    elif a_side == 'part_open' or b_side == 'part_open':
        dome_status = 'part_open'
    else:
        dome_status = 'ERROR'
    dome_open = daemon_info['dome']['dome'] == 'open'
    dome_shielding = daemon_info['dome']['shielding']
    if daemon_info['dome']['last_move_time'] is not None:
        dome_move_time = Time(daemon_info['dome']['last_move_time'], format='unix').isot
    else:
        dome_move_time = 'NA'
    header.append(('DOMESTAT', dome_status,
                   'Dome status'))
    header.append(('DOMEOPEN', dome_open,
                   'Dome is open'))
    header.append(('DOMESHLD', dome_shielding,
                   'Dome wind shield is active'))
    header.append(('DOMEMVT ', dome_move_time,
                   'Dome latest move time'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Mount info
    if daemon_info.get('mnt') is None:
        raise ValueError('No mount info provided')

    # Target coords (optional)
    targ_ra = daemon_info['mnt']['target_ra']
    targ_dec = daemon_info['mnt']['target_dec']
    if targ_ra is not None:
        targ_ra_str = Angle(targ_ra * u.hour).to_string(sep=':', precision=3, alwayssign=True)
    else:
        targ_ra_str = 'NA'
    if targ_dec is not None:
        targ_dec_str = Angle(targ_dec * u.deg).to_string(sep=':', precision=3, alwayssign=True)
    else:
        targ_dec_str = 'NA'
    header.append(('RA-TARG ', targ_ra_str,
                   'Requested pointing RA'))
    header.append(('DEC-TARG', targ_dec_str,
                   'Requested pointing Dec'))

    # Pointing coords
    mnt_ra = daemon_info['mnt']['mount_ra']
    mnt_dec = daemon_info['mnt']['mount_dec']
    mnt_ra_str = Angle(mnt_ra * u.hour).to_string(sep=':', precision=3, alwayssign=True)
    mnt_dec_str = Angle(mnt_dec * u.deg).to_string(sep=':', precision=3, alwayssign=True)
    header.append(('RA-TEL  ', mnt_ra_str,
                   'Reported mount pointing RA'))
    header.append(('DEC-TEL ', mnt_dec_str,
                   'Reported mount pointing Dec'))

    # Coord exqinox
    header.append(('EQUINOX ', 2000,
                   'RA/Dec equinox, years'))

    # Target dist (optional)
    targ_dist = daemon_info['mnt']['target_dist']
    if targ_dist is None:
        targ_dist = 'NA'
    header.append(('TARGDIST', targ_dist,
                   'Distance from target, degrees'))

    # Alt/Az
    header.append(('ALT     ', daemon_info['mnt']['mount_alt'],
                   'Mount altitude'))
    header.append(('AZ      ', daemon_info['mnt']['mount_az'],
                   'Mount azimuth'))
    header.append(('HA      ', daemon_info['mnt']['mount_ha'],
                   'Hour angle'))

    # Status
    if daemon_info['mnt']['last_move_time'] is not None:
        mnt_move_time = Time(daemon_info['mnt']['last_move_time'], format='unix').isot
    else:
        mnt_move_time = 'NA'
    header.append(('SLEWTIME', mnt_move_time,
                   'Mount latest move time'))
    header.append(('TRACKING', daemon_info['mnt']['status'] == 'Tracking',
                   'Mount is tracking'))
    header.append(('SIDEREAL', not daemon_info['mnt']['nonsidereal'],
                   'Mount is tracking at sidereal rate'))
    header.append(('RA-TRKR ', daemon_info['mnt']['trackrate_ra'],
                   'RA tracking rate (0=sidereal)'))
    header.append(('DEC-TRKR', daemon_info['mnt']['trackrate_dec'],
                   'Dec tracking rate (0=sidereal)'))

    # Position error (+ history)
    if daemon_info['mnt']['position_error_info'] is not None:
        poserr_hist_time = daemon_info['mnt']['position_error_info']['hist_time']
        poserr_ra_max = daemon_info['mnt']['position_error_info']['ra_max']
        poserr_ra_mean = daemon_info['mnt']['position_error_info']['ra_mean']
        poserr_ra_std = daemon_info['mnt']['position_error_info']['ra_std']
        poserr_dec_max = daemon_info['mnt']['position_error_info']['dec_max']
        poserr_dec_mean = daemon_info['mnt']['position_error_info']['dec_mean']
        poserr_dec_std = daemon_info['mnt']['position_error_info']['dec_std']
    else:
        poserr_hist_time = -999
        poserr_ra_max = 'NA'
        poserr_ra_mean = 'NA'
        poserr_ra_std = 'NA'
        poserr_dec_max = 'NA'
        poserr_dec_mean = 'NA'
        poserr_dec_std = 'NA'
    header.append(('RA-PERR ', daemon_info['mnt']['position_error']['ra'],
                   'RA position error'))
    header.append(('RA-PMAX ', poserr_ra_max,
                   'RA max position error (last {:.0f}s)'.format(poserr_hist_time)))
    header.append(('RA-PMEA ', poserr_ra_mean,
                   'RA mean position error (last {:.0f}s)'.format(poserr_hist_time)))
    header.append(('RA-PSTD ', poserr_ra_std,
                   'RA std position error (last {:.0f}s)'.format(poserr_hist_time)))
    header.append(('DEC-PERR', daemon_info['mnt']['position_error']['dec'],
                   'Dec position error'))
    header.append(('DEC-PMAX', poserr_dec_max,
                   'Dec max position error (last {:.0f}s)'.format(poserr_hist_time)))
    header.append(('DEC-PMEA', poserr_dec_mean,
                   'Dec mean position error (last {:.0f}s)'.format(poserr_hist_time)))
    header.append(('DEC-PSTD', poserr_dec_std,
                   'Dec std position error (last {:.0f}s)'.format(poserr_hist_time)))

    # Tracking error (+ history)
    if daemon_info['mnt']['tracking_error_info'] is not None:
        trkerr_hist_time = daemon_info['mnt']['tracking_error_info']['hist_time']
        trkerr_ra_max = daemon_info['mnt']['tracking_error_info']['ra_max']
        trkerr_ra_mean = daemon_info['mnt']['tracking_error_info']['ra_mean']
        trkerr_ra_std = daemon_info['mnt']['tracking_error_info']['ra_std']
        trkerr_dec_max = daemon_info['mnt']['tracking_error_info']['dec_max']
        trkerr_dec_mean = daemon_info['mnt']['tracking_error_info']['dec_mean']
        trkerr_dec_std = daemon_info['mnt']['tracking_error_info']['dec_std']
    else:
        trkerr_hist_time = -999
        trkerr_ra_max = 'NA'
        trkerr_ra_mean = 'NA'
        trkerr_ra_std = 'NA'
        trkerr_dec_max = 'NA'
        trkerr_dec_mean = 'NA'
        trkerr_dec_std = 'NA'
    header.append(('RA-TERR ', daemon_info['mnt']['tracking_error']['ra'],
                   'RA tracking error'))
    header.append(('RA-TMAX ', trkerr_ra_max,
                   'RA max tracking error (last {:.0f}s)'.format(trkerr_hist_time)))
    header.append(('RA-TMEA ', trkerr_ra_mean,
                   'RA mean tracking error (last {:.0f}s)'.format(trkerr_hist_time)))
    header.append(('RA-TSTD ', trkerr_ra_std,
                   'RA std tracking error (last {:.0f}s)'.format(trkerr_hist_time)))
    header.append(('DEC-TERR', daemon_info['mnt']['tracking_error']['dec'],
                   'Dec tracking error'))
    header.append(('DEC-TMAX', trkerr_dec_max,
                   'Dec max tracking error (last {:.0f}s)'.format(trkerr_hist_time)))
    header.append(('DEC-TMEA', trkerr_dec_mean,
                   'Dec mean tracking error (last {:.0f}s)'.format(trkerr_hist_time)))
    header.append(('DEC-TSTD', trkerr_dec_std,
                   'Dec std tracking error (last {:.0f}s)'.format(trkerr_hist_time)))

    # Motor current (+ history)
    if daemon_info['mnt']['motor_current_info'] is not None:
        current_hist_time = daemon_info['mnt']['motor_current_info']['hist_time']
        current_ra_max = daemon_info['mnt']['motor_current_info']['ra_max']
        current_ra_mean = daemon_info['mnt']['motor_current_info']['ra_mean']
        current_ra_std = daemon_info['mnt']['motor_current_info']['ra_std']
        current_dec_max = daemon_info['mnt']['motor_current_info']['dec_max']
        current_dec_mean = daemon_info['mnt']['motor_current_info']['dec_mean']
        current_dec_std = daemon_info['mnt']['motor_current_info']['dec_std']
    else:
        current_hist_time = -999
        current_ra_max = 'NA'
        current_ra_mean = 'NA'
        current_ra_std = 'NA'
        current_dec_max = 'NA'
        current_dec_mean = 'NA'
        current_dec_std = 'NA'
    header.append(('RA-CURR ', daemon_info['mnt']['motor_current']['ra'],
                   'RA motor current'))
    header.append(('RA-CMAX ', current_ra_max,
                   'RA max motor current (last {:.0f}s)'.format(current_hist_time)))
    header.append(('RA-CMEA ', current_ra_mean,
                   'RA mean motor current (last {:.0f}s)'.format(current_hist_time)))
    header.append(('RA-CSTD ', current_ra_std,
                   'RA std motor current (last {:.0f}s)'.format(current_hist_time)))
    header.append(('DEC-CURR', daemon_info['mnt']['motor_current']['dec'],
                   'Dec motor current'))
    header.append(('DEC-CMAX', current_dec_max,
                   'Dec max motor current (last {:.0f}s)'.format(current_hist_time)))
    header.append(('DEC-CMEA', current_dec_mean,
                   'Dec mean motor current (last {:.0f}s)'.format(current_hist_time)))
    header.append(('DEC-CSTD', current_dec_std,
                   'Dec std motor current (last {:.0f}s)'.format(current_hist_time)))

    # Pointing altitude
    airmass = 1 / (math.cos(math.pi / 2 - (daemon_info['mnt']['mount_alt'] * math.pi / 180)))
    header.append(('AIRMASS ', airmass,
                   'Airmass'))
    header.append(('ZENDIST ', 90 - daemon_info['mnt']['mount_alt'],
                   'Distance from zenith, degrees'))

    # Sun/Moon
    header.append(('SUNALT  ', daemon_info['mnt']['sun_alt'],
                   'Current Sun altitude, degrees'))

    header.append(('MOONALT ', daemon_info['mnt']['moon_alt'],
                   'Current Moon altitude, degrees'))
    header.append(('MOONILL ', daemon_info['mnt']['moon_ill'] * 100,
                   'Current Moon illumination, percent'))
    header.append(('MOONPHAS', daemon_info['mnt']['moon_phase'],
                   'Current Moon phase, [DGB]'))
    header.append(('MOONDIST', daemon_info['mnt']['moon_dist'],
                   'Distance from Moon, degrees'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Conditions info
    if daemon_info.get('conditions') is None:
        raise ValueError('No conditions info provided')

    # Site conditions
    clouds = daemon_info['conditions']['clouds']
    if clouds == -999:
        clouds = 'NA'
    seeing = daemon_info['conditions']['tng']['seeing']
    if seeing == -999:
        seeing = 'NA'
    seeing_ing = daemon_info['conditions']['robodimm']['seeing']
    if seeing_ing == -999:
        seeing_ing = 'NA'
    dust = daemon_info['conditions']['tng']['dust']
    if dust == -999:
        dust = 'NA'
    header.append(('SATCLOUD', clouds,
                   'IR satellite cloud opacity, percent (sat24.com)'))
    header.append(('SEEING  ', seeing,
                   'Seeing, arcseconds (TNG DIMM)'))
    header.append(('SEEING2 ', seeing_ing,
                   'Seeing, arcseconds (ING RoboDIMM)'))
    header.append(('DUST    ', dust,
                   'Dust level, ug/m3 (TNG)'))

    # External conditions
    ext_temp = daemon_info['conditions']['weather_ext']['temperature']
    if ext_temp == -999:
        ext_temp = 'NA'
    ext_hum = daemon_info['conditions']['weather_ext']['humidity']
    if ext_hum == -999:
        ext_hum = 'NA'
    header.append(('EXT-TEMP', ext_temp,
                   'External temperature, Celsius (GOTO mast)'))
    header.append(('EXT-HUM ', ext_hum,
                   'External humidity, percent (GOTO mast)'))

    # Wind (+ history)
    ext_wind = daemon_info['conditions']['weather_ext']['windspeed']
    if ext_wind == -999:
        ext_wind = 'NA'
    ext_winddir = daemon_info['conditions']['weather_ext']['winddir']
    if ext_winddir == -999:
        ext_winddir = 'NA'
    ext_gust = daemon_info['conditions']['weather_ext']['windgust']
    if ext_gust == -999:
        ext_gust = 'NA'
    if daemon_info['conditions']['weather_ext']['windgust_history_info'] is not None:
        hist_time = daemon_info['conditions']['weather_ext']['windgust_history_info']['hist_time']
        ext_gustmax = daemon_info['conditions']['weather_ext']['windgust_history_info']['max']
        ext_gustmean = daemon_info['conditions']['weather_ext']['windgust_history_info']['mean']
        ext_guststd = daemon_info['conditions']['weather_ext']['windgust_history_info']['std']
    else:
        hist_time = -999
        ext_gustmax = 'NA'
        ext_gustmean = 'NA'
        ext_guststd = 'NA'
    header.append(('EXT-WIND', ext_wind,
                   'External wind speed, km/h (GOTO mast)'))
    header.append(('EXT-WDIR', ext_winddir,
                   'External wind direction, degrees (GOTO mast)'))
    header.append(('EXT-GUST', ext_gust,
                   'External wind gust, km/h (GOTO mast)'))
    header.append(('EXT-GMAX', ext_gustmax,
                   'Max wind gust, km/h (last {:.0f}s)'.format(hist_time)))
    header.append(('EXT-GMEA', ext_gustmean,
                   'Mean wind gust, km/h (last {:.0f}s)'.format(hist_time)))
    header.append(('EXT-GSTD', ext_guststd,
                   'Std wind gust, km/h (last {:.0f}s)'.format(hist_time)))

    # Internal conditions
    int_temp = daemon_info['conditions']['weather_int']['temperature']
    if int_temp == -999:
        int_temp = 'NA'
    int_hum = daemon_info['conditions']['weather_int']['humidity']
    if int_hum == -999:
        int_hum = 'NA'
    header.append(('INT-TEMP', int_temp,
                   'Internal temperature, Celsius (dome)'))
    header.append(('INT-HUM ', int_hum,
                   'Internal humidity, percent (dome)'))

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Now verify all the cards and raise an exception if any aren't valid
    for card in header:
        fits.Card(*card).verify('exception')

    # It would be great to return a list of Cards, but they then couldn't be sent to the interfaces
    # if they're not Pyro picklable.
    # So we just return the list of tuples, and add them to the header when created.
    return header


def read_fits(filepath, dtype='int32'):
    """Load a FITS file."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            data = fits.getdata(filepath).astype(dtype)
    except (TypeError, OSError):
        # Image was still being written, wait a sec and try again
        time.sleep(1)
        data = fits.getdata(filepath).astype(dtype)

    return data


def get_image_data(run_number=None, direc=None, uts=None, timeout=None):
    """Open the most recent images and return the data.

    Parameters
    ----------
    run_number : int, default=None
        the run number of the files to open
        if None (and glance=False), open the latest images from `direc`
    direc : string, default=None
        the file directory to load images from within `gtecs.control.params.IMAGE_PATH`
        if None, use the date from `gtecs.control.astronomy.night_startdate`
    uts : list of ints, default=None
        the UTs to read the files of
        if None, open files from all UTs
    timeout : float, default=None
        time in seconds after which to timeout. None to wait forever

    Returns
    -------
    data : dict
        a dictionary of the image data, with the UT numbers as keys

    """
    if direc is None:
        direc = night_startdate()
    path = os.path.join(params.IMAGE_PATH, direc)

    if uts is None:
        uts = params.UTS_WITH_CAMERAS

    if run_number is None:
        newest = max(glob.iglob(os.path.join(path, '*.fits')), key=os.path.getmtime)
        run = os.path.basename(newest).split('_')[1]
        run_number = int(run[1:])

    filenames = {ut: image_filename(params.TELESCOPE_NUMBER, run_number, ut) for ut in uts}
    filepaths = {ut: os.path.join(path, filenames[ut]) for ut in filenames}

    # wait until the images exist, if they don't already
    start_time = time.time()
    files_exist = False
    timed_out = False
    while not files_exist and not timed_out:
        time.sleep(0.2)

        try:
            done = [os.path.exists(filepaths[ut]) for ut in filepaths]
            if np.all(done):
                files_exist = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Image fetching timed out')
    filepaths = {ut: filepaths[ut] for ut in filepaths}

    print('Loading run r{:07d}: {} images'.format(run_number, len(filepaths)))

    # read the files
    data = {ut: read_fits(filepaths[ut]) for ut in filepaths}
    return data


def get_glance_data(uts=None, timeout=None):
    """Open the most recent glance images and return the data.

    Parameters
    ----------
    uts : list of ints, default=None
        the UTs to read the files of
        if None, open files from all UTs
    timeout : float, default=None
        time in seconds after which to timeout. None to wait forever

    Returns
    -------
    data : dict
        a dictionary of the image data, with the UT numbers as keys

    """
    if uts is None:
        uts = params.UTS_WITH_CAMERAS

    filenames = {ut: glance_filename(params.TELESCOPE_NUMBER, ut) for ut in uts}
    filepaths = {ut: os.path.join(params.IMAGE_PATH, filenames[ut]) for ut in filenames}

    # wait until the images exist, if they don't already
    # NOTE this can be an issue since glance files get overwritten, so best to call
    #      `clear_glance_files` first to be sure.
    start_time = time.time()
    files_exist = False
    timed_out = False
    while not files_exist and not timed_out:
        time.sleep(0.2)

        try:
            done = [os.path.exists(filepaths[ut]) for ut in filepaths]
            if np.all(done):
                files_exist = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Image fetching timed out')
    filepaths = {ut: filepaths[ut] for ut in filepaths}

    print('Loading glances: {} images'.format(len(filepaths)))

    # read the files
    data = {ut: read_fits(filepaths[ut]) for ut in filepaths}
    return data
