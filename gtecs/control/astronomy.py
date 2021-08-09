"""Astronomy utilities."""

import datetime
import math
import warnings

from astroplan import Observer
from astroplan.moon import moon_illumination

from astropy import units as u
from astropy._erfa import eo06a
from astropy.coordinates import (AltAz, CIRS, EarthLocation, FK5, GCRS, Longitude, SkyCoord,
                                 get_moon, get_sun)
from astropy.coordinates.builtin_frames.utils import get_jd12
from astropy.time import Time

import numpy as np
from numpy.polynomial.polynomial import polyval

from scipy import interpolate

# from . import astropy_speedups  # noqa: F401 to ignore unused module
from . import params


MAGIC_TIME = Time(-999, format='jd')


def _equation_of_time(time):
    """Find the difference between apparent and mean solar time.

    Parameters
    ----------
    time : `~astropy.time.Time`
        times (array)

    Returns
    ----------
    ret1 : `~astropy.units.Quantity`
        the equation of time

    """
    # Julian centuries since J2000.0
    T = (time - Time("J2000")).to(u.year).value / 100

    # obliquity of ecliptic (Meeus 1998, eq 22.2)
    poly_pars = (84381.448, 46.8150, 0.00059, 0.001813)
    eps = u.Quantity(polyval(T, poly_pars), u.arcsec)
    y = np.tan(eps / 2)**2

    # Sun's mean longitude (Meeus 1998, eq 25.2)
    poly_pars = (280.46646, 36000.76983, 0.0003032)
    L0 = u.Quantity(polyval(T, poly_pars), u.deg)

    # Sun's mean anomaly (Meeus 1998, eq 25.3)
    poly_pars = (357.52911, 35999.05029, 0.0001537)
    M = u.Quantity(polyval(T, poly_pars), u.deg)

    # eccentricity of Earth's orbit (Meeus 1998, eq 25.4)
    poly_pars = (0.016708634, -0.000042037, -0.0000001267)
    e = polyval(T, poly_pars)

    # equation of time, radians (Meeus 1998, eq 28.3)
    eot = (y * np.sin(2 * L0) - 2 * e * np.sin(M) + 4 * e * y * np.sin(M) * np.cos(2 * L0) -
           0.5 * y**2 * np.sin(4 * L0) - 5 * e**2 * np.sin(2 * M) / 4) * u.rad
    return eot.to(u.hourangle)


def _astropy_time_from_lst(time, lst, location, prev_next):
    """Convert a Local Sidereal Time to an astropy Time object.

    The local time is related to the LST through the RA of the Sun.
    This routine uses this relationship to convert a LST to an astropy
    time object.

    Returns
    -------
    ret1 : `~astropy.time.Time`
        time corresponding to LST

    """
    # now we need to figure out time to return from LST
    sun_ra = get_sun(time).ra

    # calculate Greenwich Apparent Solar Time, which we will use as ~UTC for now
    good_mask = ~np.isnan(lst)
    solar_time = lst[good_mask] - sun_ra + 12 * u.hourangle - location.lon

    # assume this is on the same day as supplied time, and fix later
    first_guess = Time(u.d * int(time.mjd) + u.hour * solar_time.wrap_at('360d').hour,
                       format='mjd')

    # Equation of time is difference between GAST and UTC
    eot = _equation_of_time(first_guess)
    first_guess = first_guess - u.hour * eot.value

    if prev_next == 'next':
        # if 'next', we want time to be greater than given time
        mask = first_guess < time
        rise_set_time = first_guess + mask * u.sday
    else:
        # if 'previous', we want time to be less than given time
        mask = first_guess > time
        rise_set_time = first_guess - mask * u.sday

    retvals = -999 * np.ones_like(lst.value)
    retvals[good_mask] = rise_set_time.jd
    return Time(retvals, format='jd')


def _rise_set_trig(time, target, location, prev_next, rise_set):
    """Crude time at next rise/set of ``target`` using spherical trig.

    This method is ~15 times faster than `_calcriseset`,
    and inherently does *not* take the atmosphere into account.

    The time returned should not be used in calculations; the purpose
    of this routine is to supply a guess to `_calcriseset`.

    Parameters
    ----------
    time : `~astropy.time.Time` or other (see below)
        Time of observation. This will be passed in as the first argument to
        the `~astropy.time.Time` initializer, so it can be anything that
        `~astropy.time.Time` will accept (including a `~astropy.time.Time`
        object)

    target : `~astropy.coordinates.SkyCoord`
        Position of target or multiple positions of that target
        at multiple times (if target moves, like the Sun)

    location : `~astropy.coordinates.EarthLocation`
        Observatory location

    prev_next : str - either 'previous' or 'next'
        Test next rise/set or previous rise/set

    rise_set : str - either 'rising' or 'setting'
        Compute prev/next rise or prev/next set

    Returns
    -------
    ret1 : `~astropy.time.Time`
        Time of rise/set

    """
    dec = target.transform_to(GCRS).dec
    cos_ha = -np.tan(dec) * np.tan(location.lat.radian)
    # find the absolute value of the hour angle
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        ha = Longitude(np.fabs(np.arccos(cos_ha)))
    # if rise, hour angle is -ve and vice versa
    if rise_set == 'rising':
        ha = -1 * ha
    # LST = HA + RA
    lst = ha + target.ra

    return _astropy_time_from_lst(time, lst, location, prev_next)


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
        time to check altitude

    Returns
    -------
    above_horizon : bool
        True if the target is above params.MIN_ELEVATION, False if below

    """
    targ_alt, _ = altaz_from_radec(targ_ra, targ_dec, now)
    return targ_alt > params.MIN_ELEVATION


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
        if `alt` is below the horizon then phase is given as 'D',
            regardless of illumination

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
    if alt < params.DARK_MOON_ALT_LIMIT:
        phase = 'D'

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
