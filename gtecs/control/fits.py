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
from .daemons import daemon_info
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

    # Find the directory, using the date the observing night began
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


def make_fits(image_data, ut, all_info, compress=False,
              include_stats=True, measure_hfds=False, hfd_regions=None,
              log=None):
    """Format and update a FITS HDU for the image."""
    # Create the hdu
    if compress:
        hdu = fits.CompImageHDU(image_data)
    else:
        hdu = fits.PrimaryHDU(image_data)

    # Update the image header with info from the daemons
    try:
        update_header(hdu.header, ut, all_info, log)
    except Exception:
        if log is None:
            raise
        log.error('Failed to update FITS header')
        log.debug('', exc_info=True)

    if include_stats:
        hdu.header['MEANCNTS'] = (np.mean(image_data), 'Mean image counts')
        hdu.header['MEDCNTS '] = (np.median(image_data), 'Median image counts')
        hdu.header['STDCNTS '] = (np.std(image_data), 'Std of image counts')

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
        ut = hdu.header['UT      ']
        interface_id = params.UT_DICT[ut]['INTERFACE']
        if not hdu.header['GLANCE  ']:
            expstr = 'Exposure r{:07d}'.format(int(hdu.header['RUN     ']))
        else:
            expstr = 'Glance'
        if log:
            log.info('{}: Saved exposure from camera {} ({})'.format(expstr, ut, interface_id))
        else:
            print('{}: Saved exposure from camera {} ({})'.format(expstr, ut, interface_id))


def get_all_info(cam_info, log=None, log_debug=False):
    """Get all info dicts from the running daemons, and other common info."""
    info_time = Time.now()
    all_info = {}
    bad_info = []

    # Camera daemon
    all_info['cam'] = cam_info
    if 'current_exposure' not in cam_info or cam_info['current_exposure'] is None:
        raise ValueError('No current exposure details in camera info dict')

    # Get the info from the other daemons in parallel to save time
    def daemon_info_thread(daemon_id, log=None, log_debug=False):
        try:
            if log and log_debug:
                log.debug(f'Fetching "{daemon_id}" info')
            force_update = bool(daemon_id != 'conditions')
            all_info[daemon_id] = daemon_info(daemon_id, force_update, timeout=60)
            if log and log_debug:
                log.debug(f'Fetched "{daemon_id}" info')
        except Exception:
            if log is None:
                raise
            log.error(f'Failed to fetch "{daemon_id}" info')
            log.debug('', exc_info=True)
            all_info[daemon_id] = None
            bad_info.append(daemon_id)

    threads = [threading.Thread(target=daemon_info_thread, args=(daemon_id, log, log_debug))
               for daemon_id in ['ota', 'foc', 'filt', 'dome', 'mnt', 'conditions']]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Mount history
    if all_info['mnt'] is not None:
        try:
            info = all_info['mnt']
            exptime = cam_info['current_exposure']['exptime']

            # Position error history
            poserr_info = {}
            poserr_info['hist_time'] = -999
            poserr_info['ra_max'] = 'NA'
            poserr_info['ra_mean'] = 'NA'
            poserr_info['ra_std'] = 'NA'
            poserr_info['dec_max'] = 'NA'
            poserr_info['dec_mean'] = 'NA'
            poserr_info['dec_std'] = 'NA'
            if info['position_error_history'] is not None:
                # Get lookback time
                max_hist = info_time.unix - info['position_error_history'][0][0]
                hist_time = params.MIN_HEADER_HIST_TIME
                if exptime > hist_time:
                    hist_time = exptime
                if hist_time > max_hist:
                    hist_time = max_hist
                poserr_info['hist_time'] = hist_time
                # Get RA history values
                ra_hist = [h[1]['ra'] for h in info['position_error_history']
                           if info_time.unix - h[0] <= hist_time]
                if len(ra_hist) > 0:
                    poserr_info['ra_hist'] = ra_hist
                    poserr_info['ra_max'] = np.max(ra_hist)
                    poserr_info['ra_mean'] = np.mean(ra_hist)
                    poserr_info['ra_std'] = np.std(ra_hist)
                # Get Dec history values
                dec_hist = [h[1]['dec'] for h in info['position_error_history']
                            if info_time.unix - h[0] <= hist_time]
                if len(dec_hist) > 0:
                    poserr_info['dec_hist'] = dec_hist
                    poserr_info['dec_max'] = np.max(dec_hist)
                    poserr_info['dec_mean'] = np.mean(dec_hist)
                    poserr_info['dec_std'] = np.std(dec_hist)
            all_info['mnt']['position_error_info'] = poserr_info

            # Tracking error history
            trackerr_info = {}
            trackerr_info['hist_time'] = -999
            trackerr_info['ra_max'] = 'NA'
            trackerr_info['ra_mean'] = 'NA'
            trackerr_info['ra_std'] = 'NA'
            trackerr_info['dec_max'] = 'NA'
            trackerr_info['dec_mean'] = 'NA'
            trackerr_info['dec_std'] = 'NA'
            if info['tracking_error_history'] is not None:
                # Get lookback time
                max_hist = info_time.unix - info['tracking_error_history'][0][0]
                hist_time = params.MIN_HEADER_HIST_TIME
                if exptime > hist_time:
                    hist_time = exptime
                if hist_time > max_hist:
                    hist_time = max_hist
                trackerr_info['hist_time'] = hist_time
                # Get RA history values
                ra_hist = [h[1]['ra'] for h in info['tracking_error_history']
                           if info_time.unix - h[0] <= hist_time]
                if len(ra_hist) > 0:
                    trackerr_info['ra_hist'] = ra_hist
                    trackerr_info['ra_max'] = np.max(ra_hist)
                    trackerr_info['ra_mean'] = np.mean(ra_hist)
                    trackerr_info['ra_std'] = np.std(ra_hist)
                # Get Dec history values
                dec_hist = [h[1]['dec'] for h in info['tracking_error_history']
                            if info_time.unix - h[0] <= hist_time]
                if len(dec_hist) > 0:
                    trackerr_info['dec_hist'] = dec_hist
                    trackerr_info['dec_max'] = np.max(dec_hist)
                    trackerr_info['dec_mean'] = np.mean(dec_hist)
                    trackerr_info['dec_std'] = np.std(dec_hist)
            all_info['mnt']['tracking_error_info'] = trackerr_info

            # Motor current histroy
            current_info = {}
            current_info['hist_time'] = -999
            current_info['ra_max'] = 'NA'
            current_info['ra_mean'] = 'NA'
            current_info['ra_std'] = 'NA'
            current_info['dec_max'] = 'NA'
            current_info['dec_mean'] = 'NA'
            current_info['dec_std'] = 'NA'
            if info['motor_current_history'] is not None:
                # Get lookback time
                max_hist = info_time.unix - info['motor_current_history'][0][0]
                hist_time = params.MIN_HEADER_HIST_TIME
                if exptime > hist_time:
                    hist_time = exptime
                if hist_time > max_hist:
                    hist_time = max_hist
                current_info['hist_time'] = hist_time
                # Get RA history values
                ra_hist = [h[1]['ra'] for h in info['motor_current_history']
                           if info_time.unix - h[0] <= hist_time]
                if len(ra_hist) > 0:
                    current_info['ra_hist'] = ra_hist
                    current_info['ra_max'] = np.max(ra_hist)
                    current_info['ra_mean'] = np.mean(ra_hist)
                    current_info['ra_std'] = np.std(ra_hist)
                # Get Dec history values
                dec_hist = [h[1]['dec'] for h in info['motor_current_history']
                            if info_time.unix - h[0] <= hist_time]
                if len(dec_hist) > 0:
                    current_info['dec_hist'] = dec_hist
                    current_info['dec_max'] = np.max(dec_hist)
                    current_info['dec_mean'] = np.mean(dec_hist)
                    current_info['dec_std'] = np.std(dec_hist)
            all_info['mnt']['motor_current_info'] = current_info

        except Exception:
            if log is None:
                raise
            log.error('Failed to calculate mount history info')
            log.debug('', exc_info=True)
            all_info['mnt']['position_error_info'] = None
            all_info['mnt']['tracking_error_info'] = None
            all_info['mnt']['motor_current_info'] = None
            bad_info.append('mnt_history')

    # Conditions sources
    if all_info['conditions'] is not None:
        try:
            # Select external source
            ext_source = params.WEATHER_SOURCES[0]
            ext_weather = all_info['conditions']['weather'][ext_source].copy()
            all_info['conditions']['weather_ext'] = ext_weather

            # Select internal source
            int_weather = all_info['conditions']['internal'].copy()
            all_info['conditions']['weather_int'] = int_weather

        except Exception:
            if log is None:
                raise
            log.error('Failed to find conditions sources')
            log.debug('', exc_info=True)
            all_info['conditions']['weather_ext'] = None
            all_info['conditions']['weather_int'] = None
            bad_info.append('conditions_sources')

    # Conditions history
    if all_info['conditions'] is not None and all_info['conditions']['weather_ext'] is not None:
        try:
            info = all_info['conditions']['weather_ext']
            exptime = cam_info['current_exposure']['exptime']

            # Wind gust history
            hist_info = {}
            hist_info['hist_time'] = -999
            hist_info['max'] = 'NA'
            hist_info['mean'] = 'NA'
            hist_info['std'] = 'NA'
            if info['windgust_history'] != -999:
                # Get lookback time
                max_hist = info_time.unix - info['windgust_history'][0][0]
                hist_time = params.MIN_HEADER_HIST_TIME
                if exptime > hist_time:
                    hist_time = exptime
                if hist_time > max_hist:
                    hist_time = max_hist
                hist_info['hist_time'] = hist_time
                # Get gust history values
                gust_hist = [h[1] for h in info['windgust_history']
                             if info_time.unix - h[0] <= hist_time]
                if len(gust_hist) > 0:
                    hist_info['hist'] = gust_hist
                    hist_info['max'] = np.max(gust_hist)
                    hist_info['mean'] = np.mean(gust_hist)
                    hist_info['std'] = np.std(gust_hist)
            all_info['conditions']['weather_ext']['windgust_history_info'] = hist_info

        except Exception:
            if log is None:
                raise
            log.error('Failed to calculate conditions history info')
            log.debug('', exc_info=True)
            all_info['conditions']['weather_ext']['windgust_history_info'] = None
            bad_info.append('conditions_history')

    # Database
    if cam_info['current_exposure']['set_id'] is not None:
        try:
            if log and log_debug:
                log.debug('Fetching database info')
            db_info = {}
            db_info['from_database'] = True
            db_info['expset_id'] = cam_info['current_exposure']['set_id']
            db_info['pointing_id'] = cam_info['current_exposure']['pointing_id']

            # Get Pointing info from the scheduler
            pointing_info = get_pointing_info(cam_info['current_exposure']['pointing_id'])
            db_info.update(pointing_info)

            # Check IDs match
            if db_info['pointing_id'] != pointing_info['id']:
                raise ValueError('Pointing ID {} does not match {}'.format(
                    db_info['pointing_id'], pointing_info['id']))
            else:
                del db_info['id']

            all_info['db'] = db_info
            if log and log_debug:
                log.debug('Fetched database info')
        except Exception:
            if log is None:
                raise
            log.error('Failed to fetch database info')
            log.debug('', exc_info=True)
            all_info['db'] = None
            bad_info.append('database')
    else:
        db_info = {}
        db_info['from_database'] = False
        all_info['db'] = db_info

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
    ut_mask = misc.ut_list_to_mask(all_info['cam']['current_exposure']['ut_list'])
    params_info['ut_mask'] = ut_mask
    params_info['ut_string'] = misc.ut_mask_to_string(ut_mask)
    params_info['uts_with_covers'] = params.UTS_WITH_COVERS
    params_info['uts_with_focusers'] = params.UTS_WITH_FOCUSERS
    params_info['uts_with_filterwheels'] = params.UTS_WITH_FILTERWHEELS

    status = Status()
    params_info['system_mode'] = status.mode
    params_info['observer'] = status.observer

    all_info['params'] = params_info

    return all_info, bad_info


def update_header(header, ut, all_info, log=None):
    """Add observation, exposure and hardware info to the FITS header."""
    # These cards are set automatically by AstroPy, we just give them better comments
    # header.comments['SIMPLE  '] = 'Standard FITS'
    # header.comments['BITPIX  '] = 'Bits per pixel'
    # header.comments['NAXIS   '] = 'Number of dimensions'
    # header.comments['NAXIS1  '] = 'Number of columns'
    # header.comments['NAXIS2  '] = 'Number of rows'
    # header.comments['EXTEND  '] = 'Can contain extensions'
    # header.comments['BSCALE  '] = 'Pixel scale factor'
    # header.comments['BZERO   '] = 'Real = Pixel * BSCALE + BZERO'

    # Observation info
    params_info = all_info['params']
    cam_info = all_info['cam']
    exposure_info = cam_info['current_exposure']
    cam_info = cam_info[ut]
    glance = exposure_info['glance']
    if not glance:
        run_number = exposure_info['run_number']
        run_number_str = 'r{:07d}'.format(run_number)
    else:
        run_number = 'NA'
        run_number_str = 'NA'
    header['RUN     '] = (run_number, 'GOTO run number')
    header['RUN-ID  '] = (run_number_str, 'Padded run ID string')

    write_time = Time.now()
    header['DATE    '] = (write_time.isot, 'Date HDU created')

    header['ORIGIN  '] = (params_info['org_name'], 'Origin organisation')

    header['SITE    '] = (params_info['site_name'], 'Site location')
    header['SITE-LAT'] = (params_info['site_lat'], 'Site latitude, degrees +N')
    header['SITE-LON'] = (params_info['site_lon'], 'Site longitude, degrees +E')
    header['SITE-ALT'] = (params_info['site_alt'], 'Site elevation, m above sea level')

    header['TELESCOP'] = (params_info['tel_name'], 'Origin telescope name')
    header['TEL     '] = (params_info['tel_number'], 'Origin telescope ID number')

    header['INSTRUME'] = ('UT' + str(ut), 'Origin unit telescope')
    header['UT      '] = (ut, 'Integer UT number')

    if 'HW_VERSION' in params_info['ut_dict'][ut]:
        ut_hw_version = params_info['ut_dict'][ut]['HW_VERSION']
    else:
        ut_hw_version = 'NA'
    header['UT-VERS '] = (ut_hw_version, 'UT hardware version number')

    header['UTMASK  '] = (params_info['ut_mask'], 'Run UT mask integer')
    header['UTMASKBN'] = (params_info['ut_string'], 'Run UT mask binary string')

    interface_id = params_info['ut_dict'][ut]['INTERFACE']
    header['INTERFAC'] = (interface_id, 'System interface code')

    header['SWVN    '] = (params_info['version'], 'Software version number')

    header['SYS-MODE'] = (params_info['system_mode'], 'Current telescope system mode')
    header['OBSERVER'] = (params_info['observer'], 'Who started the exposure')

    header['OBJECT  '] = (exposure_info['target'], 'Observed object name')

    set_number = exposure_info['set_num']
    if set_number is None:
        set_number = 'NA'
    header['SET     '] = (set_number, 'GOTO set number')
    header['SET-POS '] = (exposure_info['set_pos'], 'Position of this exposure in this set')
    header['SET-TOT '] = (exposure_info['set_tot'], 'Total number of exposures in this set')

    # Exposure data
    exptime = exposure_info['exptime']
    header['EXPTIME '] = (exptime, 'Exposure time, seconds')

    start_time = Time(cam_info['exposure_start_time'], format='unix')
    mid_time = start_time + (exposure_info['exptime'] * u.second) / 2.
    header['DATE-OBS'] = (start_time.isot, 'Exposure start time, UTC')
    header['DATE-MID'] = (mid_time.isot, 'Exposure midpoint, UTC')

    mid_jd = mid_time.jd
    header['JD      '] = (mid_jd, 'Exposure midpoint, Julian Date')

    lst = get_lst(mid_time)
    mid_lst = '{:02.0f}:{:02.0f}:{:06.3f}'.format(*lst.hms)
    header['LST     '] = (mid_lst, 'Exposure midpoint, Local Sidereal Time')

    # Frame info
    header['FRMTYPE '] = (exposure_info['frametype'], 'Frame type (shutter open/closed)')
    header['IMGTYPE '] = (exposure_info['imgtype'], 'Image type')
    header['GLANCE  '] = (exposure_info['glance'], 'Is this a glance frame?')

    # (Depreciated section cards)
    header['FULLSEC '] = ('[1:8304,1:6220]', 'Size of the full frame')
    header['TRIMSEC '] = ('[65:8240,46:6177]', 'Central data region (both channels)')
    header['TRIMSEC1'] = ('[65:4152,46:6177]', 'Data section for left channel')
    header['TRIMSEC2'] = ('[4153:8240,46:6177]', 'Data section for right channel')
    header['BIASSEC1'] = ('[3:10,3:6218]', 'Recommended bias section for left channel')
    header['BIASSEC2'] = ('[8295:8302,3:6218]', 'Recommended bias section for right channel')
    header['DARKSEC1'] = ('[26:41,500:5721]', 'Recommended dark section for left channel')
    header['DARKSEC2'] = ('[8264:8279,500:5721]', 'Recommended dark section for right channel')

    # Database info
    try:
        if all_info['db'] is None:
            raise ValueError('No database info provided')
        info = all_info['db']
        from_database = info['from_database']

    except Exception as err:
        if log is None:
            raise
        if 'info provided' in str(err):
            log.warning(str(err))
        else:
            log.error('Failed to write database info to header')
            log.debug('', exc_info=True)
        from_database = False

    header['FROMDB  '] = (from_database, 'Exposure linked to database pointing?')

    # Database table info
    try:
        info = all_info['db']

        # ExposureSet info
        expset_id = info['expset_id']  # from Exposure

        # Pointing info
        pointing_id = info['pointing_id']
        if info['rank'] is not None:
            rank = info['rank']
        else:
            rank = 'inf'
        if info['start_time'] is not None:
            starttime = info['start_time'].strftime('%Y-%m-%dT%H:%M:%S')
        else:
            starttime = 'NA'
        if info['stop_time'] is not None:
            stoptime = info['stop_time'].strftime('%Y-%m-%dT%H:%M:%S')
        else:
            stoptime = 'NA'

        # Target info
        target_id = info['target_id']
        if info['start_rank'] is not None:
            initialrank = info['start_rank']
        else:
            initialrank = 'inf'
        weight = info['weight']
        num_observed = info['num_completed']
        is_template = info['is_template']

        # Strategy info
        strategy_id = info['strategy_id']
        infinite = info['infinite']
        if info['min_time'] is not None:
            min_time = info['min_time']
        else:
            min_time = 'NA'
        too = info['too']
        requires_template = info['requires_template']
        min_alt = info['min_alt']
        max_sunalt = info['max_sunalt']
        max_moon = info['max_moon']
        min_moonsep = info['min_moonsep']

        # TimeBlock info
        time_block_id = info['time_block_id']
        block_num = info['block_num']
        if info['wait_time'] is not None:
            wait_time = info['wait_time']
        else:
            wait_time = 'NA'
        if info['valid_time'] is not None:
            valid_time = info['valid_time']
        else:
            valid_time = 'NA'

        # Get User info
        user_id = info['user_id']
        user_name = info['user_name']
        user_fullname = info['user_fullname']

        # Get Grid info
        if info['grid_id'] is not None:
            grid_id = info['grid_id']
            grid_name = info['grid_name']
            tile_id = info['tile_id']
            tile_name = info['tile_name']
        else:
            grid_id = 'NA'
            grid_name = 'NA'
            tile_id = 'NA'
            tile_name = 'NA'

        # Get Survey info
        if info['survey_id'] is not None:
            survey_id = info['survey_id']
            survey_name = info['survey_name']
            skymap = info['skymap']
        else:
            survey_id = 'NA'
            survey_name = 'NA'
            skymap = 'NA'

        # Get Event info
        if info['event_id'] is not None:
            event_id = info['event_id']
            event_name = info['event_name']
            event_source = info['event_source']
            event_type = info['event_type']
            if info['event_time'] is not None:
                event_time = info['event_type'].strftime('%Y-%m-%dT%H:%M:%S')
            else:
                event_time = 'NA'
        else:
            event_id = 'NA'
            event_name = 'NA'
            event_source = 'NA'
            event_type = 'NA'
            event_time = 'NA'

    except Exception:
        if from_database:
            # It's only an error if the values should be there
            if log is None:
                raise
            log.error('Failed to write database info to header')
            log.debug('', exc_info=True)
        expset_id = 'NA'

        pointing_id = 'NA'
        rank = 'NA'
        starttime = 'NA'
        stoptime = 'NA'

        target_id = 'NA'
        initialrank = 'NA'
        weight = 'NA'
        num_observed = 'NA'
        is_template = 'NA'

        strategy_id = 'NA'
        infinite = 'NA'
        min_time = 'NA'
        too = 'NA'
        requires_template = 'NA'
        min_alt = 'NA'
        max_sunalt = 'NA'
        max_moon = 'NA'
        min_moonsep = 'NA'

        time_block_id = 'NA'
        block_num = 'NA'
        wait_time = 'NA'
        valid_time = 'NA'

        user_id = 'NA'
        user_name = 'NA'
        user_fullname = 'NA'

        grid_id = 'NA'
        grid_name = 'NA'
        tile_id = 'NA'
        tile_name = 'NA'

        survey_id = 'NA'
        survey_name = 'NA'
        skymap = 'NA'

        event_id = 'NA'
        event_name = 'NA'
        event_source = 'NA'
        event_type = 'NA'
        event_time = 'NA'

    # MAX COMMENT LENGTH: '~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~'
    header['DB-EXPS '] = (expset_id, 'Database ExposureSet ID')

    header['DB-PNT  '] = (pointing_id, 'Database Pointing ID')
    header['RANK    '] = (rank, 'Rank of this pointing when observed')
    header['LIM-STRT'] = (starttime, 'Valid start time limit for this pointing')
    header['LIM-STOP'] = (stoptime, 'Valid stop time limit for this pointing')

    header['DB-TARG '] = (target_id, 'Database Target ID')
    header['BASERANK'] = (initialrank, 'Initial rank of this Target')
    header['WEIGHT  '] = (weight, 'Target weighting')
    header['OBSNUM  '] = (num_observed, 'Count of times this Target has been observed')
    header['IS-TMPL '] = (is_template, 'Is this Pointing a template observation?')

    header['DB-STRAT'] = (strategy_id, 'Database Strategy ID')
    header['INFINITE'] = (infinite, 'Is this an infinitely repeating pointing?')
    header['LIM-TIME'] = (min_time, 'Minimum observing time for this pointing')
    header['TOO     '] = (too, 'Is this Pointing a Target of Opportunity?')
    header['REQ-TMPL'] = (requires_template, 'Did this Pointing require a template?')
    header['LIM-ALT '] = (min_alt, 'Minimum altitude limit for this pointing')
    header['LIM-SALT'] = (max_sunalt, 'Maximum Sun altitude limit for this pointing')
    header['LIM-MPHS'] = (max_moon, 'Maximum Moon phase limit for this pointing')
    header['LIM-MDIS'] = (min_moonsep, 'Minimum Moon distance limit for this pointing')

    header['DB-TIMBK'] = (time_block_id, 'Database TimeBlock ID')
    header['TIMBKNUM'] = (block_num, 'Number of this time block')
    header['TIMEVALD'] = (wait_time, 'How long this Pointing is valid in the queue')
    header['TIMEWAIT'] = (valid_time, 'How long between Pointings for this Target')

    header['DB-USER '] = (user_id, 'Database User ID who submitted this pointing')
    header['USERNAME'] = (user_name, 'Username that submitted this pointing')
    header['USERFULL'] = (user_fullname, 'User who submitted this pointing')

    header['DB-GRID '] = (grid_id, 'Database Grid ID')
    header['GRID    '] = (grid_name, 'Sky grid name')
    header['DB-GTILE'] = (tile_id, 'Database GridTile ID')
    header['TILENAME'] = (tile_name, 'Name of this grid tile')

    header['DB-SURVY'] = (survey_id, 'Database Survey ID')
    header['SURVEY  '] = (survey_name, 'Name of this survey')
    header['SKYMAP  '] = (skymap, 'Skymap URL for this event')

    header['DB-EVENT'] = (event_id, 'Database Event ID')
    header['EVENT   '] = (event_name, 'Event name for this pointing')
    header['SOURCE  '] = (event_source, 'Source of this event')
    header['EVNTTYPE'] = (event_type, 'Type of event')
    header['EVNTTIME'] = (event_time, 'Recorded time of the event')

    # Camera info
    cam_serial = cam_info['serial_number']
    cam_class = cam_info['hw_class']
    header['CAMERA  '] = (cam_serial, 'Camera serial number')
    header['CAMCLS  '] = (cam_class, 'Camera hardware class')

    header['XBINNING'] = (exposure_info['binning'], 'CCD x binning factor')
    header['YBINNING'] = (exposure_info['binning'], 'CCD y binning factor')

    x_pixel_size = cam_info['x_pixel_size'] * exposure_info['binning']
    y_pixel_size = cam_info['y_pixel_size'] * exposure_info['binning']
    header['XPIXSZ  '] = (x_pixel_size, 'Binned x pixel size, m')
    header['YPIXSZ  '] = (y_pixel_size, 'Binned y pixel size, m')

    full_area = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(*cam_info['full_area'])
    active_area = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(*cam_info['active_area'])
    window_area = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(*cam_info['window_area'])
    header['FULLAREA'] = (full_area, 'Full frame area in unbinned pixels (x,y,dx,dy)')
    header['ACTVAREA'] = (active_area, 'Active area in unbinned pixels (x,y,dx,dy)')
    header['WINDOW  '] = (window_area, 'Windowed region in unbinned pixels (x,y,dx,dy)')

    header['CHANNELS'] = (2, 'Number of CCD channels')  # TODO: this should come from the camera

    header['CCDTEMP '] = (cam_info['ccd_temp'], 'CCD temperature, C')
    header['CCDTEMPS'] = (cam_info['target_temp'], 'Requested CCD temperature, C')
    header['BASETEMP'] = (cam_info['base_temp'], 'Peltier base temperature, C')

    # OTA info
    try:
        if all_info['ota'] is None:
            raise ValueError('No OTA info provided')

        info = all_info['ota'][ut]
        ota_serial = info['serial_number']
        ota_class = info['hw_class']
        if ut not in params_info['uts_with_covers']:
            cover_position = 'NA'
            cover_open = 'NA'
            cover_move_time = 'NA'
        else:
            cover_position = info['position']
            cover_open = info['position'] == 'full_open'
            cover_move_time = info['last_move_time']
            if cover_move_time is not None:
                cover_move_time = Time(cover_move_time, format='unix')
                cover_move_time = cover_move_time.isot
            else:
                cover_move_time = 'NA'
    except Exception as err:
        if log is None:
            raise
        if 'info provided' in str(err):
            log.warning(str(err))
        else:
            log.error('Failed to write OTA info to header')
            log.debug('', exc_info=True)
        ota_serial = 'NA'
        ota_class = 'NA'
        cover_position = 'NA'
        cover_open = 'NA'
        cover_move_time = 'NA'

    header['OTA     '] = (ota_serial, 'OTA serial number')
    header['OTACLS  '] = (ota_class, 'OTA hardware class')
    header['COVSTAT '] = (cover_position, 'Mirror cover position')
    header['COVOPEN '] = (cover_open, 'Mirror cover is open')
    header['COVMVT  '] = (cover_move_time, 'Mirror cover latest move time')

    # Focuser info
    try:
        if all_info['foc'] is None:
            raise ValueError('No focuser info provided')

        if ut not in params_info['uts_with_focusers']:
            foc_serial = 'None'
            foc_class = 'NA'
            foc_pos = 'NA'
            foc_move_time = 'NA'
            foc_temp_int = 'NA'
            foc_temp_ext = 'NA'
        else:
            info = all_info['foc'][ut]

            foc_serial = info['serial_number']
            foc_class = info['hw_class']
            foc_pos = info['current_pos']
            foc_move_time = info['last_move_time']
            if foc_move_time is not None:
                foc_move_time = Time(foc_move_time, format='unix')
                foc_move_time = foc_move_time.isot
            else:
                foc_move_time = 'NA'
            foc_temp_int = info['int_temp'] if info['int_temp'] is not None else 'NA'
            foc_temp_ext = info['ext_temp'] if info['ext_temp'] is not None else 'NA'
    except Exception as err:
        if log is None:
            raise
        if 'info provided' in str(err):
            log.warning(str(err))
        else:
            log.error('Failed to write focuser info to header')
            log.debug('', exc_info=True)
        foc_serial = 'NA'
        foc_class = 'NA'
        foc_pos = 'NA'
        foc_move_time = 'NA'
        foc_temp_int = 'NA'
        foc_temp_ext = 'NA'

    header['FOCUSER '] = (foc_serial, 'Focuser serial number')
    header['FOCCLS  '] = (foc_class, 'Focuser hardware class')
    header['FOCPOS  '] = (foc_pos, 'Focuser motor position')
    header['FOCMVT  '] = (foc_move_time, 'Focuser latest move time')
    header['FOCTEMPI'] = (foc_temp_int, 'Focuser internal temperature, C')
    header['FOCTEMPX'] = (foc_temp_ext, 'Focuser external temperature, C')

    # Filter wheel info
    try:
        if all_info['filt'] is None:
            raise ValueError('No filter wheel info provided')

        if ut not in params_info['uts_with_filterwheels']:
            filt_serial = 'None'
            filt_class = 'NA'
            filt_filter = params_info['ut_dict'][ut]['FILTERS'][0]
            filt_filters = ','.join(params_info['ut_dict'][ut]['FILTERS'])  # Should only be one?
            filt_num = 'NA'
            filt_pos = 'NA'
            filt_move_time = 'NA'
        else:
            info = all_info['filt'][ut]

            filt_serial = info['serial_number']
            filt_class = info['hw_class']
            if not info['homed']:
                filt_filter = 'UNHOMED'
            else:
                filt_filter = info['current_filter']
            filt_filters = ','.join(params_info['ut_dict'][ut]['FILTERS'])
            filt_num = info['current_filter_num']
            filt_pos = info['current_pos']
            filt_move_time = info['last_move_time']
            if filt_move_time is not None:
                filt_move_time = Time(filt_move_time, format='unix')
                filt_move_time = filt_move_time.isot
            else:
                filt_move_time = 'NA'
    except Exception as err:
        if log is None:
            raise
        if 'info provided' in str(err):
            log.warning(str(err))
        else:
            log.error('Failed to write filter wheel info to header')
            log.debug('', exc_info=True)
        filt_serial = 'NA'
        filt_class = 'NA'
        filt_filter = 'NA'
        filt_filters = 'NA'
        filt_num = 'NA'
        filt_pos = 'NA'
        filt_move_time = 'NA'

    header['FLTWHEEL'] = (filt_serial, 'Filter wheel serial number')
    header['FILTCLS '] = (filt_class, 'Filter wheel hardware class')
    header['FILTER  '] = (filt_filter, 'Filter used for exposure')
    header['FILTERS '] = (filt_filters, 'Filters in filter wheel')
    header['FILTNUM '] = (filt_num, 'Filter wheel position number')
    header['FILTPOS '] = (filt_pos, 'Filter wheel motor position')
    header['FILTMVT '] = (filt_move_time, 'Filter wheel latest move time')

    # Dome info
    try:
        if all_info['dome'] is None:
            raise ValueError('No dome info provided')

        info = all_info['dome']

        north_status = info['north']
        south_status = info['south']
        if north_status == 'ERROR' or south_status == 'ERROR':
            dome_status = 'ERROR'
        elif north_status == 'closed' and south_status == 'closed':
            dome_status = 'closed'
        elif north_status == 'full_open' and south_status == 'full_open':
            dome_status = 'full_open'
        elif north_status == 'part_open' or south_status == 'part_open':
            dome_status = 'part_open'
        else:
            dome_status = 'ERROR'

        dome_open = info['dome'] == 'open'
        dome_shielding = info['shielding']
        dome_move_time = info['last_move_time']
        if dome_move_time is not None:
            dome_move_time = Time(dome_move_time, format='unix')
            dome_move_time = dome_move_time.isot
        else:
            dome_move_time = 'NA'

    except Exception as err:
        if log is None:
            raise
        if 'info provided' in str(err):
            log.warning(str(err))
        else:
            log.error('Failed to write dome info to header')
            log.debug('', exc_info=True)
        dome_status = 'NA'
        dome_open = 'NA'
        dome_shielding = 'NA'
        dome_move_time = 'NA'

    header['DOMESTAT'] = (dome_status, 'Dome status')
    header['DOMEOPEN'] = (dome_open, 'Dome is open')
    header['DOMESHLD'] = (dome_shielding, 'Dome wind shield is active')
    header['DOMEMVT '] = (dome_move_time, 'Dome latest move time')

    # Mount info
    try:
        if all_info['mnt'] is None:
            raise ValueError('No mount info provided')

        info = all_info['mnt']

        targ_ra = info['target_ra']
        if targ_ra is not None:
            targ_ra_str = Angle(targ_ra * u.hour).to_string(sep=':', precision=3, alwayssign=True)
        else:
            targ_ra_str = 'NA'

        targ_dec = info['target_dec']
        if targ_dec is not None:
            targ_dec_str = Angle(targ_dec * u.deg).to_string(sep=':', precision=3, alwayssign=True)
        else:
            targ_dec_str = 'NA'

        targ_dist = info['target_dist']
        if targ_dist is None:
            targ_dist = 'NA'

        mnt_ra = info['pointing_ra']
        mnt_ra_str = Angle(mnt_ra * u.hour).to_string(sep=':', precision=3, alwayssign=True)

        mnt_dec = info['pointing_dec']
        mnt_dec_str = Angle(mnt_dec * u.deg).to_string(sep=':', precision=3, alwayssign=True)

        mnt_alt = info['pointing_alt']
        mnt_az = info['pointing_az']
        ha = info['pointing_ha']

        mnt_move_time = info['last_move_time']
        if mnt_move_time is not None:
            mnt_move_time = Time(mnt_move_time, format='unix')
            mnt_move_time = mnt_move_time.isot
        else:
            mnt_move_time = 'NA'

        mount_tracking = info['status'] == 'Tracking'
        sidereal = not info['nonsidereal']
        trackrate_ra = info['trackrate_ra']
        trackrate_dec = info['trackrate_dec']

        poserr_ra = info['position_error']['ra']
        poserr_dec = info['position_error']['dec']
        trkerr_ra = info['tracking_error']['ra']
        trkerr_dec = info['tracking_error']['dec']
        current_ra = info['motor_current']['ra']
        current_dec = info['motor_current']['dec']

        if info['position_error_info'] is None:
            poserr_hist_time = -999
            poserr_ra_max = 'NA'
            poserr_ra_mean = 'NA'
            poserr_ra_std = 'NA'
            poserr_dec_max = 'NA'
            poserr_dec_mean = 'NA'
            poserr_dec_std = 'NA'
        else:
            poserr_hist_time = info['position_error_info']['hist_time']
            poserr_ra_max = info['position_error_info']['ra_max']
            poserr_ra_mean = info['position_error_info']['ra_mean']
            poserr_ra_std = info['position_error_info']['ra_std']
            poserr_dec_max = info['position_error_info']['dec_max']
            poserr_dec_mean = info['position_error_info']['dec_mean']
            poserr_dec_std = info['position_error_info']['dec_std']

        if info['tracking_error_info'] is None:
            trkerr_hist_time = -999
            trkerr_ra_max = 'NA'
            trkerr_ra_mean = 'NA'
            trkerr_ra_std = 'NA'
            trkerr_dec_max = 'NA'
            trkerr_dec_mean = 'NA'
            trkerr_dec_std = 'NA'
        else:
            trkerr_hist_time = info['tracking_error_info']['hist_time']
            trkerr_ra_max = info['tracking_error_info']['ra_max']
            trkerr_ra_mean = info['tracking_error_info']['ra_mean']
            trkerr_ra_std = info['tracking_error_info']['ra_std']
            trkerr_dec_max = info['tracking_error_info']['dec_max']
            trkerr_dec_mean = info['tracking_error_info']['dec_mean']
            trkerr_dec_std = info['tracking_error_info']['dec_std']

        if info['motor_current_info'] is None:
            current_hist_time = -999
            current_ra_max = 'NA'
            current_ra_mean = 'NA'
            current_ra_std = 'NA'
            current_dec_max = 'NA'
            current_dec_mean = 'NA'
            current_dec_std = 'NA'
        else:
            current_hist_time = info['motor_current_info']['hist_time']
            current_ra_max = info['motor_current_info']['ra_max']
            current_ra_mean = info['motor_current_info']['ra_mean']
            current_ra_std = info['motor_current_info']['ra_std']
            current_dec_max = info['motor_current_info']['dec_max']
            current_dec_mean = info['motor_current_info']['dec_mean']
            current_dec_std = info['motor_current_info']['dec_std']

        zen_dist = 90 - mnt_alt
        airmass = 1 / (math.cos(math.pi / 2 - (mnt_alt * math.pi / 180)))
        equinox = 2000

        sun_alt = info['sun_alt']

        moon_alt = info['moon_alt']
        moon_ill = info['moon_ill'] * 100
        moon_phase = info['moon_phase']
        moon_dist = info['moon_dist']

    except Exception as err:
        if log is None:
            raise
        if 'info provided' in str(err):
            log.warning(str(err))
        else:
            log.error('Failed to write mount info to header')
            log.debug('', exc_info=True)
        targ_ra_str = 'NA'
        targ_dec_str = 'NA'
        targ_dist = 'NA'
        mnt_ra_str = 'NA'
        mnt_dec_str = 'NA'
        mnt_alt = 'NA'
        mnt_az = 'NA'
        ha = 'NA'
        mnt_move_time = 'NA'
        mount_tracking = 'NA'
        sidereal = 'NA'
        trackrate_ra = 'NA'
        trackrate_dec = 'NA'
        poserr_ra = 'NA'
        poserr_dec = 'NA'
        trkerr_ra = 'NA'
        trkerr_dec = 'NA'
        current_ra = 'NA'
        current_dec = 'NA'
        poserr_hist_time = -999
        poserr_ra_max = 'NA'
        poserr_ra_mean = 'NA'
        poserr_ra_std = 'NA'
        poserr_dec_max = 'NA'
        poserr_dec_mean = 'NA'
        poserr_dec_std = 'NA'
        trkerr_hist_time = -999
        trkerr_ra_max = 'NA'
        trkerr_ra_mean = 'NA'
        trkerr_ra_std = 'NA'
        trkerr_dec_max = 'NA'
        trkerr_dec_mean = 'NA'
        trkerr_dec_std = 'NA'
        current_hist_time = -999
        current_ra_max = 'NA'
        current_ra_mean = 'NA'
        current_ra_std = 'NA'
        current_dec_max = 'NA'
        current_dec_mean = 'NA'
        current_dec_std = 'NA'
        zen_dist = 'NA'
        airmass = 'NA'
        equinox = 'NA'
        sun_alt = 'NA'
        moon_alt = 'NA'
        moon_ill = 'NA'
        moon_phase = 'NA'
        moon_dist = 'NA'

    header['RA-TARG '] = (targ_ra_str, 'Requested pointing RA')
    header['DEC-TARG'] = (targ_dec_str, 'Requested pointing Dec')

    header['RA-TEL  '] = (mnt_ra_str, 'Reported mount pointing RA')
    header['DEC-TEL '] = (mnt_dec_str, 'Reported mount pointing Dec')

    header['EQUINOX '] = (equinox, 'RA/Dec equinox, years')

    header['TARGDIST'] = (targ_dist, 'Distance from target, degrees')

    header['ALT     '] = (mnt_alt, 'Mount altitude')
    header['AZ      '] = (mnt_az, 'Mount azimuth')
    header['HA      '] = (ha, 'Hour angle')

    header['SLEWTIME'] = (mnt_move_time, 'Mount latest move time')
    header['TRACKING'] = (mount_tracking, 'Mount is tracking')
    header['SIDEREAL'] = (sidereal, 'Mount is tracking at sidereal rate')
    header['RA-TRKR '] = (trackrate_ra, 'RA tracking rate (0=sidereal)')
    header['DEC-TRKR'] = (trackrate_dec, 'Dec tracking rate (0=sidereal)')

    header['RA-PERR '] = (poserr_ra, 'RA position error')
    header['RA-PMAX '] = (poserr_ra_max, 'RA max position error (last {:.0f}s)'.format(
                          poserr_hist_time))
    header['RA-PMEA '] = (poserr_ra_mean, 'RA mean position error (last {:.0f}s)'.format(
                          poserr_hist_time))
    header['RA-PSTD '] = (poserr_ra_std, 'RA std position error (last {:.0f}s)'.format(
                          poserr_hist_time))
    header['DEC-PERR'] = (poserr_dec, 'Dec position error')
    header['DEC-PMAX'] = (poserr_dec_max, 'Dec max position error (last {:.0f}s)'.format(
                          poserr_hist_time))
    header['DEC-PMEA'] = (poserr_dec_mean, 'Dec mean position error (last {:.0f}s)'.format(
                          poserr_hist_time))
    header['DEC-PSTD'] = (poserr_dec_std, 'Dec std position error (last {:.0f}s)'.format(
                          poserr_hist_time))

    header['RA-TERR '] = (trkerr_ra, 'RA tracking error')
    header['RA-TMAX '] = (trkerr_ra_max, 'RA max tracking error (last {:.0f}s)'.format(
                          trkerr_hist_time))
    header['RA-TMEA '] = (trkerr_ra_mean, 'RA mean tracking error (last {:.0f}s)'.format(
                          trkerr_hist_time))
    header['RA-TSTD '] = (trkerr_ra_std, 'RA std tracking error (last {:.0f}s)'.format(
                          trkerr_hist_time))
    header['DEC-TERR'] = (trkerr_dec, 'Dec tracking error')
    header['DEC-TMAX'] = (trkerr_dec_max, 'Dec max tracking error (last {:.0f}s)'.format(
                          trkerr_hist_time))
    header['DEC-TMEA'] = (trkerr_dec_mean, 'Dec mean tracking error (last {:.0f}s)'.format(
                          trkerr_hist_time))
    header['DEC-TSTD'] = (trkerr_dec_std, 'Dec std tracking error (last {:.0f}s)'.format(
                          trkerr_hist_time))

    header['RA-CURR '] = (current_ra, 'RA motor current')
    header['RA-CMAX '] = (current_ra_max, 'RA max motor current (last {:.0f}s)'.format(
                          current_hist_time))
    header['RA-CMEA '] = (current_ra_mean, 'RA mean motor current (last {:.0f}s)'.format(
                          current_hist_time))
    header['RA-CSTD '] = (current_ra_std, 'RA std motor current (last {:.0f}s)'.format(
                          current_hist_time))
    header['DEC-CURR'] = (current_dec, 'Dec motor current')
    header['DEC-CMAX'] = (current_dec_max, 'Dec max motor current (last {:.0f}s)'.format(
                          current_hist_time))
    header['DEC-CMEA'] = (current_dec_mean, 'Dec mean motor current (last {:.0f}s)'.format(
                          current_hist_time))
    header['DEC-CSTD'] = (current_dec_std, 'Dec std motor current (last {:.0f}s)'.format(
                          current_hist_time))

    header['AIRMASS '] = (airmass, 'Airmass')

    header['ZENDIST '] = (zen_dist, 'Distance from zenith, degrees')

    header['SUNALT  '] = (sun_alt, 'Current Sun altitude, degrees')

    header['MOONALT '] = (moon_alt, 'Current Moon altitude, degrees')
    header['MOONILL '] = (moon_ill, 'Current Moon illumination, percent')
    header['MOONPHAS'] = (moon_phase, 'Current Moon phase, [DGB]')
    header['MOONDIST'] = (moon_dist, 'Distance from Moon, degrees')

    # Conditions info
    try:
        if all_info['conditions'] is None:
            raise ValueError('No conditions info provided')

        info = all_info['conditions']

        clouds = info['clouds']
        if clouds == -999:
            clouds = 'NA'

        seeing = info['tng']['seeing']
        if seeing == -999:
            seeing = 'NA'

        seeing_ing = info['robodimm']['seeing']
        if seeing_ing == -999:
            seeing_ing = 'NA'

        dust = info['tng']['dust']
        if dust == -999:
            dust = 'NA'

        ext_temp = info['weather_ext']['temperature']
        if ext_temp == -999:
            ext_temp = 'NA'

        ext_hum = info['weather_ext']['humidity']
        if ext_hum == -999:
            ext_hum = 'NA'

        ext_wind = info['weather_ext']['windspeed']
        if ext_wind == -999:
            ext_wind = 'NA'

        ext_winddir = info['weather_ext']['winddir']
        if ext_winddir == -999:
            ext_winddir = 'NA'

        ext_gust = info['weather_ext']['windgust']
        if ext_gust == -999:
            ext_gust = 'NA'

        if info['weather_ext']['windgust_history_info'] is None:
            hist_time = -999
            ext_gustmax = 'NA'
            ext_gustmean = 'NA'
            ext_guststd = 'NA'
        else:
            hist_time = info['weather_ext']['windgust_history_info']['hist_time']
            ext_gustmax = info['weather_ext']['windgust_history_info']['max']
            ext_gustmean = info['weather_ext']['windgust_history_info']['mean']
            ext_guststd = info['weather_ext']['windgust_history_info']['std']

        int_temp = info['weather_int']['temperature']
        if int_temp == -999:
            int_temp = 'NA'

        int_hum = info['weather_int']['humidity']
        if int_hum == -999:
            int_hum = 'NA'

    except Exception as err:
        if log is None:
            raise
        if 'info provided' in str(err):
            log.warning(str(err))
        else:
            log.error('Failed to write conditions info to header')
            log.debug('', exc_info=True)
        clouds = 'NA'
        seeing = 'NA'
        seeing_ing = 'NA'
        dust = 'NA'
        ext_temp = 'NA'
        ext_hum = 'NA'
        ext_wind = 'NA'
        ext_winddir = 'NA'
        ext_gust = 'NA'
        hist_time = -999
        ext_gustmax = 'NA'
        ext_gustmean = 'NA'
        ext_guststd = 'NA'
        int_temp = 'NA'
        int_hum = 'NA'

    header['SATCLOUD'] = (clouds, 'IR satellite cloud opacity, percent (sat24.com)')
    header['SEEING  '] = (seeing, 'Seeing, arcseconds (TNG DIMM)')
    header['SEEING2 '] = (seeing_ing, 'Seeing, arcseconds (ING RoboDIMM)')
    header['DUST    '] = (dust, 'Dust level, ug/m3 (TNG)')

    header['EXT-TEMP'] = (ext_temp, 'External temperature, Celsius (GOTO mast)')
    header['EXT-HUM '] = (ext_hum, 'External humidity, percent (GOTO mast)')
    header['EXT-WIND'] = (ext_wind, 'External wind speed, km/h (GOTO mast)')
    header['EXT-WDIR'] = (ext_winddir, 'External wind direction, degrees (GOTO mast)')
    header['EXT-GUST'] = (ext_gust, 'External wind gust, km/h (GOTO mast)')
    header['EXT-GMAX'] = (ext_gustmax, 'Max wind gust, km/h (last {:.0f}s)'.format(hist_time))
    header['EXT-GMEA'] = (ext_gustmean, 'Mean wind gust, km/h (last {:.0f}s)'.format(hist_time))
    header['EXT-GSTD'] = (ext_guststd, 'Std wind gust, km/h (last {:.0f}s)'.format(hist_time))

    header['INT-TEMP'] = (int_temp, 'Internal temperature, Celsius (dome)')
    header['INT-HUM '] = (int_hum, 'Internal humidity, percent (dome)')


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
