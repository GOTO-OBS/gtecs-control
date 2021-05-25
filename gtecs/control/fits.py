"""Functions to write FITS image files."""

import glob
import math
import os
import time
import threading
import warnings

import astropy.units as u
from astropy.coordinates import Angle
from astropy.io import fits
from astropy.time import Time

from gtecs.obs import database as db

from . import astronomy
from . import misc
from . import params
from .daemons import daemon_info
from .flags import Status


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
    night = astronomy.night_startdate()
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


def write_fits(image_data, filename, ut, all_info, compress=False, log=None):
    """Update an image's FITS header and save to a file."""
    # extract the hdu
    if compress:
        hdu = fits.CompImageHDU(image_data)
    else:
        hdu = fits.PrimaryHDU(image_data)

    # update the image header
    try:
        update_header(hdu.header, ut, all_info, log)
    except Exception:
        if log is None:
            raise
        log.error('Failed to update FITS header')
        log.debug('', exc_info=True)

    # write the image log to the database
    if not all_info['cam']['current_exposure']['glance']:
        try:
            write_image_log(filename, hdu.header)
        except Exception:
            if log is None:
                raise
            log.error('Failed to add entry to image log')
            log.debug('', exc_info=True)

    # create the hdulist
    if not isinstance(hdu, fits.PrimaryHDU):
        hdulist = fits.HDUList([fits.PrimaryHDU(), hdu])
    else:
        hdulist = fits.HDUList([hdu])

    # remove any existing file
    try:
        os.remove(filename)
    except FileNotFoundError:
        pass

    # write to a tmp file, then move it once it's finished (removes the need for .done files)
    try:
        hdulist.writeto(filename + '.tmp')
    except Exception:
        if log is None:
            raise
        log.error('Failed to write hdulist to file')
        log.debug('', exc_info=True)
    else:
        os.rename(filename + '.tmp', filename)

    # record image being saved
    interface_id = params.UT_DICT[ut]['INTERFACE']
    expstr = all_info['cam']['current_exposure']['expstr'].capitalize()
    if log:
        log.info('{}: Saved exposure from camera {} ({})'.format(expstr, ut, interface_id))
    else:
        print('{}: Saved exposure from camera {} ({})'.format(expstr, ut, interface_id))


def get_all_info(cam_info, log=None, log_debug=False):
    """Get all info dicts from the running daemons, and other common info."""
    all_info = {}

    # Camera daemon
    all_info['cam'] = cam_info

    # Get the info from the other daemons in parallel to save time
    def daemon_info_thread(daemon_id, log=None, log_debug=False):
        try:
            if log and log_debug:
                log.debug(f'Fetching "{daemon_id}" info')
            force_update = True if daemon_id != 'conditions' else False
            all_info[daemon_id] = daemon_info(daemon_id, force_update, timeout=60)
            if log and log_debug:
                log.debug(f'Fetched "{daemon_id}" info')
        except Exception:
            if log is None:
                raise
            log.error(f'Failed to fetch "{daemon_id}" info')
            log.debug('', exc_info=True)
            all_info[daemon_id] = None

    threads = [threading.Thread(target=daemon_info_thread, args=(daemon_id, log, log_debug))
               for daemon_id in ['ota', 'foc', 'filt', 'dome', 'mnt', 'conditions']]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Astronomy
    try:
        if log and log_debug:
            log.debug('Fetching astronomy info')
        now = Time.now()
        astro = {}
        astro['moon_alt'], astro['moon_ill'], astro['moon_phase'] = astronomy.get_moon_params(now)
        astro['sun_alt'] = astronomy.get_sunalt(Time.now())
        all_info['astro'] = astro
        if log and log_debug:
            log.debug('Fetched astronomy info')
    except Exception:
        if log is None:
            raise
        log.error('Failed to fetch astronomy info')
        log.debug('', exc_info=True)
        all_info['astro'] = None

    # Database
    db_info = {}
    if not cam_info['current_exposure']['from_db']:
        db_info['from_db'] = False
    else:
        db_info['from_db'] = True

        if log and log_debug:
            log.debug('Fetching database info')
        with db.open_session() as session:
            try:
                expset_id = cam_info['current_exposure']['db_id']
                expset = db.get_exposure_set_by_id(session, expset_id)
                db_info['expset'] = {}
                db_info['expset']['id'] = expset_id
            except Exception:
                if log is None:
                    raise
                log.error('Failed to fetch database expset')
                log.debug('', exc_info=True)
                expset = None

            if expset and expset.pointing:
                try:
                    pointing = expset.pointing
                    db_info['pointing'] = {}
                    db_info['pointing']['id'] = pointing.db_id
                    db_info['pointing']['rank'] = pointing.rank
                    db_info['pointing']['too'] = bool(pointing.too)
                    db_info['pointing']['minalt'] = pointing.min_alt
                    db_info['pointing']['maxsunalt'] = pointing.max_sunalt
                    db_info['pointing']['mintime'] = pointing.min_time
                    db_info['pointing']['maxmoon'] = pointing.max_moon
                    db_info['pointing']['minmoonsep'] = pointing.min_moonsep
                    starttime = pointing.start_time.strftime('%Y-%m-%dT%H:%M:%S')
                    db_info['pointing']['starttime'] = starttime
                    if pointing.stop_time:
                        stoptime = pointing.stop_time.strftime('%Y-%m-%dT%H:%M:%S')
                        db_info['pointing']['stoptime'] = stoptime
                    else:
                        db_info['pointing']['stoptime'] = 'None'

                    if pointing.user:
                        db_info['user'] = {}
                        db_info['user']['id'] = pointing.user.db_id
                        db_info['user']['name'] = pointing.user.username
                        db_info['user']['fullname'] = pointing.user.full_name

                    if pointing.mpointing:
                        db_info['mpointing'] = {}
                        db_info['mpointing']['id'] = pointing.mpointing.db_id
                        db_info['mpointing']['initialrank'] = pointing.mpointing.initial_rank
                        db_info['mpointing']['obsnum'] = pointing.mpointing.num_completed
                        db_info['mpointing']['target'] = pointing.mpointing.num_todo
                        db_info['mpointing']['infinite'] = bool(pointing.mpointing.infinite)

                    if pointing.time_block:
                        db_info['time_block'] = {}
                        db_info['time_block']['id'] = pointing.time_block.db_id
                        db_info['time_block']['num'] = pointing.time_block.block_num

                    if pointing.grid_tile:
                        db_info['grid'] = {}
                        db_info['grid']['id'] = pointing.grid_tile.grid.db_id
                        db_info['grid']['name'] = pointing.grid_tile.grid.name
                        db_info['grid']['tile_id'] = pointing.grid_tile.db_id
                        db_info['grid']['tile_name'] = pointing.grid_tile.name

                    if pointing.survey_tile:
                        db_info['survey'] = {}
                        db_info['survey']['id'] = pointing.survey_tile.survey.db_id
                        db_info['survey']['name'] = pointing.survey_tile.survey.name
                        db_info['survey']['tile_id'] = pointing.survey_tile.db_id
                        db_info['survey']['tile_weight'] = pointing.survey_tile.current_weight
                        db_info['survey']['tile_initial'] = pointing.survey_tile.initial_weight

                    if pointing.event:
                        db_info['event'] = {}
                        db_info['event']['id'] = pointing.event.db_id
                        db_info['event']['name'] = pointing.event.name
                        db_info['event']['type'] = pointing.event.event_type
                        db_info['event']['time'] = pointing.event.time.strftime('%Y-%m-%dT%H:%M:%S')
                        db_info['event']['ivorn'] = pointing.event.ivorn
                        db_info['event']['source'] = pointing.event.source
                        db_info['event']['skymap'] = pointing.event.skymap

                except Exception:
                    if log is None:
                        raise
                    log.error('Failed to fetch database info')
                    log.debug('', exc_info=True)
        if log and log_debug:
            log.debug('Fetched database info')

    all_info['db'] = db_info

    return all_info


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

    header['ORIGIN  '] = (params.ORG_NAME, 'Origin organisation')

    header['SITE    '] = (params.SITE_NAME, 'Site location')
    header['SITE-LAT'] = (params.SITE_LATITUDE, 'Site latitude, degrees +N')
    header['SITE-LON'] = (params.SITE_LONGITUDE, 'Site longitude, degrees +E')
    header['SITE-ALT'] = (params.SITE_ALTITUDE, 'Site elevation, m above sea level')

    header['TELESCOP'] = (params.TELESCOPE_NAME, 'Origin telescope name')
    header['TEL     '] = (params.TELESCOPE_NUMBER, 'Origin telescope ID number')

    header['INSTRUME'] = ('UT' + str(ut), 'Origin unit telescope')
    header['UT      '] = (ut, 'Integer UT number')

    if 'HW_VERSION' in params.UT_DICT[ut]:
        ut_hw_version = params.UT_DICT[ut]['HW_VERSION']
    else:
        ut_hw_version = 'NA'
    header['UT-VERS '] = (ut_hw_version, 'UT hardware version number')

    ut_mask = misc.ut_list_to_mask(exposure_info['ut_list'])
    ut_string = misc.ut_mask_to_string(ut_mask)
    header['UTMASK  '] = (ut_mask, 'Run UT mask integer')
    header['UTMASKBN'] = (ut_string, 'Run UT mask binary string')

    interface_id = params.UT_DICT[ut]['INTERFACE']
    header['INTERFAC'] = (interface_id, 'System interface code')

    header['SWVN    '] = (params.VERSION, 'Software version number')

    status = Status()
    header['SYS-MODE'] = (status.mode, 'Current telescope system mode')
    header['OBSERVER'] = (status.observer, 'Who started the exposure')

    header['OBJECT  '] = (exposure_info['target'], 'Observed object name')

    set_number = exposure_info['set_num']
    if set_number is None:
        set_number = 'NA'
    header['SET     '] = (set_number, 'GOTO set number')
    header['SET-POS '] = (exposure_info['set_pos'], 'Position of this exposure in this set')
    header['SET-TOT '] = (exposure_info['set_tot'], 'Total number of exposures in this set')

    # Exposure data
    header['EXPTIME '] = (exposure_info['exptime'], 'Exposure time, seconds')

    start_time = Time(cam_info['exposure_start_time'], format='unix')
    mid_time = start_time + (exposure_info['exptime'] * u.second) / 2.
    header['DATE-OBS'] = (start_time.isot, 'Exposure start time, UTC')
    header['DATE-MID'] = (mid_time.isot, 'Exposure midpoint, UTC')

    mid_jd = mid_time.jd
    header['JD      '] = (mid_jd, 'Exposure midpoint, Julian Date')

    lst = astronomy.get_lst(mid_time)
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
        from_db = info['from_db']
    except Exception:
        if log is None:
            raise
        log.error('Failed to write database info to header')
        log.debug('', exc_info=True)
        from_db = False

    header['FROMDB  '] = (from_db, 'Exposure linked to database set?')

    try:
        info = all_info['db']['expset']
        expset_id = info['id']
    except Exception:
        if from_db:
            if log is None:
                raise
            log.error('Failed to write exposure set info to header')
            log.debug('', exc_info=True)
        expset_id = 'NA'

    header['DB-EXPS '] = (expset_id, 'Database ExposureSet ID')

    try:
        info = all_info['db']['pointing']
        pointing_id = info['id']
        pointing_rank = info['rank']
        pointing_too = info['too']
        pointing_minalt = info['minalt']
        pointing_maxsunalt = info['maxsunalt']
        pointing_mintime = info['mintime']
        pointing_maxmoon = info['maxmoon']
        pointing_minmoonsep = info['minmoonsep']
        pointing_starttime = info['starttime']
        pointing_stoptime = info['stoptime']
    except Exception:
        if from_db:
            # Every ExposureSet should have a Pointing (or how else did we observe it?)
            if log is None:
                raise
            log.error('Failed to write pointing info to header')
            log.debug('', exc_info=True)
        pointing_id = 'NA'
        pointing_rank = 'NA'
        pointing_too = 'NA'
        pointing_minalt = 'NA'
        pointing_maxsunalt = 'NA'
        pointing_mintime = 'NA'
        pointing_maxmoon = 'NA'
        pointing_minmoonsep = 'NA'
        pointing_starttime = 'NA'
        pointing_stoptime = 'NA'

    header['DB-PNT  '] = (pointing_id, 'Database Pointing ID')
    header['RANK    '] = (pointing_rank, 'Rank of this pointing when observed')
    header['TOO     '] = (pointing_too, 'ToO flag for this pointing')
    header['LIM-ALT '] = (pointing_minalt, 'Minimum altitude limit for this pointing')
    header['LIM-SALT'] = (pointing_maxsunalt, 'Maximum Sun altitude limit for this pointing')
    header['LIM-MPHS'] = (pointing_maxmoon, 'Maximum Moon phase limit for this pointing')
    header['LIM-MDIS'] = (pointing_minmoonsep, 'Minimum Moon distance limit for this pointing')
    header['LIM-TIME'] = (pointing_mintime, 'Minimum valid time limit for this pointing')
    header['LIM-STRT'] = (pointing_starttime, 'Valid start time limit for this pointing')
    header['LIM-STOP'] = (pointing_stoptime, 'Valid stop time limit for this pointing')

    try:
        info = all_info['db']['user']
        user_id = info['id']
        user_name = info['name']
        user_fullname = info['fullname']
    except Exception:
        if from_db:
            # Every Pointing should have a User
            if log is None:
                raise
            log.error('Failed to fetch user info')
            log.debug('', exc_info=True)
        user_id = 'NA'
        user_name = 'NA'
        user_fullname = 'NA'

    header['DB-USER '] = (user_id, 'Database User ID who submitted this pointing')
    header['USERNAME'] = (user_name, 'Username that submitted this pointing')
    header['USERFULL'] = (user_fullname, 'User who submitted this pointing')

    try:
        info = all_info['db']['mpointing']
        mpointing_id = info['id']
        mpointing_initialrank = info['initialrank']
        mpointing_obsnum = info['obsnum']
        mpointing_target = info['target']
        mpointing_infinite = info['infinite']
    except Exception:
        if from_db and 'mpointing' in all_info['db']:
            # It's not necessarily an error if the info isn't there,
            # it might just not be connected to an mpointing
            if log is None:
                raise
            log.error('Failed to fetch mpointing info')
            log.debug('', exc_info=True)
        mpointing_id = 'NA'
        mpointing_initialrank = 'NA'
        mpointing_obsnum = 'NA'
        mpointing_target = 'NA'
        mpointing_infinite = 'NA'

    header['DB-MPNT '] = (mpointing_id, 'Database Mpointing ID')
    header['BASERANK'] = (mpointing_initialrank, 'Initial rank of this Mpointing')
    header['OBSNUM  '] = (mpointing_obsnum, 'Count of times this pointing has been observed')
    header['OBSTARG '] = (mpointing_target, 'Count of times this pointing should be observed')
    header['INFINITE'] = (mpointing_infinite, 'Is this an infinitely repeating pointing?')

    try:
        info = all_info['db']['time_block']
        time_block_id = info['id']
        time_block_num = info['num']
    except Exception:
        if from_db and 'mpointing' in all_info['db']:
            # It's not necessarily an error if the info isn't there,
            # it might just not be connected to a time block
            if log is None:
                raise
            log.error('Failed to fetch time block info')
            log.debug('', exc_info=True)
        time_block_id = 'NA'
        time_block_num = 'NA'

    header['DB-TIMBK'] = (time_block_id, 'Database TimeBlock ID')
    header['TIMBKNUM'] = (time_block_num, 'Number of this time block')

    try:
        info = all_info['db']['grid']
        grid_id = info['id']
        grid_name = info['name']
        grid_tile_id = info['tile_id']
        grid_tile_name = info['tile_name']
    except Exception:
        if from_db and 'grid' in all_info['db']:
            # It's not necessarily an error if the info isn't there,
            # it might just not be connected to a grid
            if log is None:
                raise
            log.error('Failed to fetch grid tile info')
            log.debug('', exc_info=True)
        grid_id = 'NA'
        grid_name = 'NA'
        grid_tile_id = 'NA'
        grid_tile_name = 'NA'

    header['DB-GRID '] = (grid_id, 'Database Grid ID')
    header['GRID    '] = (grid_name, 'Sky grid name')
    header['DB-GTILE'] = (grid_tile_id, 'Database GridTile ID')
    header['TILENAME'] = (grid_tile_name, 'Name of this grid tile')

    try:
        info = all_info['db']['survey']
        survey_id = info['id']
        survey_name = info['name']
        survey_tile_id = info['tile_id']
        survey_tile_weight = info['tile_weight']
        survey_tile_initial = info['tile_initial']
    except Exception:
        if from_db and 'survey' in all_info['db']:
            # It's not necessarily an error if the info isn't there,
            # it might just not be connected to a survey
            if log is None:
                raise
            log.error('Failed to fetch survey tile info')
            log.debug('', exc_info=True)
        survey_id = 'NA'
        survey_name = 'NA'
        survey_tile_id = 'NA'
        survey_tile_weight = 'NA'
        survey_tile_initial = 'NA'

    header['DB-SURVY'] = (survey_id, 'Database Survey ID')
    header['SURVEY  '] = (survey_name, 'Name of this survey')
    header['DB-STILE'] = (survey_tile_id, 'Database SurveyTile ID')
    header['WEIGHT  '] = (survey_tile_weight, 'Survey tile current weighting')
    header['INWEIGHT'] = (survey_tile_initial, 'Survey tile initial weighting')

    try:
        info = all_info['db']['event']
        event_id = info['id']
        event_name = info['name']
        event_type = info['type']
        event_time = info['time']
        event_ivorn = info['ivorn']
        event_source = info['source']
        event_skymap = info['skymap']
    except Exception:
        if from_db and 'event' in all_info['db']:
            # It's not necessarily an error if the info isn't there
            if log is None:
                raise
            log.error('Failed to fetch event info')
            log.debug('', exc_info=True)
        event_id = 'NA'
        event_name = 'NA'
        event_type = 'NA'
        event_time = 'NA'
        event_ivorn = 'NA'
        event_source = 'NA'
        event_skymap = 'NA'

    header['DB-EVENT'] = (event_id, 'Database Event ID')
    header['EVENT   '] = (event_name, 'Event name for this pointing')
    header['EVNTTYPE'] = (event_type, 'Type of event')
    header['EVNTTIME'] = (event_time, 'Recorded time of the event')
    header['IVORN   '] = (event_ivorn, 'IVOA identifier for this event')
    header['SOURCE  '] = (event_source, 'Source of this event')
    header['SKYMAP  '] = (event_skymap, 'Skymap URL for this event')

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
        if ut not in params.UTS_WITH_COVERS:
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
    except Exception:
        if log is None:
            raise
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

        if ut not in params.UTS_WITH_FOCUSERS:
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
    except Exception:
        if log is None:
            raise
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

        if ut not in params.UTS_WITH_FILTERWHEELS:
            filt_serial = 'None'
            filt_class = 'NA'
            filt_filter = 'C'
            filt_filters = 'C'
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
                filt_filter_num = info['current_filter_num']
                filt_filter = params.FILTER_LIST[filt_filter_num]
            filt_filters = ','.join(params.FILTER_LIST)
            filt_num = info['current_filter_num']
            filt_pos = info['current_pos']
            filt_move_time = info['last_move_time']
            if filt_move_time is not None:
                filt_move_time = Time(filt_move_time, format='unix')
                filt_move_time = filt_move_time.isot
            else:
                filt_move_time = 'NA'
    except Exception:
        if log is None:
            raise
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

    except Exception:
        if log is None:
            raise
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

        mnt_ra = info['mount_ra']
        mnt_ra_str = Angle(mnt_ra * u.hour).to_string(sep=':', precision=3, alwayssign=True)

        mnt_dec = info['mount_dec']
        mnt_dec_str = Angle(mnt_dec * u.deg).to_string(sep=':', precision=3, alwayssign=True)

        mnt_alt = info['mount_alt']
        mnt_az = info['mount_az']
        ha = astronomy.get_ha(info['mount_ra'], lst.hour)  # LST is found under exposure data

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

        zen_dist = 90 - mnt_alt
        airmass = 1 / (math.cos(math.pi / 2 - (mnt_alt * math.pi / 180)))
        equinox = 2000

        mnt_ra_deg = mnt_ra * 180 / 12.
        moon_dist = astronomy.get_moon_distance(mnt_ra_deg, mnt_dec, Time.now())

    except Exception:
        if log is None:
            raise
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
        zen_dist = 'NA'
        airmass = 'NA'
        equinox = 'NA'
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

    header['AIRMASS '] = (airmass, 'Airmass')

    header['ZENDIST '] = (zen_dist, 'Distance from zenith, degrees')

    header['MOONDIST'] = (moon_dist, 'Distance from Moon, degrees')

    # Astronomy info
    try:
        if all_info['astro'] is None:
            raise ValueError('No astronomy info provided')

        info = all_info['astro']

        moon_alt = info['moon_alt']
        moon_ill = info['moon_ill'] * 100
        moon_phase = info['moon_phase']

        sun_alt = info['sun_alt']
    except Exception:
        if log is None:
            raise
        log.error('Failed to write astronomy info to header')
        log.debug('', exc_info=True)
        moon_alt = 'NA'
        moon_ill = 'NA'
        moon_phase = 'NA'
        sun_alt = 'NA'

    header['MOONALT '] = (moon_alt, 'Current Moon altitude, degrees')
    header['MOONILL '] = (moon_ill, 'Current Moon illumination, percent')
    header['MOONPHAS'] = (moon_phase, 'Current Moon phase, [DGB]')
    header['SUNALT  '] = (sun_alt, 'Current Sun altitude, degrees')

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

        ext_source = params.EXTERNAL_WEATHER_SOURCES[0]
        ext_weather = info['weather'][ext_source]

        ext_temp = ext_weather['temperature']
        if ext_temp == -999:
            ext_temp = 'NA'

        ext_hum = ext_weather['humidity']
        if ext_hum == -999:
            ext_hum = 'NA'

        ext_wind = ext_weather['windspeed']
        if ext_wind == -999:
            ext_wind = 'NA'

        ext_gust = ext_weather['windgust']
        if ext_gust == -999:
            ext_gust = 'NA'

        int_source = params.INTERNAL_WEATHER_SOURCES[0]
        int_weather = info['weather'][int_source]

        int_temp = int_weather['temperature']
        if int_temp == -999:
            int_temp = 'NA'

        int_hum = int_weather['humidity']
        if int_hum == -999:
            int_hum = 'NA'

    except Exception:
        if log is None:
            raise
        log.error('Failed to write conditions info to header')
        log.debug('', exc_info=True)
        clouds = 'NA'
        seeing = 'NA'
        seeing_ing = 'NA'
        dust = 'NA'
        ext_temp = 'NA'
        ext_hum = 'NA'
        ext_wind = 'NA'
        ext_gust = 'NA'
        int_temp = 'NA'
        int_hum = 'NA'

    header['SATCLOUD'] = (clouds, 'IR satellite cloud opacity, percent (sat24.com)')
    header['SEEING  '] = (seeing, 'Seeing, arcseconds (TNG DIMM)')
    header['SEEING2 '] = (seeing_ing, 'Seeing, arcseconds (ING RoboDIMM)')
    header['DUST    '] = (dust, 'Dust level, ug/m3 (TNG)')

    header['EXT-TEMP'] = (ext_temp, 'External temperature, Celsius (GOTO mast)')
    header['EXT-HUM '] = (ext_hum, 'External humidity, percent (GOTO mast)')
    header['EXT-WIND'] = (ext_wind, 'External wind speed, km/h (GOTO mast)')
    header['EXT-GUST'] = (ext_wind, 'External wind gust, km/h (last {:.0f}s)'.format(
                          params.WINDGUST_PERIOD))

    header['INT-TEMP'] = (int_temp, 'Internal temperature, Celsius (dome)')
    header['INT-HUM '] = (int_hum, 'Internal humidity, percent (dome)')


def write_image_log(filename, header):
    """Add an image log to the database for this frame."""
    filename = filename.split('/')[-1]
    run_number = int(header['RUN     '])
    ut = int(header['UT      '])
    ut_mask = int(header['UTMASK  '])
    start_time = Time(header['DATE-OBS'])
    write_time = Time(header['DATE    '])
    set_pos = int(header['SET-POS '])
    set_tot = int(header['SET-TOT '])

    expset_id = None
    pointing_id = None
    mpointing_id = None

    if header['DB-EXPS '] != 'NA':
        expset_id = header['DB-EXPS ']
    if header['DB-PNT  '] != 'NA':
        pointing_id = header['DB-PNT  ']
    if header['DB-MPNT '] != 'NA':
        mpointing_id = header['DB-MPNT ']

    log = db.ImageLog(filename=filename, run_number=run_number, ut=ut,
                      ut_mask=ut_mask, start_time=start_time, write_time=write_time,
                      set_position=set_pos, set_total=set_tot,
                      exposure_set_id=expset_id, pointing_id=pointing_id, mpointing_id=mpointing_id)

    with db.open_session() as session:
        session.add(log)
        session.commit()


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


def get_image_data(run_number=None, direc=None, uts=None):
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

    Returns
    -------
    data : dict
        a dictionary of the image data, with the UT numbers as keys

    """
    if direc is None:
        direc = astronomy.night_startdate()
    path = os.path.join(params.IMAGE_PATH, direc)

    if uts is None:
        uts = params.UTS_WITH_CAMERAS

    if run_number is None:
        newest = max(glob.iglob(os.path.join(path, '*.fits')), key=os.path.getmtime)
        run = os.path.basename(newest).split('_')[1]
        run_number = int(run[1:])

    filenames = {ut: image_filename(params.TELESCOPE_NUMBER, run_number, ut) for ut in uts}
    filepaths = {ut: os.path.join(path, filenames[ut]) for ut in filenames}

    # limit it to only existing files
    filepaths = {ut: filepaths[ut] for ut in filepaths if os.path.exists(filepaths[ut])}
    print('Loading run r{:07d}: {} images'.format(run_number, len(filepaths)))

    # read the files
    data = {ut: read_fits(filepaths[ut]) for ut in filepaths}
    return data


def get_glance_data(uts=None):
    """Open the most recent glance images and return the data.

    Parameters
    ----------
    uts : list of ints, default=None
        the UTs to read the files of
        if None, open files from all UTs

    Returns
    -------
    data : dict
        a dictionary of the image data, with the UT numbers as keys

    """
    if uts is None:
        uts = params.UTS_WITH_CAMERAS

    filenames = {ut: glance_filename(params.TELESCOPE_NUMBER, ut) for ut in uts}
    filepaths = {ut: os.path.join(params.IMAGE_PATH, filenames[ut]) for ut in filenames}

    # limit it to only existing files
    filepaths = {ut: filepaths[ut] for ut in filepaths if os.path.exists(filepaths[ut])}
    print('Loading glances: {} images'.format(len(filepaths)))

    # read the files
    data = {ut: read_fits(filepaths[ut]) for ut in filepaths}
    return data
