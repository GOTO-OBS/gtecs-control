"""Functions to write FITS image files."""

import math
import os

import astropy.io.fits as pyfits
import astropy.units as u
from astropy.coordinates import Angle
from astropy.time import Time

import numpy

import obsdb as db

from . import astronomy
from . import misc
from . import params
from .daemons import daemon_info
from .flags import Status


def image_location(run_number, tel):
    """Construct the image file location based on the run and tel number."""
    # Find the directory, using the date the observing night began
    night = astronomy.night_startdate()
    direc = os.path.join(params.IMAGE_PATH, night)
    if not os.path.exists(direc):
        os.mkdir(direc)

    # Find the file name, using the run number and UT number
    filename = 'r{:07d}_UT{:d}.fits'.format(run_number, tel)

    return os.path.join(direc, filename)


def glance_location(tel):
    """Construct the glance file location based on the tel number."""
    # Find the directory
    direc = params.IMAGE_PATH
    if not os.path.exists(direc):
        os.mkdir(direc)

    # Find the file name, using the run number and UT number
    filename = 'glance_UT{:d}.fits'.format(tel)

    return os.path.join(direc, filename)


def write_fits(image, filename, tel, all_info, log=None):
    """Update an image's FITS header and save to a file."""
    # extract the hdu
    hdu = pyfits.PrimaryHDU(image)

    # update the image header
    run_number = all_info['cam']['run_number']
    update_header(hdu.header, tel, all_info, log)

    # write the image log to the database
    if run_number > 0:
        write_image_log(filename, hdu.header)

    # recreate the hdulist, and write to file
    hdulist = pyfits.HDUList([hdu])
    if os.path.exists(filename):
        os.remove(filename)
    hdulist.writeto(filename)

    if log:
        if run_number > 0:
            log.info('Exposure r{:07} saved'.format(run_number))
        else:
            log.info('Glance saved')


def get_all_info(cam_info, log):
    """Get all info dicts from the running daemons, and other common info."""
    all_info = {}

    # Camera daemon
    all_info['cam'] = cam_info

    # Focuser info
    try:
        all_info['foc'] = daemon_info('foc')
    except Exception:
        log.error('Failed to fetch focuser info')
        log.debug('', exc_info=True)
        all_info['foc'] = None

    # Filter wheel info
    try:
        all_info['filt'] = daemon_info('filt')
    except Exception:
        log.error('Failed to fetch filter wheel info')
        log.debug('', exc_info=True)
        all_info['filt'] = None

    # Dome info
    try:
        all_info['dome'] = daemon_info('dome')
    except Exception:
        log.error('Failed to fetch dome info')
        log.debug('', exc_info=True)
        all_info['dome'] = None

    # Mount info
    try:
        all_info['mnt'] = daemon_info('mnt')
    except Exception:
        log.error('Failed to fetch mount info')
        log.debug('', exc_info=True)
        all_info['mnt'] = None

    # Conditions info
    try:
        all_info['conditions'] = daemon_info('conditions')
    except Exception:
        log.error('Failed to fetch conditions info')
        log.debug('', exc_info=True)
        all_info['conditions'] = None

    # Astronomy
    now = Time.now()
    astro = {}
    astro['moon_alt'], astro['moon_ill'], astro['moon_phase'] = astronomy.get_moon_params(now)
    astro['sun_alt'] = astronomy.get_sunalt(Time.now())
    all_info['astro'] = astro

    # Database
    expset_id = cam_info['current_exposure']['db_id']
    db = {}
    if expset_id == 0:
        db['from_db'] = False
    else:
        db['from_db'] = True

        with db.open_session() as session:
            try:
                expset = db.get_exposure_set_by_id(session, expset_id)
                db['expset'] = {}
                db['expset']['id'] = expset_id
            except Exception:
                pointing = None
                log.error('Failed to fetch database expset')
                log.debug('', exc_info=True)

            if expset and expset.pointing:
                try:
                    pointing = expset.pointing
                    db['pointing'] = {}
                    db['pointing']['id'] = pointing.db_id
                    db['pointing']['rank'] = pointing.rank
                    db['pointing']['too'] = bool(pointing.too)
                    db['pointing']['minalt'] = pointing.min_alt
                    db['pointing']['maxsunalt'] = pointing.max_sunalt
                    db['pointing']['mintime'] = pointing.min_time
                    db['pointing']['maxmoon'] = pointing.max_moon
                    db['pointing']['minmoonsep'] = pointing.min_moonsep
                    db['pointing']['starttime'] = pointing.start_time.strftime("%Y-%m-%dT%H:%M:%S")
                    if pointing.stop_time:
                        stoptime = pointing.stop_time.strftime("%Y-%m-%dT%H:%M:%S")
                        db['pointing']['stoptime'] = stoptime
                    else:
                        db['pointing']['stoptime'] = 'None'

                    if pointing.user:
                        db['user'] = {}
                        db['user']['id'] = pointing.user.db_id
                        db['user']['name'] = pointing.user.username
                        db['user']['fullname'] = pointing.user.full_name

                    if pointing.mpointing:
                        db['mpointing'] = {}
                        db['mpointing']['id'] = pointing.mpointing.db_id
                        db['mpointing']['initialrank'] = pointing.mpointing.initial_rank
                        db['mpointing']['obsnum'] = pointing.mpointing.num_completed
                        db['mpointing']['target'] = pointing.mpointing.num_todo
                        db['mpointing']['infinite'] = bool(pointing.mpointing.infinite)

                    if pointing.time_block:
                        db['time_block'] = {}
                        db['time_block']['id'] = pointing.time_block.db_id
                        db['time_block']['num'] = pointing.time_block.block_num

                    if pointing.grid_tile:
                        db['grid'] = {}
                        db['grid']['id'] = pointing.grid_tile.grid.db_id
                        db['grid']['name'] = pointing.grid_tile.grid.name
                        db['grid']['tile_id'] = pointing.grid_tile.db_id
                        db['grid']['tile_name'] = pointing.grid_tile.name

                    if pointing.survey_tile:
                        db['survey'] = {}
                        db['survey']['id'] = pointing.survey_tile.survey.db_id
                        db['survey']['name'] = pointing.survey_tile.survey.name
                        db['survey']['tile_id'] = pointing.survey_tile.db_id
                        db['survey']['tile_weight'] = pointing.survey_tile.current_weight
                        db['survey']['tile_initialweight'] = pointing.survey_tile.initial_weight

                    if pointing.event:
                        db['event'] = {}
                        db['event']['id'] = pointing.event.db_id
                        db['event']['name'] = pointing.event.name
                        db['event']['ivorn'] = pointing.event.ivorn
                        db['event']['source'] = pointing.event.source
                        db['event']['type'] = pointing.event.event_type
                        db['event']['time'] = pointing.event.time.strftime("%Y-%m-%dT%H:%M:%S")

                except Exception:
                    log.error('Failed to fetch database info')
                    log.debug('', exc_info=True)

    all_info['db'] = db

    return all_info


def update_header(header, tel, all_info, log):
    """Add observation, exposure and hardware info to the FITS header."""
    # These cards are set automatically by AstroPy, we just give them better comments
    header.comments["SIMPLE  "] = "Standard FITS"
    header.comments["BITPIX  "] = "Bits per pixel"
    header.comments["NAXIS   "] = "Number of dimensions"
    header.comments["NAXIS1  "] = "Number of columns"
    header.comments["NAXIS2  "] = "Number of rows"
    header.comments["EXTEND  "] = "Can contain extensions"
    header.comments["BSCALE  "] = "Pixel scale factor"
    header.comments["BZERO   "] = "Real = Pixel * BSCALE + BZERO"

    # Observation info
    cam_info = all_info['cam']
    run_number = cam_info['run_number']
    run_id = 'r{:07d}'.format(run_number)
    header["RUN     "] = (run_number, "GOTO run number")
    header["RUN-ID  "] = (run_id, "Padded run ID string")

    write_time = Time.now()
    write_time.precision = 0
    header["DATE    "] = (write_time.isot, "Date HDU created")

    header["ORIGIN  "] = (params.ORIGIN, "Origin organisation")
    header["TELESCOP"] = (params.TELESCOP, "Origin telescope")

    intf, hw = params.TEL_DICT[tel]
    current_exposure = cam_info['current_exposure']
    ut_mask = misc.ut_list_to_mask(current_exposure['tel_list'])
    ut_string = misc.ut_mask_to_string(ut_mask)
    header["INSTRUME"] = ('UT' + str(tel), "Origin unit telescope")
    header["UT      "] = (tel, "Integer UT number")
    header["UTMASK  "] = (ut_mask, "Run UT mask integer")
    header["UTMASKBN"] = (ut_string, "Run UT mask binary string")
    header["INTERFAC"] = (intf + '-' + str(hw), "System interface code")

    header["SWVN    "] = (params.VERSION, "Software version number")

    status = Status()
    header["SYS-MODE"] = (status.mode, "Current telescope system mode")
    header["OBSERVER"] = (status.observer, "Who started the exposure")

    header["OBJECT  "] = (current_exposure['target'], "Observed object name")

    header["SET-POS "] = (current_exposure['set_pos'], "Position of this exposure in this set")
    header["SET-TOT "] = (current_exposure['set_total'], "Total number of exposures in this set")

    header["SITE-LAT"] = (params.SITE_LATITUDE, "Site latitude, degrees +N")
    header["SITE-LON"] = (params.SITE_LONGITUDE, "Site longitude, degrees +E")
    header["SITE-ALT"] = (params.SITE_ALTITUDE, "Site elevation, m above sea level")
    header["SITE-LOC"] = (params.SITE_LOCATION, "Site location")

    # Exposure data
    header["EXPTIME "] = (current_exposure['exptime'], "Exposure time, seconds")

    start_time = Time(cam_info['exposure_start_time'], format='unix')
    start_time.precision = 0
    mid_time = start_time + (current_exposure['exptime'] * u.second) / 2.
    header["DATE-OBS"] = (start_time.isot, "Exposure start time, UTC")
    header["DATE-MID"] = (mid_time.isot, "Exposure midpoint, UTC")

    mid_jd = mid_time.jd
    header["JD      "] = (mid_jd, "Exposure midpoint, Julian Date")

    lst = astronomy.find_lst(mid_time)
    lst_m, lst_s = divmod(abs(lst) * 3600, 60)
    lst_h, lst_m = divmod(lst_m, 60)
    if lst < 0:
        lst_h = -lst_h
    mid_lst = '{:02.0f}:{:02.0f}:{:02.0f}'.format(lst_h, lst_m, lst_s)
    header["LST     "] = (mid_lst, "Exposure midpoint, Local Sidereal Time")

    # Frame info
    header["FRMTYPE "] = (current_exposure['frametype'], "Frame type (shutter open/closed)")
    header["IMGTYPE "] = (current_exposure['imgtype'], "Image type")

    header["FULLSEC "] = ('[1:8304,1:6220]', "Size of the full frame")
    header["TRIMSEC "] = ('[65:8240,46:6177]', "Central data region (both channels)")

    header["CHANNELS"] = (2, "Number of CCD channels")

    header["TRIMSEC1"] = ('[65:4152,46:6177]', "Data section for left channel")
    header["TRIMSEC2"] = ('[4153:8240,46:6177]', "Data section for right channel")
    header["BIASSEC1"] = ('[3:10,3:6218]', "Recommended bias section for left channel")
    header["BIASSEC2"] = ('[8295:8302,3:6218]', "Recommended bias section for right channel")
    header["DARKSEC1"] = ('[26:41,500:5721]', "Recommended dark section for left channel")
    header["DARKSEC2"] = ('[8264:8279,500:5721]', "Recommended dark section for right channel")

    # Database info
    try:
        if all_info['db'] is None:
            raise ValueError('No database info provided')
        info = all_info['db']
        from_db = info['from_db']
    except Exception:
        log.error('Failed to write database info to header')
        log.debug('', exc_info=True)
        from_db = False

    header["FROMDB  "] = (from_db, "Exposure linked to database set?")

    try:
        info = all_info['db']['expset']
        expset_id = info['id']
    except Exception:
        if from_db:
            log.error('Failed to write exposure set info to header')
            log.debug('', exc_info=True)
        expset_id = 'NA'

    header["DB-EXPS "] = (expset_id, "Database ExposureSet ID")

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

    header["DB-PNT  "] = (pointing_id, "Database Pointing ID")
    header["RANK    "] = (pointing_rank, "Rank of this pointing when observed")
    header["TOO     "] = (pointing_too, "ToO flag for this pointing")
    header["LIM-ALT "] = (pointing_minalt, "Minimum altitude limit for this pointing")
    header["LIM-SALT"] = (pointing_maxsunalt, "Maximum Sun altitude limit for this pointing")
    header["LIM-MPHS"] = (pointing_maxmoon, "Maximum Moon phase limit for this pointing")
    header["LIM-MDIS"] = (pointing_minmoonsep, "Minimum Moon distance limit for this pointing")
    header["LIM-TIME"] = (pointing_mintime, "Minimum valid time limit for this pointing")
    header["LIM-STRT"] = (pointing_starttime, "Valid start time limit for this pointing")
    header["LIM-STOP"] = (pointing_stoptime, "Valid stop time limit for this pointing")

    try:
        info = all_info['db']['user']
        user_id = info['id']
        user_name = info['name']
        user_fullname = info['full_name']
    except Exception:
        if from_db:
            # Every Pointing should have a User
            log.error('Failed to fetch user info')
            log.debug('', exc_info=True)
        user_id = 'NA'
        user_name = 'NA'
        user_fullname = 'NA'

    header["DB-USER "] = (user_id, "Database User ID who submitted this pointing")
    header["USERNAME"] = (user_name, "Username that submitted this pointing")
    header["USERFULL"] = (user_fullname, "User who submitted this pointing")

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
            log.error('Failed to fetch mpointing info')
            log.debug('', exc_info=True)
        mpointing_id = 'NA'
        mpointing_initialrank = 'NA'
        mpointing_obsnum = 'NA'
        mpointing_target = 'NA'
        mpointing_infinite = 'NA'

    header["DB-MPNT "] = (mpointing_id, "Database Mpointing ID")
    header["BASERANK"] = (mpointing_initialrank, "Initial rank of this Mpointing")
    header["OBSNUM  "] = (mpointing_obsnum, "Count of times this pointing has been observed")
    header["OBSTARG "] = (mpointing_target, "Count of times this pointing should be observed")
    header["INFINITE"] = (mpointing_infinite, "Is this an infinitely repeating pointing?")

    try:
        info = all_info['db']['time_block']
        time_block_id = info['id']
        time_block_num = info['num']
    except Exception:
        if from_db and 'mpointing' in all_info['db']:
            # It's not necessarily an error if the info isn't there,
            # it might just not be connected to a time block
            log.error('Failed to fetch time block info')
            log.debug('', exc_info=True)
        time_block_id = 'NA'
        time_block_num = 'NA'

    header["DB-TIMBK"] = (time_block_id, "Database TimeBlock ID")
    header["TIMBKNUM"] = (time_block_num, "Number of this time block")

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
            log.error('Failed to fetch grid tile info')
            log.debug('', exc_info=True)
        grid_id = 'NA'
        grid_name = 'NA'
        grid_tile_id = 'NA'
        grid_tile_name = 'NA'

    header["DB-GRID "] = (grid_id, "Database Grid ID")
    header["GRID    "] = (grid_name, "Sky grid name")
    header["DB-GTILE"] = (grid_tile_id, "Database GridTile ID")
    header["TILENAME"] = (grid_tile_name, "Name of this grid tile")

    try:
        info = all_info['db']['survey']
        survey_id = info['id']
        survey_name = info['name']
        survey_tile_id = info['tile_id']
        survey_tile_weight = info['tile_weight']
        survey_tile_initialweight = info['tile_initialweight']
    except Exception:
        if from_db and 'survey' in all_info['db']:
            # It's not necessarily an error if the info isn't there,
            # it might just not be connected to a survey
            log.error('Failed to fetch survey tile info')
            log.debug('', exc_info=True)
        survey_id = 'NA'
        survey_name = 'NA'
        survey_tile_id = 'NA'
        survey_tile_weight = 'NA'
        survey_tile_initialweight = 'NA'

    header["DB-SURVY"] = (survey_id, "Database Survey ID")
    header["SURVEY  "] = (survey_name, "Name of this survey")
    header["DB-STILE"] = (survey_tile_id, "Database SurveyTile ID")
    header["WEIGHT  "] = (survey_tile_weight, "Survey tile current weighting")
    header["INWEIGHT"] = (survey_tile_initialweight, "Survey tile initial weighting")

    try:
        info = all_info['db']['event']
        event_id = info['id']
        event_name = info['name']
        event_ivorn = info['ivorn']
        event_source = info['source']
        event_type = info['type']
        event_time = info['time']
    except Exception:
        if from_db and 'survey' in all_info['db']:
            # It's not necessarily an error if the info isn't there
            log.error('Failed to fetch event info')
            log.debug('', exc_info=True)
        event_id = 'NA'
        event_name = 'NA'
        event_ivorn = 'NA'
        event_source = 'NA'
        event_type = 'NA'
        event_time = 'NA'

    header["DB-EVENT"] = (event_id, "Database Event ID")
    header["EVENT   "] = (event_name, "Event name for this pointing")
    header["IVORN   "] = (event_ivorn, "IVOA identifier for this event")
    header["SOURCE  "] = (event_source, "Source of this event")
    header["EVNTTYPE"] = (event_type, "Type of event")
    header["EVNTTIME"] = (event_time, "Recorded time of the event")

    # Camera info
    cam_info = cam_info[tel]
    cam_serial = cam_info['serial_number']
    header["CAMERA  "] = (cam_serial, "Camera serial number")

    header["XBINNING"] = (current_exposure['binning'], "CCD x binning factor")
    header["YBINNING"] = (current_exposure['binning'], "CCD y binning factor")

    x_pixel_size = cam_info['x_pixel_size'] * current_exposure['binning']
    y_pixel_size = cam_info['y_pixel_size'] * current_exposure['binning']
    header["XPIXSZ  "] = (x_pixel_size, "Binned x pixel size, microns")
    header["YPIXSZ  "] = (y_pixel_size, "Binned y pixel size, microns")

    header["CCDTEMP "] = (cam_info['ccd_temp'], "CCD temperature, C")
    header["CCDTEMPS"] = (cam_info['target_temp'], "Requested CCD temperature, C")
    header["BASETEMP"] = (cam_info['base_temp'], "Peltier base temperature, C")

    # Focuser info
    try:
        if all_info['foc'] is None:
            raise ValueError('No focuser info provided')

        info = all_info['foc'][tel]

        foc_serial = info['serial_number']
        foc_pos = info['current_pos']
        foc_temp_int = info['int_temp']
        foc_temp_ext = info['ext_temp']
    except Exception:
        log.error('Failed to write focuser info to header')
        log.debug('', exc_info=True)
        foc_serial = 'NA'
        foc_pos = 'NA'
        foc_temp_int = 'NA'
        foc_temp_ext = 'NA'

    header["FOCUSER "] = (foc_serial, "Focuser serial number")
    header["FOCPOS  "] = (foc_pos, "Focuser motor position")
    header["FOCTEMPI"] = (foc_temp_int, "Focuser internal temperature, C")
    header["FOCTEMPX"] = (foc_temp_ext, "Focuser external temperature, C")

    # Filter wheel info
    try:
        if all_info['filt'] is None:
            raise ValueError('No filter wheel info provided')

        info = all_info['filt'][tel]

        filt_serial = info['serial_number']
        if not info['homed']:
            filt_filter = 'UNHOMED'
        else:
            filt_filter_num = info['current_filter_num']
            filt_filter = params.FILTER_LIST[filt_filter_num]
        filt_num = info['current_filter_num']
        filt_pos = info['current_pos']
    except Exception:
        log.error('Failed to write filter wheel info to header')
        log.debug('', exc_info=True)
        filt_serial = 'NA'
        filt_filter = 'NA'
        filt_num = 'NA'
        filt_pos = 'NA'
    filter_list_str = ''.join(params.FILTER_LIST)

    header["FLTWHEEL"] = (filt_serial, "Filter wheel serial number")
    header["FILTER  "] = (filt_filter, "Filter used for exposure [{}]".format(filter_list_str))
    header["FILTNUM "] = (filt_num, "Filter wheel position number")
    header["FILTPOS "] = (filt_pos, "Filter wheel motor position")

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

    except Exception:
        log.error('Failed to write dome info to header')
        log.debug('', exc_info=True)
        dome_status = 'NA'
        dome_open = 'NA'

    header["DOMESTAT"] = (dome_status, "Dome status")
    header["DOMEOPEN"] = (dome_open, "Dome is open")

    # Mount info
    try:
        if all_info['mnt'] is None:
            raise ValueError('No mount info provided')

        info = all_info['mnt']

        mount_tracking = info['status'] == 'Tracking'

        targ_ra = info['target_ra']
        if targ_ra:
            targ_ra_str = Angle(targ_ra * u.hour).to_string(sep=':', precision=1, alwayssign=True)
        else:
            targ_ra_str = 'NA'

        targ_dec = info['target_dec']
        if targ_dec:
            targ_dec_str = Angle(targ_dec * u.deg).to_string(sep=':', precision=1, alwayssign=True)
        else:
            targ_dec_str = 'NA'

        targ_dist_a = info['target_dist']
        if targ_dist_a:
            targ_dist = numpy.around(targ_dist_a, decimals=1)
        else:
            targ_dist = 'NA'

        mnt_ra = info['mount_ra']
        mnt_ra_str = Angle(mnt_ra * u.hour).to_string(sep=':', precision=1, alwayssign=True)

        mnt_dec = info['mount_dec']
        mnt_dec_str = Angle(mnt_dec * u.deg).to_string(sep=':', precision=1, alwayssign=True)

        mnt_alt = numpy.around(info['mount_alt'], decimals=2)
        mnt_az = numpy.around(info['mount_az'], decimals=2)

        zen_dist = numpy.around(90 - mnt_alt, decimals=1)
        airmass = 1 / (math.cos(math.pi / 2 - (mnt_alt * math.pi / 180)))
        airmass = numpy.around(airmass, decimals=2)
        equinox = 2000

        mnt_ra_deg = mnt_ra * 180 / 12.
        moon_dist = astronomy.get_moon_distance(mnt_ra_deg, mnt_dec, Time.now())
        moon_dist = numpy.around(moon_dist, decimals=2)

    except Exception:
        log.error('Failed to write mount info to header')
        log.debug('', exc_info=True)
        mount_tracking = 'NA'
        targ_ra_str = 'NA'
        targ_dec_str = 'NA'
        targ_dist = 'NA'
        mnt_ra_str = 'NA'
        mnt_dec_str = 'NA'
        mnt_alt = 'NA'
        mnt_az = 'NA'
        zen_dist = 'NA'
        airmass = 'NA'
        equinox = 'NA'
        moon_dist = 'NA'

    header["TRACKING"] = (mount_tracking, "Mount is tracking")

    header["RA-TARG "] = (targ_ra_str, "Requested pointing RA")
    header["DEC-TARG"] = (targ_dec_str, "Requested pointing Dec")

    header["RA-TEL  "] = (mnt_ra_str, "Reported mount pointing RA")
    header["DEC-TEL "] = (mnt_dec_str, "Reported mount pointing Dec")

    header["EQUINOX "] = (equinox, "RA/Dec equinox, years")

    header["TARGDIST"] = (targ_dist, "Distance from target, degrees")

    header["ALT     "] = (mnt_alt, "Mount altitude")
    header["AZ      "] = (mnt_az, "Mount azimuth")

    header["AIRMASS "] = (airmass, "Airmass")

    header["ZENDIST "] = (zen_dist, "Distance from zenith, degrees")

    header["MOONDIST"] = (moon_dist, "Distance from Moon, degrees")

    # Astronomy info
    try:
        if all_info['astro'] is None:
            raise ValueError('No astronomy info provided')

        info = all_info['astro']

        moon_alt = numpy.around(info['moon_alt'], decimals=2)
        moon_ill = numpy.around(info['moon_ill'] * 100., decimals=1)
        moon_phase = info['moon_phase']

        sun_alt = numpy.around(info['sun_alt'], decimals=1)
    except Exception:
        log.error('Failed to write astronomy info to header')
        log.debug('', exc_info=True)
        moon_alt = 'NA'
        moon_ill = 'NA'
        moon_phase = 'NA'
        sun_alt = 'NA'

    header["MOONALT "] = (moon_alt, "Current Moon altitude, degrees")
    header["MOONILL "] = (moon_ill, "Current Moon illumination, percent")
    header["MOONPHAS"] = (moon_phase, "Current Moon phase, [DGB]")
    header["SUNALT  "] = (sun_alt, "Current Sun altitude, degrees")

    # Conditions info
    try:
        if all_info['conditions'] is None:
            raise ValueError('No conditions info provided')

        info = all_info['conditions']

        clouds = info['clouds']
        if clouds == -999:
            clouds = 'NA'
        else:
            clouds = numpy.around(clouds, decimals=1)

        ext_weather = info['weather']['goto']

        ext_temp = ext_weather['temperature']
        if ext_temp == -999:
            ext_temp = 'NA'
        else:
            ext_temp = numpy.around(ext_temp, decimals=1)

        ext_hum = ext_weather['humidity']
        if ext_hum == -999:
            ext_hum = 'NA'
        else:
            ext_hum = numpy.around(ext_hum, decimals=1)

        ext_wind = ext_weather['windspeed']
        if ext_wind == -999:
            ext_wind = 'NA'
        else:
            ext_wind = numpy.around(ext_wind, decimals=1)

        int_weather = info['weather']['pier']

        int_temp = int_weather['int_temperature']
        if int_temp == -999:
            int_temp = 'NA'
        else:
            int_temp = numpy.around(int_temp, decimals=1)

        int_hum = int_weather['int_humidity']
        if int_hum == -999:
            int_hum = 'NA'
        else:
            int_hum = numpy.around(int_hum, decimals=1)

    except Exception:
        log.error('Failed to write conditions info to header')
        log.debug('', exc_info=True)
        clouds = 'NA'
        ext_temp = 'NA'
        ext_hum = 'NA'
        ext_wind = 'NA'
        int_temp = 'NA'
        int_hum = 'NA'

    header["SATCLOUD"] = (clouds, "IR satellite cloud opacity, percent (sat24.com)")

    header["EXT-TEMP"] = (ext_temp, "External temperature, Celsius (GOTO mast)")
    header["EXT-HUM "] = (ext_hum, "External humidity, percent (GOTO mast)")
    header["EXT-WIND"] = (ext_wind, "External wind speed, km/h (GOTO mast)")

    header["INT-TEMP"] = (int_temp, "Internal temperature, Celsius (dome)")
    header["INT-HUM "] = (int_hum, "Internal humidity, percent (dome)")


def write_image_log(filename, header):
    """Add an image log to the database for this frame."""
    filename = filename.split('/')[-1]
    run_number = int(header["RUN     "])
    ut = int(header["UT      "])
    ut_mask = int(header["UTMASK  "])
    start_time = Time(header["DATE-OBS"])
    write_time = Time(header["DATE    "])
    set_position = int(header["SET-POS "])
    set_total = int(header["SET-TOT "])

    expset_id = None
    pointing_id = None
    mpointing_id = None

    if header["DB-EXPS "] != 'NA':
        expset_id = header["DB-EXPS "]
    if header["DB-PNT  "] != 'NA':
        pointing_id = header["DB-PNT  "]
    if header["DB-MPNT "] != 'NA':
        mpointing_id = header["DB-MPNT "]

    log = db.ImageLog(filename=filename, run_number=run_number, ut=ut,
                      ut_mask=ut_mask, start_time=start_time, write_time=write_time,
                      set_position=set_position, set_total=set_total,
                      exposure_set_id=expset_id, pointing_id=pointing_id, mpointing_id=mpointing_id)

    with db.open_session() as session:
        session.add(log)
        session.commit()
