"""Astronomy utilities."""

import datetime
import math

from astroplan import Observer
from astroplan.moon import moon_illumination

from astropy import units as u
from astropy.coordinates import AltAz, CIRS, EarthLocation, FK5, SkyCoord, get_moon, get_sun
from astropy.coordinates.builtin_frames.utils import get_jd12
from astropy.time import Time

from erfa import eo06a

import numpy as np

from scipy import interpolate

# from . import astropy_speedups  # noqa: F401 to ignore unused module
from . import params


MAGIC_TIME = Time(-999, format='jd')


def j2000_to_apparent(ra, dec, jd=None):
    """Find the apparent place for a star at J2000, FK5 coordinates.

    This is equivalent to the 'JNow' coordinates used by SiTech.

    Arguments
    -----------
    ra, dec: float
        J2000, FK5 coordinates of star in decimal degrees

    jd: float, default=None
        Julian date to calculate apparent place
        if None, `astropy.time.Time.now().jd` is used

    Returns
    --------
    ra, dec: float
         Apparent RA and Dec of star.

    """
    j2000 = SkyCoord(ra, dec, unit=u.deg, frame='fk5')
    if jd is None:
        now = Time.now().jd
    else:
        now = Time(jd, format='jd')
    cirs = j2000.transform_to(CIRS(obstime=now))
    # find the equation of the origins to transform CIRS to apparent place
    eo = eo06a(*get_jd12(now, 'tt')) * u.rad
    return (cirs.ra - eo).deg, cirs.dec.deg


def apparent_to_j2000(ra, dec, jd):
    """Find the J2000, FK5 coordinates of a star given the apparent place.

    Apparent place is the same as the 'JNow' coordinates used by SiTech.

    Arguments
    -----------
    ra, dec: float
        Apparent RA and Dec of star in decimal degrees

    jd: float, default=None
        Julian date to calculate apparent place
        if None, `astropy.time.Time.now().jd` is used

    Returns
    --------
    ra, dec: float
         J2000, FK5 RA and Dec of star.

    """
    if jd is None:
        now = Time.now().jd
    else:
        now = Time(jd, format='jd')
    # find the equation of the origins to transform apparent place to CIRS
    eo = eo06a(*get_jd12(now, 'tt')) * u.rad
    cirs = SkyCoord(ra + eo.to(u.deg).value, dec, unit=u.deg,
                    frame=CIRS(obstime=now))
    j2000 = cirs.transform_to(FK5())
    return j2000.ra.deg, j2000.dec.deg


def observatory_location():
    """Get the observatory location.

    Returns:
    --------
    obs_loc : `~astropy.coordinates.EarthLocation`

    """
    return EarthLocation(lon=params.SITE_LONGITUDE,
                         lat=params.SITE_LATITUDE,
                         height=params.SITE_ALTITUDE)


def get_horizon(filepath=None):
    """Get the artificial horizon of the observatory.

    The horizon should be defined in file in the G-TeCS config directory, with columns matching
    the azimuth and altitude limit at that azimuth.

    Note you will need to interpolate between alts (e.g. with `scipy.interpolate.interp1d`) to
    find the horizon at any intermediate points.

    Parameters
    ----------
    filepath : str, default=params.HORIZON_FILE
        horizon file to use

    Returns:
    --------
    az, alt : tuple of list
        altitude limit at defined azimuths

    """
    if filepath is None:
        filepath = params.HORIZON_FILE
    az, alt = np.loadtxt(filepath, usecols=(0, 1)).T
    return (az, alt)


def above_horizon(ra_deg, dec_deg, now=None, horizon=30):
    """Check if the given coordinates are above the artificial horizon.

    Parameters
    ----------
    ra_deg : float or numpy.ndarray
        right ascension in degrees
    dec_deg : float or numpy.ndarray
        declination in degrees
    now : `~astropy.time.Time`, optional
        time(s) to calculate at
        default is `Time.now()`
    horizon : float or tuple of (azs, alts), optional
        artificial horizon, either a flat value or varying with azimuth.
        default is a flat horizon of 30 deg
    """
    alt, az = altaz_from_radec(ra_deg, dec_deg, now)

    if isinstance(horizon, (int, float)):
        horizon = ([0, 90, 180, 270, 360], [horizon, horizon, horizon, horizon, horizon])
    get_alt_limit = interpolate.interp1d(*horizon,
                                         bounds_error=False,
                                         fill_value='extrapolate')
    alt_limit = get_alt_limit(az)
    return alt > alt_limit


def altaz_from_radec(ra_deg, dec_deg, now=None):
    """Calculate Altitude and Azimuth of coordinates.

    Refraction from atmosphere is ignored.

    Parameters
    ----------
    ra_deg : float or numpy.ndarray
        right ascension in degrees
    dec_deg : float or numpy.ndarray
        declination in degrees
    now : `~astropy.time.Time`, optional
        time(s) to calculate at
        default is `Time.now()`

    Returns
    --------
    alt_deg : float
        altitude in degrees
    az_deg : float
        azimuth in degrees

    """
    if now is None:
        now = Time.now()
    loc = observatory_location()
    radec_coo = SkyCoord(ra_deg * u.deg, dec_deg * u.deg)  # ICRS J2000
    altaz_frame = AltAz(obstime=now, location=loc)
    altaz_coo = radec_coo.transform_to(altaz_frame)
    return (altaz_coo.alt.degree, altaz_coo.az.degree)


def radec_from_altaz(alt_deg, az_deg, now=None):
    """Calculate RA and Dec coordinates at a given Altitude and Azimuth.

    Refraction from atmosphere is ignored.

    Parameters
    ----------
    alt_deg : float or numpy.ndarray
        altitude in degrees
    az_deg : float or numpy.ndarray
        azimuth in degrees
    now : `~astropy.time.Time`, optional
        time(s) to calculate at
        default is `Time.now()`

    Returns
    --------
    ra_deg : float
        right ascension in degrees
    dec_deg : float
        declination in degrees

    """
    if now is None:
        now = Time.now()
    loc = observatory_location()
    altaz = AltAz(az=az_deg * u.deg, alt=alt_deg * u.deg, obstime=now, location=loc)
    altaz_coo = SkyCoord(altaz)
    radec_frame = 'icrs'  # ICRS J2000
    radec_coo = altaz_coo.transform_to(radec_frame)
    return (radec_coo.ra.degree, radec_coo.dec.degree)


def get_sunalt(now):
    """Calculate sun altitude from observatory.

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


@u.quantity_input(horizon=u.deg)
def get_night_times(time, site=None, horizon=-15 * u.deg):
    """Calculate the night start and stop times for a given time.

    If the time is during the night the times for that night are returned.
    If not, the times for the following night are returned.

    Parameters
    ----------
    time : `astropy.time.Time`
        night starting date
    site : `astropy.coordinates.EarthLocation`
        the site to consider
        Default uses observatory_location() (defaults to La Palma)
    horizon : float, optional
        horizon below which night is defined
        default is -15 degrees

    Returns
    -------
    sun_set_time, sun_rise_time : 2-tuple of `astropy.time.Time`
        The time the Sun sets and rises for the selected night

    """
    if site is None:
        site = observatory_location()

    observer = Observer(location=site)

    if observer.is_night(time, horizon=horizon):
        # The time is during the night
        sun_set_time = observer.sun_set_time(time, which='previous', horizon=horizon)
    else:
        # The time is during the day
        sun_set_time = observer.sun_set_time(time, which='next', horizon=horizon)
    sun_rise_time = observer.sun_rise_time(sun_set_time, which='next', horizon=horizon)

    return sun_set_time, sun_rise_time


def twilight_length(date):
    """Twilight length for night starting on given date.

    Parameters
    ----------
    date : string
        night starting date (YYYY-MM-DD)

    Returns
    -------
    twilength : `astropy.units.Quantity`
        length of astronomical twilight

    """
    noon = Time(date + ' 12:00:00')
    observer = Observer(location=observatory_location())
    sun_set_time = observer.sun_set_time(noon, which='next')
    twilight_end = observer.sun_set_time(noon, which='next', horizon=-18 * u.deg)
    return (twilight_end - sun_set_time).to(u.min)


def local_midnight(date):
    """Find the UT time of local midnight.

    Parameters
    ----------
    date : string
        night starting date (YYYY-MM-DD)

    Returns
    -------
    midnight : `astropy.time.Time`
        time of local midnight in UT

    """
    noon = Time(date + ' 12:00:00')
    observer = Observer(location=observatory_location())
    return observer.midnight(noon, 'next')


def night_startdate():
    """Return the date at the start of the current astronomical night in format Y-M-D."""
    now = datetime.datetime.utcnow()
    if now.hour < 12:
        now = now - datetime.timedelta(days=1)
    return now.strftime('%Y-%m-%d')


@u.quantity_input(sunalt=u.deg)
def sunalt_time(date, sunalt, eve=True):
    """Find the time when the sun is at sunalt.

    Parameters
    ----------
    date : string
        night starting date (YYYY-MM-DD)
    sunalt : `astropy.units.Quantity`
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
        start = Time(date + ' 12:00:00')
        return observer.sun_set_time(start, which='next', horizon=sunalt)
    else:
        start = Time(date + ' 12:00:00') + 1 * u.day
        return observer.sun_rise_time(start, which='previous', horizon=sunalt)


def airmass(alt):
    """Calculate airmass at a given altitude.

    Parameters
    ----------
    alt : float
        altitude

    Returns
    -------
    airmass : float
        airmass at that altitude

    """
    return 1 / math.cos((math.pi) / 2) - alt


def get_ha(ra_hrs, lst):
    """Return Hour Angle of given RA.

    Parameters
    ----------
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
    if ha_hrs < -12:
        ha_hrs += 24
    return ha_hrs


def get_lst(now):
    """Return Local Apparent Sidereal Time at observatory.

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
    return now.sidereal_time(kind='apparent')


def above_elevation_limit(targ_ra, targ_dec, now):
    """Check if target is above the mount elevation limit at the given time.

    This is not to be confused with the artificial horizon used when scheduling targets.

    Parameters
    ----------
    targ_ra : float or np.ndarray
        J2000 RA in degrees
    targ_dec : float or np.ndarray
        J2000 Declination in degrees
    now : `~astropy.time.Time`
        time to check

    Returns
    -------
    above_horizon : bool
        True if the target is above params.MIN_ELEVATION, False if below

    """
    targ_alt, _ = altaz_from_radec(targ_ra, targ_dec, now)
    return targ_alt > params.MIN_ELEVATION


def within_hourangle_limit(targ_ra, now):
    """Check if target is within the mount hour angle limit at the given time.

    Parameters
    ----------
    targ_ra : float or np.ndarray
        J2000 RA in degrees
    now : `~astropy.time.Time`
        time to check

    Returns
    -------
    within_limit : bool
        True if the target is within |params.MAX_HOURANGLE| of zenith, False if outside

    """
    lst = get_lst(now).hour
    ha = get_ha(targ_ra * 12 / 180, lst)
    return abs(ha) < params.MAX_HOURANGLE


def within_mount_limits(targ_ra, targ_dec, now):
    """Check if target is within the mount limits (altitude and hour angle) at the given time.

    Parameters
    ----------
    targ_ra : float or np.ndarray
        J2000 RA in degrees
    targ_dec : float or np.ndarray
        J2000 Declination in degrees
    now : `~astropy.time.Time`
        time to check

    Returns
    -------
    within_limits : bool
        True if the target is within the mount limits, False if outside

    """
    return above_elevation_limit(targ_ra, targ_dec, now) and within_hourangle_limit(targ_ra, now)


def ang_sep(ra_1, dec_1, ra_2, dec_2):
    """Find angular separation between two sky positions.

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
    coo1 = SkyCoord(ra_1 * u.deg, dec_1 * u.deg)
    coo2 = SkyCoord(ra_2 * u.deg, dec_2 * u.deg)
    return coo1.separation(coo2).degree


def mnt_str(ra, dec):
    """Get RA and Dec strings to send to mount.

    Parameters
    ----------
    ra : float
        ra in decimal degrees
    dec : float
        declination in decimal degrees

    """
    coo = SkyCoord(ra * u.deg, dec * u.deg)
    ra_string = coo.ra.to_string(sep=' ', precision=2, unit=u.hour)
    dec_string = coo.dec.to_string(sep=' ', precision=1, alwayssign=True)
    dec_string = dec_string[0] + ' ' + dec_string[1:]
    return ra_string, dec_string


def get_moon_params(now):
    """Get the current Moon parameters.

    Parameters
    ----------
    now : `~astropy.time.Time`
        time to get Moon details

    Returns
    -------
    alt : float
        current Moon altitude in degrees
        uses astropy.coordinates.get_moon()

    illumination : float
        current fractional Moon illumination
        uses astroplan.moon.moon_illumination()

    phase : str
        current Moon phase, one of 'D', 'G', 'B'
        Dark is illumination below 25%
        Grey is illumination between 25% and 65%
        Bright is illumination above 65%

    """
    coords = get_moon(now)
    alt, az = altaz_from_radec(coords.ra.degree, coords.dec.degree, now)
    illumination = moon_illumination(now)

    if 0 <= illumination < 0.25:
        phase = 'D'
    elif 0.25 <= illumination < 0.65:
        phase = 'G'
    elif 0.65 <= illumination <= 1.00:
        phase = 'B'

    return alt, illumination, phase


def get_moon_distance(ra, dec, now):
    """Get the angular seperation of the given coordinates from the Moon at the given time.

    Parameters
    ----------
    ra : float or np.ndarray
        J2000 RA in degrees
    dec : float or np.ndarray
        J2000 Declination in degrees
    now : `~astropy.time.Time`
        time to check Moon position

    Returns
    -------
    sep : float or np.ndarray
        angular seperations in degrees

    """
    target = SkyCoord(ra * u.deg, dec * u.deg)
    moon = get_moon(now)

    # NOTE - the order matters
    # moon.separation(target) is NOT the same as target.separation(moon)
    # the former calculates the separation in the frame of the moon coord
    # which is GCRS, and that is what we want.
    # https://github.com/astropy/astroplan/blob/master/astroplan/constraints.py

    return moon.separation(target).degree
