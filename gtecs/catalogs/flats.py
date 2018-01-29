"""
Flat field catalog from WHT blank field list
"""
import warnings

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.coordinates import AltAz, get_sun

import numpy as np

from .. import astronomy as ast
from .. import params

data = [
    {'Name': 'MAblank1', 'RA2000': '01 00 00', 'DE2000': '+00 07 00', 'bmag': 11.4, 'rmag': 12.3},
    {'Name': 'CAblank1', 'RA2000': '01 47 36', 'DE2000': '+02 20 03', 'bmag': 10.4, 'rmag': 11.6},
    {'Name': 'MAblank2', 'RA2000': '02 15 00', 'DE2000': '-10 00 00', 'bmag': 11.0, 'rmag': 12.3},
    {'Name': 'AAOblank1', 'RA2000': '02 50 04', 'DE2000': '-19 33 49', 'bmag': 8.6, 'rmag': 9.7},
    {'Name': 'wfcblank3', 'RA2000': '02 58 00', 'DE2000': '-00 06 00', 'bmag': 10.2, 'rmag': 11.5},
    {'Name': 'MAblank3', 'RA2000': '04 10 00', 'DE2000': '+03 50 00', 'bmag': 10.7, 'rmag': 11.8},
    {'Name': 'BLANK1', 'RA2000': '04 29 45', 'DE2000': '+54 15 36', 'bmag': 11.5, 'rmag': 12.6},
    {'Name': 'MAblank4', 'RA2000': '05 54 48', 'DE2000': '+02 35 00', 'bmag': 11.6, 'rmag': 12.4},
    {'Name': 'MAblank5', 'RA2000': '05 56 00', 'DE2000': '+04 09 00', 'bmag': 10.8, 'rmag': 10.8},
    {'Name': 'MAblank6', 'RA2000': '07 07 00', 'DE2000': '+01 20 00', 'bmag': 9.3, 'rmag': 10.4},
    {'Name': 'wfsblank2', 'RA2000': '08 01 00', 'DE2000': '+50 00 00', 'bmag': 10.4, 'rmag': 11.5},
    {'Name': 'AAOblank2', 'RA2000': '09 12 00', 'DE2000': '-07 50 47', 'bmag': 9.5, 'rmag': 10.2},
    {'Name': 'CAblank2', 'RA2000': '09 13 49', 'DE2000': '+46 13 58', 'bmag': 10.5, 'rmag': 12.0},
    {'Name': 'AAOblank3', 'RA2000': '10 06 59', 'DE2000': '-02 33 40', 'bmag': 8.4, 'rmag': 9.0},
    {'Name': 'MAblank7', 'RA2000': '10 35 00', 'DE2000': '+03 02 00', 'bmag': 9.9, 'rmag': 12.4},
    {'Name': 'MAblank8', 'RA2000': '10 59 10', 'DE2000': '+03 30 00', 'bmag': 11.1, 'rmag': 13.2},
    {'Name': 'AAOblank4', 'RA2000': '12 28 43', 'DE2000': '-06 55 04', 'bmag':8.8, 'rmag': 10.7},
    {'Name': 'AAOblank5', 'RA2000': '12 30 39', 'DE2000': '-08 03 28', 'bmag': 8.4, 'rmag': 10.4},
    {'Name': 'AAOblank6', 'RA2000': '12 57 33', 'DE2000': '-02 23 16', 'bmag': 10.8, 'rmag': 11.8},
    {'Name': 'BLANK2', 'RA2000': '13 06 56', 'DE2000': '+29 34 48', 'bmag': 8.8, 'rmag': 10.0},
    {'Name': 'CAblank3', 'RA2000': '13 36 18', 'DE2000': '+62 14 11', 'bmag': 9.8, 'rmag': 10.3},
    {'Name': 'CAblank4', 'RA2000': '13 47 42', 'DE2000': '+05 37 36', 'bmag': 9.4, 'rmag': 11.6},
    {'Name': 'MAblank9', 'RA2000': '14 36 10', 'DE2000': '+04 40 00', 'bmag': 10.0, 'rmag': 11.1},
    {'Name': 'wfcblank1', 'RA2000': '15 02 00', 'DE2000': '+29 55 00', 'bmag': 11.0, 'rmag': 11.7},
    {'Name': 'AAOblank7', 'RA2000': '15 15 48', 'DE2000': '-00 42 50', 'bmag': 10.4, 'rmag': 11.9},
    {'Name': 'CAblank5', 'RA2000': '16 24 33', 'DE2000': '+55 43 59', 'bmag': 10.2, 'rmag': 11.7},
    {'Name': 'BLANK3', 'RA2000': '16 50 44', 'DE2000': '-15 22 48', 'bmag': 8.5, 'rmag': 9.2},
    {'Name': 'AAOblank8', 'RA2000': '16 52 33', 'DE2000': '-15 25 57', 'bmag': 12.5, 'rmag': 13.8},
    {'Name': 'wfsblank1', 'RA2000': '17 00 00', 'DE2000': '+41 00 00', 'bmag': 9.6, 'rmag': 10.7},
    {'Name': 'CAblank6', 'RA2000': '17 59 44', 'DE2000': '+66 21 19', 'bmag': 8.4, 'rmag': 10.4},
    {'Name': 'MAblank10', 'RA2000': '18 06 00', 'DE2000': '+00 30 00', 'bmag': 10.0, 'rmag': 11.9},
    {'Name': 'BLANK4', 'RA2000': '19 21 29', 'DE2000': '+12 27 49', 'bmag': 10.2, 'rmag': 10.9},
    {'Name': 'MAblank11', 'RA2000': '19 59 00', 'DE2000': '+02 20 00', 'bmag': 10.1, 'rmag': 11.5},
    {'Name': 'BLANK5', 'RA2000': '21 29 34', 'DE2000': '-08 38 30', 'bmag': 7.7, 'rmag': 10.1},
    {'Name': 'wfcblank2', 'RA2000': '22 58 00', 'DE2000': '+00 05 00', 'bmag': 9.7, 'rmag': 11.6},
    {'Name': 'CAblank7', 'RA2000': '23 15 48', 'DE2000': '+11 26 32', 'bmag': 9.2, 'rmag': 11.5},
    {'Name': 'AAOblank9', 'RA2000': '23 48 20', 'DE2000': '+00 57 21', 'bmag': 9.1, 'rmag': 10.5},
    {'Name': 'BLANK6', 'RA2000': '23 56 40', 'DE2000': '+59 45 00', 'bmag': 7.6, 'rmag': 8.3}
]
ldata = [(datum['Name'], datum['RA2000'], datum['DE2000'], datum['bmag'], datum['rmag']) for datum in data]
flats_table = Table(rows=ldata, names=('name', 'ra', 'decl', 'bmag', 'rmag'))


class FlatField:
    def __init__(self, name, ra, dec, bmag, rmag):
        self.name = name
        self.coord = SkyCoord(ra, dec, unit=(u.hour, u.deg))
        self.bmag = bmag
        self.rmag = rmag
    def __repr__(self):
        return "Flatfield(name={}, ra={}, dec={})".format(
            self.name,
            self.coord.ra.to_string(sep=" ", unit=u.hour),
            self.coord.dec.to_string(sep=" ", unit=u.deg)
        )

def best_flat(time):
    """
    Find the best flat at a given time

    The best flat field is defined as the one nearest zenith

    Parameters
    ----------
    time : `astropy.time.Time`
        the time

    Returns
    -------
    best : `FlatField`
        the best FlatField
    """
    coords = SkyCoord(flats_table['ra'], flats_table['decl'], unit=(u.hour, u.deg))
    alt, az = ast.altaz_from_radec(coords.ra.deg, coords.dec.deg, time)
    row = flats_table[np.argmax(alt)]
    flat_field = FlatField(row['name'], row['ra'], row['decl'], row['bmag'], row['rmag'])
    return flat_field


def antisun_flat(time):
    """
    Get the anti-Sun flat position
    """

    sun = get_sun(time)
    loc = ast.observatory_location()
    altaz_frame = AltAz(obstime=time, location=loc)
    sun_altaz = sun.transform_to(altaz_frame)
    sun_az = sun_altaz.az.degree

    az = sun_az + 180
    if az >= 360:
        az = az - 360
    alt = 75 # fixed
    ra, dec = ast.radec_from_altaz(alt, az, time)

    # format
    c = SkyCoord(ra, dec, unit=(u.deg, u.deg))
    ra = '{:02.0f} {:02.0f} {:02.0f}'.format(*c.ra.hms)
    dec = '{:+02.0f} {:02.0f} {:02.0f}'.format(*c.dec.dms)

    flat_field = FlatField('Anti-Sun', ra, dec, 10, 10)
    return flat_field


def exposure_sequence(date, binning, startExp, nflats=5, eve=True):
    """
    Exposure sequence for well exposed flat fields

    Following the prescription in Tyson & Gal (1993), this routine calculates
    a list of exposure times which should give identical flat field counts

    Parameters
    ----------
    date : str
        night starting date, format YYYY-MM-DD
    binning : int
        amount of on chip binning
    startExp : float
        exposure time of a well exposed flat, in seconds
    nflats : int
        number of flats required
    eve : bool
        True for evening flats, False for morning

    Returns
    -------
    exp_times : list of float
        suggested exposure times
    """
    readout_times = [30, 30, 30]
    try:
        read = readout_times[binning-1]
    except:
        read = readout_times[0]

    tau = ast.twilightLength(date).to(u.min).value

    # time constant from Tyson & Gal (1993).
    if eve:
        a = 10.0**(-7.52/tau/60.0)
    else:
        a = 10.0**(7.52/tau/60)

    t0 = 0.0
    e0 = startExp
    S = (a**e0 - 1.0)/np.log(a)

    eTimes = []
    if eve:
        check = lambda elast : elast < 60.0
        elast = 0.1
    else:
        check = lambda elast: elast > 0.5
        elast = 1000.0

    tlast = t0+read
    for i in range(nflats):
        if not check(elast):
            # have we exceeded exposure limits?
            break
        tnext = np.log(a**(tlast+read) + S*np.log(a)) / np.log(a)
        enext = tnext - (tlast + read)
        eTimes.append(enext)
        elast = enext
        tlast = tnext
    return eTimes


def sky_brightness(sunAlt, filt):
    """
    Sky brightness as a function of sky altitude

    Uses measurements of Patat (2006) for Paranal in UBVRI
    Approximate scalings are made to LRGBC

    Parameters
    ----------
    sunAlt : float
        sun altitude in degrees
    filt : string
        filter
    """
    assert filt.upper() in params.FILTER_LIST
    zenithDist = 90 - sunAlt
    if (zenithDist < 95) or (zenithDist > 105):
        warnings.warn("extrapolating outside valid range for Sun's altitude")
    phi = zenithDist - 95.0

    # now define UBVRI relationships from Patat (2006)
    # each entry gives sB in mags/arcsec**2
    surfaceBrightness = [
        lambda x: 15.01 + 1.376*x - 0.039*x*x,
        lambda x: 11.84 + 1.411*x - 0.041*x*x,
        lambda x: 11.84 + 1.518*x - 0.057*x*x,
        lambda x: 11.40 + 1.567*x - 0.064*x*x,
        lambda x: 10.93 + 1.470*x - 0.062*x*x
    ]

    # TODO: scale these weightings to do better
    if filt.upper() == 'L':
        # approx BVR.
        sb_b = surfaceBrightness[1](phi)
        sb_v = surfaceBrightness[2](phi)
        sb_r = surfaceBrightness[3](phi)
        return (sb_b + sb_v + sb_r)/3.0
    elif filt.upper() == 'B':
        # approx B
        return surfaceBrightness[1](phi)
    elif filt.upper() == 'G':
        # approx V
        return surfaceBrightness[2](phi)
    elif filt.upper() == 'R':
        # approx R
        return surfaceBrightness[3](phi)
    elif filt.upper() == 'C':
        # dunno. G?
        return surfaceBrightness[2](phi)
    else:
        raise ValueError('unknown filter ' + str(filt))


def extrapolate_from_filters(filter1, expT, filter2, time):
    """
    Given a decent exposure time in one filter, guess the correct time in another

    Parameters
    ----------
    filter1 : string
        the name of the first filter (with a good known time)
    expT : float
        exposure time in seconds for this filter
    filter2 : string
        the name of the filter we wish to use next
    time : `astropy.time.Time`
        time to make calculation

    Returns
    -------
    guess : float
        best guess of new exposure time
    """
    sunAlt = ast.sun_alt(time)

    sb1 = sky_brightness(sunAlt, filter1)
    sb2 = sky_brightness(sunAlt, filter2)

    # calculate colour
    colour = sb1 - sb2
    # convert to flux ratio
    flux_ratio = 10.0**(-0.4*colour)

    # assume throughputs are close
    # TODO: replace with proper SN calculator
    return expT * flux_ratio
