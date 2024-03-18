"""Astronomy utilities."""

from astroplan import Observer
from astroplan.moon import moon_illumination

from astropy import units as u
from astropy.coordinates import AltAz, CIRS, EarthLocation, FK5, HADec, SkyCoord, get_body, get_sun
from astropy.coordinates.builtin_frames.utils import get_jd12
from astropy.time import Time

from erfa import eo06a

# from . import astropy_speedups  # noqa: F401 to ignore unused module
from . import params


MAGIC_TIME = Time(-999, format='jd')


def j2000_to_apparent(ra_deg, dec_deg, jd=None):
    """Find the apparent place for a star at J2000, FK5 coordinates.

    This is equivalent to the 'JNow' coordinates used by SiTech.

    Parameters
    ----------
    ra_deg : float
        J2000, FK5 right ascension in degrees
    dec_deg : float or numpy.ndarray
        J2000, FK5 declination in degrees

    jd: float, default=None
        Julian date to calculate apparent place
        if None, `astropy.time.Time.now().jd` is used

    Returns
    -------
    ra, dec: float
         Apparent RA and Dec of star.

    """
    j2000 = SkyCoord(ra_deg, dec_deg, unit=u.deg, frame='fk5')
    if jd is None:
        time = Time.now().jd
    else:
        time = Time(jd, format='jd')
    cirs = j2000.transform_to(CIRS(obstime=time))
    # find the equation of the origins to transform CIRS to apparent place
    eo = eo06a(*get_jd12(time, 'tt')) * u.rad
    return (cirs.ra - eo).deg, cirs.dec.deg


def apparent_to_j2000(ra_deg, dec_deg, jd):
    """Find the J2000, FK5 coordinates of a star given the apparent place.

    Apparent place is the same as the 'JNow' coordinates used by SiTech.

    Parameters
    ----------
    ra_deg : float
        Apparent right ascension in degrees
    dec_deg : float or numpy.ndarray
        Apparent declination in degrees

    jd: float, default=None
        Julian date to calculate apparent place
        if None, `astropy.time.Time.now().jd` is used

    Returns
    -------
    ra, dec: float
         J2000, FK5 RA and Dec of star.

    """
    if jd is None:
        time = Time.now().jd
    else:
        time = Time(jd, format='jd')
    # find the equation of the origins to transform apparent place to CIRS
    eo = eo06a(*get_jd12(time, 'tt')) * u.rad
    cirs = SkyCoord(ra_deg + eo.to(u.deg).value, dec_deg, unit=u.deg, frame=CIRS(obstime=time))
    j2000 = cirs.transform_to(FK5())
    return j2000.ra.deg, j2000.dec.deg


def observatory_location():
    """Get the observatory location.

    Returns
    -------
    location : `~astropy.coordinates.EarthLocation`

    """
    return EarthLocation(lon=params.SITE_LONGITUDE,
                         lat=params.SITE_LATITUDE,
                         height=params.SITE_ALTITUDE)


def altaz_from_radec(ra_deg, dec_deg, time=None, location=None):
    """Calculate Altitude and Azimuth of coordinates.

    Refraction from atmosphere is ignored.

    Parameters
    ----------
    ra_deg : float or numpy.ndarray
        right ascension in degrees
    dec_deg : float or numpy.ndarray
        declination in degrees

    time : `~astropy.time.Time`, optional
        time to check
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()

    Returns
    -------
    alt_deg : float
        altitude in degrees
    az_deg : float
        azimuth in degrees

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()

    radec_coords = SkyCoord(ra_deg, dec_deg, unit=u.deg)  # ICRS J2000
    altaz_frame = AltAz(obstime=time, location=location)
    altaz_coords = radec_coords.transform_to(altaz_frame)
    return (altaz_coords.alt.degree, altaz_coords.az.degree)


def radec_from_altaz(alt_deg, az_deg, time=None, location=None):
    """Calculate RA and Dec coordinates at a given Altitude and Azimuth.

    Refraction from atmosphere is ignored.

    Parameters
    ----------
    alt_deg : float or numpy.ndarray
        altitude in degrees
    az_deg : float or numpy.ndarray
        azimuth in degrees

    time : `~astropy.time.Time`, optional
        time to check
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()

    Returns
    -------
    ra_deg : float
        right ascension in degrees
    dec_deg : float
        declination in degrees

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()

    altaz_frame = AltAz(az=az_deg * u.deg, alt=alt_deg * u.deg, obstime=time, location=location)
    altaz_coords = SkyCoord(altaz_frame)
    radec_coords = altaz_coords.transform_to('icrs')  # ICRS J2000
    return (radec_coords.ra.degree, radec_coords.dec.degree)


def get_sunalt(time=None, location=None):
    """Calculate the altitude of the Sun at the given time.

    Parameters
    ----------
    time : `~astropy.time.Time`, optional
        time to check
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()

    Returns
    -------
    alt : float or np.ndarray

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()

    sun = get_sun(time)
    altaz_frame = AltAz(obstime=time, location=location)
    altaz_coo = sun.transform_to(altaz_frame)
    return altaz_coo.alt.degree


def get_night_times(time=None, location=None, horizon=-15):
    """Calculate the night start and stop times for a given time.

    If the time is during the night the times for that night are returned.
    If not, the times for the following night are returned.

    Parameters
    ----------
    time : `astropy.time.Time`, optional
        current time
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()
    horizon : float, optional
        horizon below which night is defined
        default is -15 degrees

    Returns
    -------
    sun_set_time, sun_rise_time : 2-tuple of `astropy.time.Time`
        The time the Sun sets and rises for the selected night

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()
    observer = Observer(location=location)

    if observer.is_night(time, horizon=horizon * u.deg):
        # The time is during the night
        sun_set_time = observer.sun_set_time(time, which='previous', horizon=horizon * u.deg)
    else:
        # The time is during the day
        sun_set_time = observer.sun_set_time(time, which='next', horizon=horizon * u.deg)
    sun_rise_time = observer.sun_rise_time(sun_set_time, which='next', horizon=horizon * u.deg)

    return sun_set_time, sun_rise_time


def twilight_length(time=None, location=None, horizon=-15):
    """Twilight length for the given night.

    Parameters
    ----------
    time : `astropy.time.Time`, optional
        current time
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()
    horizon : float, optional
        horizon below which night is defined
        default is -15 degrees

    Returns
    -------
    twilight_length : float
        length of astronomical twilight in minutes

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()
    observer = Observer(location=location)

    if observer.is_night(time, horizon=0 * u.deg):
        # The time is after twilight has started
        twilight_start = observer.sun_set_time(time, which='previous', horizon=0 * u.deg)
    else:
        # The time is during the day
        twilight_start = observer.sun_set_time(time, which='next', horizon=0 * u.deg)
    twilight_end = observer.sun_set_time(twilight_start, which='next', horizon=horizon * u.deg)
    return (twilight_end - twilight_start).to(u.min).value


def local_midnight(time=None, location=None):
    """Find the UT time of local midnight (the nearest midnight to occur).

    Parameters
    ----------
    time : `astropy.time.Time`, optional
        current time
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()

    Returns
    -------
    midnight : `astropy.time.Time`
        time of local midnight in UT

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()
    observer = Observer(location=location)

    midnight = observer.midnight(time, which='nearest')
    return midnight


def night_startdate(time=None, location=None):
    """Return the date at the start of the current astronomical night in format Y-M-D."""
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()

    midnight = local_midnight(time, location)
    midday = midnight - 12 * u.hour
    return midday.strftime('%Y-%m-%d')


@u.quantity_input(sunalt=u.deg)
def sunalt_time(sunalt, eve=True, time=None, location=None):
    """Find the time when the sun is at sunalt.

    Parameters
    ----------
    sunalt : `astropy.units.Quantity`
        altitude of sun to use
    eve : bool
        True for an evening calculation, false for morning

    time : `astropy.time.Time`, optional
        current time
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()

    Returns
    -------
    time : `astropy.time.Time`
        time when sun is at that altitude

    """
    if location is None:
        location = observatory_location()
    observer = Observer(location=location)

    midnight = local_midnight(time, location)
    if eve:
        return observer.sun_set_time(midnight, which='previous', horizon=sunalt)
    else:
        return observer.sun_rise_time(midnight, which='next', horizon=sunalt)


def get_ha(ra_deg, dec_deg, time=None, location=None):
    """Return Hour Angle of given coordinates.

    Parameters
    ----------
    ra_deg : float or numpy.ndarray
        right ascension in degrees
    dec_deg : float or numpy.ndarray
        declination in degrees

    time : `~astropy.time.Time`, optional
        time to check
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()

    Returns
    -------
    ha : float
        hour angle in hours

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()

    radec_coords = SkyCoord(ra_deg, dec_deg, unit=u.deg)
    hadec_frame = HADec(obstime=time, location=location)
    hadec_coords = radec_coords.transform_to(hadec_frame)
    return hadec_coords.ha.hour


def get_lst(time=None, location=None):
    """Return Local Apparent Sidereal Time at the given time.

    Parameters
    ----------
    time : `~astropy.time.Time`, optional
        time to check
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()

    Returns
    -------
    sidereal_time : `~astropy.units.Quantity`
        LAST

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()

    return time.sidereal_time(kind='apparent', longitude=location)


def get_moon_params(time=None):
    """Get the current Moon parameters.

    Parameters
    ----------
    time : `~astropy.time.Time`, optional
        time to get Moon details
        default = Time.now()

    Returns
    -------
    alt : float
        current Moon altitude in degrees
        uses astropy.coordinates.get_body('moon')

    illumination : float
        current fractional Moon illumination
        uses astroplan.moon.moon_illumination()

    phase : str
        current Moon phase, one of 'D', 'G', 'B'
        Dark is illumination below 25%
        Grey is illumination between 25% and 65%
        Bright is illumination above 65%

    """
    if time is None:
        time = Time.now()

    coords = get_body('moon', time)
    alt, _ = altaz_from_radec(coords.ra.deg, coords.dec.deg, time)
    illumination = moon_illumination(time)

    if 0 <= illumination < 0.25:
        phase = 'D'
    elif 0.25 <= illumination < 0.65:
        phase = 'G'
    elif 0.65 <= illumination <= 1.00:
        phase = 'B'

    return alt, illumination, phase


def get_moon_distance(ra_deg, dec_deg, time):
    """Get the angular separation of the given coordinates from the Moon at the given time.

    Parameters
    ----------
    ra_deg : float or numpy.ndarray
        right ascension in degrees
    dec_deg : float or numpy.ndarray
        declination in degrees

    time : `~astropy.time.Time`, optional
        time to get Moon distance
        default = Time.now()

    Returns
    -------
    sep : float or np.ndarray
        angular separation in degrees

    """
    if time is None:
        time = Time.now()

    target = SkyCoord(ra_deg, dec_deg, unit=u.deg)
    moon = get_body('moon', time)

    # NOTE - the order matters
    # moon.separation(target) is NOT the same as target.separation(moon)
    # the former calculates the separation in the frame of the moon coord
    # which is GCRS, and that is what we want.
    # https://github.com/astropy/astroplan/blob/master/astroplan/constraints.py

    return moon.separation(target).degree
