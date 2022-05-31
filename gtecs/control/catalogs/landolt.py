"""Landolt 2009 catalog of standard stars."""

import importlib.resources as pkg_resources

from astropy import units as u
from astropy.coordinates import AltAz, SkyCoord
from astropy.table import Table
from astropy.time import Time

import numpy as np

import scipy.spatial as sp

from ..astronomy import observatory_location


class LandoltStar(object):
    """A Landolt catalog target."""

    def __init__(self, name, ra, dec, pmra, pmdec, Vmag, BV):
        self.name = str(name).strip()
        self.coord = SkyCoord(ra, dec, unit=(u.hour, u.deg))
        self.pmra = pmra * u.mas / u.yr
        self.pmdec = pmdec * u.mas / u.yr
        self.Vmag = Vmag
        self.BV = BV

    def __repr__(self):
        coord = self.coord_now()
        return "LandoltStar(name={}, ra={}, dec={}, V={:.1f}, B-V={:.1f})".format(
            self.name,
            coord.ra.to_string(sep=" ", unit=u.hour, precision=2),
            coord.dec.to_string(sep=" ", unit=u.deg, precision=1),
            self.Vmag, self.BV
        )

    def coord_now(self):
        """Get coordinates at the current time."""
        dt = Time.now() - Time("J2000")
        ra = self.coord.ra + self.pmra * dt
        dec = self.coord.dec + self.pmra * dt
        return SkyCoord(ra, dec)


def nearest(x, arr):
    """Return index and distance of nearest neighbour to given point.

    Parameters
    ----------
    x :
        point you want to find NN to
    arr :
        (n,k) array of k-dimensional data

    """
    tree = sp.cKDTree(arr)
    return tree.query(x, 1)


def standard_star(airmass, colour, time=None, location=None):
    """Find the standard star nearest in airmass and B-V color to request.

    Parameters
    ----------
    airmass : float
        desired airmass
    colour : float
        desired B-V colour

    time : `astropy.time.Time`
        time to check
        default = Time.now()
    location : `~astropy.coordinates.EarthLocation`, optional
        observatory location
        default = observatory_location()

    Returns
    -------
    std : `LandoltStar`
        the standard star to observe

    """
    if time is None:
        time = Time.now()
    if location is None:
        location = observatory_location()

    with pkg_resources.path('gtecs.control.data', 'Landolt09.fit') as path:
        landolt_table = Table.read(path)

    coords = SkyCoord(landolt_table['RAJ2000'], landolt_table['DEJ2000'], unit=(u.hour, u.deg))
    altaz_frame = AltAz(location=location, obstime=time)
    altaz_coords = coords.transform_to(altaz_frame)
    airmasses = altaz_coords.secz
    colours = landolt_table['B-V']
    mask = np.logical_and(airmasses > 1, airmasses < 4)
    data = np.column_stack((colours[mask], airmasses[mask]))
    goal = [colour, airmass]
    distance, index = nearest(goal, data)
    row = landolt_table[mask][index]
    star = LandoltStar(row['Name'], row['RAJ2000'], row['DEJ2000'],
                       row['pmRA'], row['pmDE'], row['Vmag'], row['B-V'])
    return star
