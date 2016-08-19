# oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo #
#                             astronomy.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#             G-TeCS module containing astronomy utilities             #
#                     Stuart Littlefair, Sheffield, 2016               #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
# oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo #

#  Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
# TeCS modules
from . import params

# astropy/astroplan
from astropy.coordinates import SkyCoord, EarthLocation, AltAz, get_sun
from astropy import units as u
from astropy.time import Time
from astroplan import Observer


def observatory_location():
    """
    Get the observatory location.

    Returns:
    --------
    obs_loc : `~astropy.coordinates.EarthLocation`
    """
    return EarthLocation(lon=params.SITE_LONGITUDE, lat=params.SITE_LATITUDE,
                         height=params.SITE_ALTITUDE)


def altaz(ra_deg, dec_deg, now):
    """
    Calculate Altitude and Azimuth of coordinates.

    Refraction from atmosphere is ignored.

    Parameters
    ----------
    ra_deg : float or numpy.ndarray
        right ascension in degrees
    dec_deg : float or numpy.ndarray
        declination in degrees
    now : `~astropy.time.Time`
        time(s) to calculate Altitude and Azimuth

    Returns
    --------
    alt : float
        altitude in degrees
    az : float
        azimuth in degrees
    """
    loc = observatory_location()
    coo = SkyCoord(ra_deg*u.deg, dec_deg*u.deg)  # ICRS J2000
    altaz_frame = AltAz(obstime=now, location=loc)
    altaz_coo = coo.transform_to(altaz_frame)
    return (altaz_coo.alt.degree, altaz_coo.az.degree)


def sun_alt(now):
    """
    Calculate sun altitude from observatory

    Parameters
    ----------
    now : `~astropy.time.Time`
        time(s) to calculate Altitude

    Returns
    --------
    alt : float or np.ndarray
    """
    sun = get_sun(now)
    loc = observatory_location()
    altaz_frame = AltAz(obstime=now, location=loc)
    altaz_coo = sun.transform_to(altaz_frame)
    return altaz_coo.alt.degree


def twilightLength(date):
    """
    Twilight length for night starting on given date

    Parameters
    ----------
    date : string
        night starting date (YYYY-MM-DD)

    Returns
    -------
    twilength : `astropy.units.Quantity`
        length of astronomical twilight
    """
    noon = Time(date + " 12:00:00")
    observer = Observer(location=observatory_location())
    sun_set_time = observer.sun_set_time(noon, which='next')
    twilight_end = observer.sun_set_time(noon, which='next', horizon=-18*u.deg)
    return (twilight_end - sun_set_time).to(u.min)


@u.quantity_input(sunAlt=u.deg)
def startTime(date, sunAlt, eve=True):
    """
    Find the time when the sun is at sunAlt

    Parameters
    ----------
    date : string
        night starting date (YYYY-MM-DD)
    sunAlt : `astropy.units.Quantity`
        altitude of sun to use
    eve : bool
        True for an evening calculation, false for morning

    Returns
    -------
    goTime : `astropy.time.Time`
        time when sun is at that altitude
    """
    observer = Observer(location=observatory_location())
    if eve:
        start = Time(date + " 12:00:00")
        return observer.sun_set_time(start, which='next', horizon=sunAlt)
    else:
        start = Time(date + " 12:00:00") + u.day
        return observer.sun_rise_time(start, which='previous',
                                      horizon=sunAlt)


def find_ha(ra_hrs, lst):
    """
    Find Hour Angle of given RA.

    Parameters
    -----------
    ra_hrs : float
        J2000 Right Ascension, in hours
    lst : float
        Local Apparent Sidereal Time, hours

    Returns
    -------
    ha_hrs : float
        hour angle, hours
    """
    ha_hrs = lst - ra_hrs
    return ha_hrs


def find_lst(now):
    """
    Return Local Apparent Sidereal Time at observatory.

    Parameters
    ----------
    now: `~astropy.time.Time`
        astropy Time object

    Returns
    --------
    sidereal_time : float
        LAST
    """
    now.location = observatory_location()
    return now.sidereal_time(kind='apparent').hour


def check_alt_limit(targ_ra, targ_dec, now):
    """
    Check if target is above site altitude limit at given time.

    Parameters
    ----------
    targ_ra : float or np.ndarray
        J2000 RA in degrees
    targ_dec : float or np.ndarray
        J2000 Declination in degrees
    now : `~astropy.time.Time`
        time to check altitude

    Returns
    -------
    flag : int
        1 if below altitude limit, 0 if above
    """
    targ_alt, targ_az = altaz(targ_ra, targ_dec, now)
    if targ_alt < params.MIN_ELEVATION:
        return 1
    else:
        return 0


def ang_sep(ra_1, dec_1, ra_2, dec_2):
    """
    Find angular separation between two sky positions.

    Parameters
    ----------
    ra_1 : float or np.ndarray
        RA of coordinate 1, degrees
    dec_1 : float or np.ndarray
        DEC of coordinate 1, degrees
    ra_2 : float or np.ndarray
        RA of coordinate 2, degrees
    dec_2 : float or np.ndarray
        DEC of coordinate 2, degrees

    Returns
    --------
    sep : float or np.ndarray
        angular seperations in degrees
    """
    coo1 = SkyCoord(ra_1*u.deg, dec_1*u.deg)
    coo2 = SkyCoord(ra_2*u.deg, dec_2*u.deg)
    return coo1.separation(coo2).degree


def tel_str(ra, dec):
    """
    Get RA and Dec strings to send to mount

    Parameters
    ----------
    ra : float
        ra in decimal degrees
    dec : float
        declination in decimal degrees
    """
    coo = SkyCoord(ra*u.deg, dec*u.deg)
    ra_string = coo.ra.to_string(sep=' ', precision=2, unit=u.hour)
    dec_string = coo.dec.to_string(sep=' ', precision=1, alwayssign=True)
    dec_string = dec_string[0] + ' ' + dec_string[1:]
    return ra_string, dec_string
