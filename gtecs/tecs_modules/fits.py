#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                                fits.py                               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#     G-TeCS module containing functions to write FITS image files     #
#                     Martin Dyer, Sheffield, 2017                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
import numpy
import math
import datetime
import Pyro4
import os
import sys
from astropy.time import Time
from astropy.coordinates import Angle
import astropy.io.fits as pyfits
import astropy.units as u
# TeCS modules
from . import params
from . import misc
from . import astronomy
from .time_date import nightStarting


def image_location(run_number, tel):
    """Construct the image file location based on the run and tel number"""
    # Find the directory, using the date the observing night began
    night = nightStarting()
    direc = params.IMAGE_PATH + night
    if not os.path.exists(direc):
        os.mkdir(direc)

    # Find the file name, using the run number and UT number
    filename = '/r{:07d}_UT{:d}.fits'.format(run_number, tel)

    return direc + filename


def write_fits(image, filename, tel, cam_info):
    """Update an image's FITS header and save to a file"""
    # extract the hdu
    hdu = pyfits.PrimaryHDU(image)

    # update the image header
    update_header(hdu.header, tel, cam_info)

    # recreate the hdulist, and write to file
    hdulist = pyfits.HDUList([hdu])
    if os.path.exists(filename):
        os.remove(filename)
    hdulist.writeto(filename)


def update_header(header, tel, cam_info):
    """Add observation, exposure and hardware info to the FITS header"""

    # These cards are set automatically by AstroPy, we just give them
    # better comments
    header.comments["SIMPLE  "] = "Standard FITS"
    header.comments["BITPIX  "] = "Bits per pixel"
    header.comments["NAXIS   "] = "Number of dimensions"
    header.comments["NAXIS1  "] = "Number of columns"
    header.comments["NAXIS2  "] = "Number of rows"
    header.comments["EXTEND  "] = "Can contain extensions"
    header.comments["BSCALE  "] = "Pixel scale factor"
    header.comments["BZERO   "] = "Real = Pixel * BSCALE + BZERO"


    # Observation info
    run_number = cam_info['run_number']
    run_id = 'r{:07d}'.format(run_number)
    header["RUN     "] = (run_number, "GOTO run number")
    header["RUN-ID  "] = (run_id, "Padded run ID string")

    now = datetime.datetime.utcnow()
    hdu_date = now.strftime("%Y-%m-%dT%H:%M:%S")
    header["DATE    "] = (hdu_date, "Date HDU created")

    header["ORIGIN  "] = (params.ORIGIN, "Origin organisation")
    header["TELESCOP"] = (params.TELESCOP, "Origin telescope")

    intf, HW = params.TEL_DICT[tel]
    current_exposure = cam_info['current_exposure']
    ut_mask = misc.ut_list_to_mask(current_exposure.tel_list)
    ut_string = misc.ut_mask_to_string(ut_mask)
    header["INSTRUME"] = ('UT'+str(tel), "Origin unit telescope")
    header["UT      "] = (tel, "Integer UT number")
    header["UTMASK  "] = (ut_mask, "Run UT mask integer")
    header["UTMASKBN"] = (ut_string, "Run UT mask binary string")
    header["INTERFAC"] = (intf + '-' + str(HW), "System interface code")

    header["SWVN    "] = (params.GTECS_VERSION, "Software version number")

    observer = misc.get_observer()
    header["OBSERVER"] = (observer, "Who started the exposure")
    header["OBJECT  "] = (current_exposure.target, "Observed object name")

    header["SET-POS "] = (current_exposure.set_pos, "Position of this exposure in this set")
    header["SET-TOT "] = (current_exposure.set_total, "Total number of exposures in this set")

    header["SITE-LAT"] = (params.SITE_LATITUDE, "Site latitude, degrees +N")
    header["SITE-LON"] = (params.SITE_LONGITUDE, "Site longitude, degrees +E")
    header["SITE-ALT"] = (params.SITE_ALTITUDE, "Site elevation, m above sea level")
    header["SITE-LOC"] = (params.SITE_LOCATION, "Site location")


    # Exposure data
    header["EXPTIME "] = (current_exposure.exptime, "Exposure time, seconds")

    start_time = Time(cam_info['exposure_start_time'+str(tel)])
    start_time.precision = 0
    mid_time = start_time + (current_exposure.exptime*u.second)/2.
    header["DATE-OBS"] = (start_time.isot, "Exposure start time, UTC")
    header["DATE-MID"] = (mid_time.isot, "Exposure midpoint, UTC")

    mid_jd = mid_time.jd
    header["JD      "] = (mid_jd, "Exposure midpoint, Julian Date")

    lst = astronomy.find_lst(mid_time)
    lst_m, lst_s = divmod(abs(lst)*3600,60)
    lst_h, lst_m = divmod(lst_m,60)
    if lst < 0: lst_h = -lst_h
    mid_lst = '{:02.0f}:{:02.0f}:{:02.0f}'.format(lst_h, lst_m, lst_s)
    header["LST     "] = (mid_lst, "Exposure midpoint, Local Sidereal Time")


    # Frame info
    header["FRMTYPE "] = (current_exposure.frametype, "Frame type (shutter open/closed)")
    header["IMGTYPE "] = (current_exposure.imgtype, "Image type")

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
    from_db = False
    expsetID = 'NA'
    pointingID = 'NA'
    ToO_flag = 'NA'
    rank = 'NA'
    userID = 'NA'
    userName = 'NA'
    mpointingID = 'NA'
    repeatID = 'NA'
    repeatNum = 'NA'
    eventTileID = 'NA'
    eventTileProb = 'NA'
    surveyTileID = 'NA'
    eventID = 'NA'
    eventName = 'NA'
    eventIVO = 'NA'
    eventSource = 'NA'

    expsetID = current_exposure.expID
    if expsetID != 0:
        from_db = True
        from gtecs import database as db
        with db.open_session() as session:
            expset = session.query(db.ExposureSet).filter(
                     db.ExposureSet.expID == expsetID).one_or_none()

            if expset.pointingID:
                pointingID = expset.pointingID
                pointing = session.query(db.Pointing).filter(
                           db.Pointing.pointingID == pointingID).one_or_none()
                rank = pointing.rank
                ToO_flag = bool(pointing.ToO)
                userID = pointing.userKey
                user = session.query(db.User).filter(
                       db.User.userKey == userID).one_or_none()
                userName = user.fullName

                if pointing.mpointingID:
                    mpointingID = expset.mpointingID

                if pointing.repeatID:
                    repeatID = pointing.repeatID
                    repeat = session.query(db.Repeat).filter(
                               db.Repeat.repeatID == repeatID).one_or_none()
                    repeatNum = repeat.repeatNum

                if pointing.eventTileID:
                    eventTileID = pointing.eventTileID
                    eventTile = session.query(db.EventTile).filter(
                               db.EventTile.eventTileID == pointingID).one_or_none()
                    eventTileProb = eventTile.probability

                if pointing.surveyTileID:
                    surveyTileID = pointing.surveyTileID

                if pointing.eventID:
                    eventID = pointing.eventID
                    event = session.query(db.Event).filter(
                               db.Event.eventID == eventID).one_or_none()
                    eventName = event.name
                    eventIVO = event.ivo
                    eventSource = event.source

    header["FROMDB  "] = (from_db, "Exposure linked to database set")
    header["EXPS-ID "] = (expsetID, "Database ExposureSet ID")
    header["PNT-ID  "] = (pointingID, "Database Pointing ID")
    header["TOO     "] = (ToO_flag, "ToO flag for this Pointing")
    header["RANK    "] = (rank, "Rank of this Pointing")
    header["USER-ID "] = (userID, "Database User ID who submitted this Pointing")
    header["USER    "] = (userName, "User who submitted this Pointing")
    header["MPNT-ID "] = (mpointingID, "Database Mpointing ID")
    header["REP-ID  "] = (repeatID, "Database Repeat ID")
    header["REP-N   "] = (repeatNum, "Number of this Repeat")
    header["GW-ID   "] = (eventTileID, "Database Event tile ID")
    header["GW-PROB "] = (eventTileProb, "Event tile contained probability")
    header["SVY-ID  "] = (surveyTileID, "Database Survey tile ID")
    header["EVENT-ID"] = (eventID, "Database Event ID")
    header["EVENT   "] = (eventName, "Event name for this Pointing")
    header["IVO     "] = (eventIVO, "IVOA identifier for this event")
    header["SOURCE  "] = (eventSource, "Source of this event")

    # Camera info
    cam_serial = cam_info['serial_number'+str(tel)]
    header["CAMERA  "] = (cam_serial, "Camera serial number")

    header["XBINNING"] = (current_exposure.binning, "CCD x binning factor")
    header["YBINNING"] = (current_exposure.binning, "CCD y binning factor")

    x_pixel_size = cam_info['x_pixel_size'+str(tel)]*current_exposure.binning
    y_pixel_size = cam_info['y_pixel_size'+str(tel)]*current_exposure.binning
    header["XPIXSZ  "] = (x_pixel_size, "Binned x pixel size, microns")
    header["YPIXSZ  "] = (y_pixel_size, "Binned y pixel size, microns")

    header["CCDTEMP "] = (cam_info['ccd_temp'+str(tel)], "CCD temperature, C")
    header["CCDTEMPS"] = (cam_info['target_temp'+str(tel)], "Requested CCD temperature, C")
    header["BASETEMP"] = (cam_info['base_temp'+str(tel)], "Peltier base temperature, C")


    # Focuser info
    foc = Pyro4.Proxy(params.DAEMONS['foc']['ADDRESS'])
    foc._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = foc.get_info()
        foc_serial = info['serial_number'+str(tel)]
        foc_pos = info['current_pos'+str(tel)]
        foc_temp_int = info['int_temp'+str(tel)]
        foc_temp_ext = info['ext_temp'+str(tel)]
    except:
        foc_serial = 'NA'
        foc_pos = 'NA'
        foc_temp_int = 'NA'
        foc_temp_ext = 'NA'

    header["FOCUSER "] = (foc_serial, "Focuser serial number")
    header["FOCPOS  "] = (foc_pos, "Focuser motor position")
    header["FOCTEMPI"] = (foc_temp_int, "Focuser internal temperature, C")
    header["FOCTEMPX"] = (foc_temp_ext, "Focuser external temperature, C")


    #Filter wheel info
    filt = Pyro4.Proxy(params.DAEMONS['filt']['ADDRESS'])
    filt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = filt.get_info()
        filt_serial = info['serial_number'+str(tel)]
        if info['current_filter_num'+str(tel)] != -1:
            filt_filter_num = info['current_filter_num'+str(tel)]
            filt_filter = params.FILTER_LIST[filt_filter_num]
        else:
            filt_filter = 'UNHOMED'
        filt_num = info['current_filter_num'+str(tel)]
        filt_pos = info['current_pos'+str(tel)]
    except:
        filt_serial = 'NA'
        filt_filter = 'NA'
        filt_num = 'NA'
        filt_pos = 'NA'
    filter_list_str = ''.join(params.FILTER_LIST)

    header["FLTWHEEL"] = (filt_serial, "Filter wheel serial number")
    header["FILTER  "] = (filt_filter, "Filter used for exposure [{}]".format(filter_list_str))
    header["FILTNUM "] = (filt_num, "Filter wheel position number")
    header["FILTPOS "] = (filt_pos, "Filter wheel motor position")

    # Mount info
    mnt = Pyro4.Proxy(params.DAEMONS['mnt']['ADDRESS'])
    mnt._pyroTimeout = params.PROXY_TIMEOUT
    try:
        info = mnt.get_info()
        targ_ra = info['target_ra']
        if targ_ra:
            targ_ra_str = Angle(targ_ra*u.hour).to_string(sep=':', precision=1, alwayssign=True)
        else:
            targ_ra_str = 'NA'

        targ_dec = info['target_dec']
        if targ_dec:
            targ_dec_str = Angle(dec*u.deg).to_string(sep=':', precision=1, alwayssign=True)
        else:
            targ_dec_str = 'NA'

        targ_dist_a = info['target_dist']
        if targ_dist_a:
            targ_dist = numpy.around(targ_dist_a, decimals=1)
        else:
            targ_dist = 'NA'

        mnt_ra = info['mount_ra']
        mnt_ra_str = Angle(mnt_ra*u.hour).to_string(sep=':', precision=1, alwayssign=True)

        mnt_dec = info['mount_dec']
        mnt_dec_str = Angle(mnt_dec*u.deg).to_string(sep=':', precision=1, alwayssign=True)

        mnt_alt = numpy.around(info['mount_alt'], decimals=2)
        mnt_az = numpy.around(info['mount_az'], decimals=2)

        zen_dist = numpy.around(90-mnt_alt, decimals=1)
        airmass = 1/(math.cos(math.pi/2-(mnt_alt*math.pi/180)))
        airmass = numpy.around(airmass, decimals=2)
        equinox = 2000
    except:
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

    header["RA-TARG "] = (targ_ra_str, "Requested pointing RA")
    header["DEC-TARG"] = (targ_dec_str, "Requested pointing Dec")

    header["RA-TEL  "] = (mnt_ra_str, "Reported mount pointing RA")
    header["DEC-TEL "] = (mnt_dec_str, "Reported mount pointing Dec")

    header["EQUINOX "] = (equinox, "RA/Dec equinox, years")

    header["TARGDIST"] = (targ_dist, "Distance from target, degrees")

    header["ALT     "] = (mnt_alt, "Mount altitude")
    header["AZ      "] = (mnt_az, "Mount azimuth")

    header["ZENDIST "] = (zen_dist, "Distance from zenith, degrees")

    header["AIRMASS "] = (airmass, "Airmass")
