"""
Landolt 2009 catalog of standard stars
"""
import pkg_resources
import os
import warnings

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table
from astropy.time import Time

import numpy as np
import scipy.spatial as sp

from .. import astronomy as ast

with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    gtecs_data_dir = pkg_resources.resource_filename('gtecs', 'data')
    landolt_table_path = os.path.join(gtecs_data_dir, 'Landolt09.fit')
    landolt_table = Table.read(landolt_table_path)


class LandoltStar:
    def __init__(self, name, ra, dec, pmra, pmdec, Vmag, BV):
        self.name = str(name).strip()
        self.coord = SkyCoord(ra, dec, unit=(u.hour, u.deg))
        self.pmra = pmra*u.mas/u.yr
        self.pmdec = pmdec*u.mas/u.yr
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
        dt = Time.now() - Time("J2000")
        ra = self.coord.ra + self.pmra*dt
        dec = self.coord.dec + self.pmra*dt
        return SkyCoord(ra, dec)


def nearest(x, arr):
    # returns index and distance of nearest neighbour to given point
    # inputs:
    #    x:        point you want to find NN to
    #  arr:       (n,k) array of k-dimensional data
    tree = sp.cKDTree(arr)
    return tree.query(x, 1)


def standard_star(time, airmass, colour):
    """
    Find the standard star nearest in airmass and B-V color to request

    Parameters
    ----------
    time : `astropy.time.Time`
        the time
    airmass : float
        desired airmass
    colour : float
        desired B-V colour

    Returns
    -------
    std : `LandoltStar`
        the standard star to observe
    """
    coords = SkyCoord(landolt_table['RAJ2000'], landolt_table['DEJ2000'], unit=(u.hour, u.deg))
    observer = ast.observatory_location()
    altaz_frame = ast.AltAz(location=observer, obstime=time)
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
