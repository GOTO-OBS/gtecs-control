"""
Gliese 1991 catalog of nearby stars
"""
import pkg_resources
import os
import warnings

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.time import Time

import numpy as np

from .. import astronomy as ast
from .. import params

gtecs_data_dir = pkg_resources.resource_filename('gtecs', 'data')
gliese_table_path = os.path.join(gtecs_data_dir, 'Gliese91.fit')
gliese_table = Table.read(gliese_table_path)


class GlieseStar:
    def __init__(self, name, ra, dec, pmra, pmdec, Jmag):
        self.name = str(name).strip()
        self.coord = SkyCoord(ra, dec, unit=(u.deg, u.deg))
        self.pmra = pmra*u.arcsec/u.yr
        self.pmdec = pmdec*u.arcsec/u.yr
        self.Jmag = Jmag

    def __repr__(self):
        coord = self.coord_now()
        return "GlieseStar(name={}, ra={}, dec={}, J={:.1f})".format(
            self.name,
            coord.ra.to_string(sep=" ", unit=u.hour, precision=2),
            coord.dec.to_string(sep=" ", unit=u.deg, precision=1),
            self.Jmag
        )

    def coord_now(self):
        dt = Time.now() - Time("J2000")
        ra = self.coord.ra + self.pmra*dt
        dec = self.coord.dec + self.pmra*dt
        return SkyCoord(ra, dec)

def focus_star(time):
    """
    Find the best Gliese star to observe at a given time

    The best flat field is defined as the one nearest zenith with a JMag near 10.

    Parameters
    ----------
    time : `astropy.time.Time`
        the time

    Returns
    -------
    best : `GlieseStar`
        the best Gliese Star for focusing
    """
    coords = SkyCoord(gliese_table['RAJ2000'], gliese_table['DEJ2000'], unit=(u.hour, u.deg))
    alt, az = ast.altaz_from_radec(coords.ra.deg, coords.dec.deg, time)
    jmag = gliese_table['Jmag']

    # filter on magnitudes
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        mag_mask = np.fabs(jmag-10) < 2

    # filter on moon distance
    moon = ast.get_moon(time)
    moon_dist = coords.separation(moon).degree
    moon_mask = moon_dist > 45

    mask = mag_mask & moon_mask

    row = gliese_table[mask][np.argmax(alt[mask])]
    star = GlieseStar(row['Name'], row['RAJ2000'], row['DEJ2000'],
                      row['pmRA'], row['pmDE'], row['Jmag'])
    return star
