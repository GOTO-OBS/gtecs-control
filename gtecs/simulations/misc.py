"""Miscellaneous functions for simulations."""

from astropy import units as u
from astropy.coordinates import SkyCoord

import obsdb as db

from . import params as simparams


def estimate_completion_time(new_id, current_id, session):
    """Extimate the exposure time for a new pointing.

    Based on the combined exposure times in all exposures,
    and the time to move into position.
    """
    total_exptime = 0 * u.s
    new_pointing = db.get_pointing_by_id(session, new_id)
    for exp in new_pointing.exposure_sets:
        total_exptime += ((exp.exptime * u.s + simparams.READOUT_TIME) * exp.num_exp)

    if current_id is not None:
        current_pointing = db.get_pointing_by_id(session, current_id)
        current_position = SkyCoord(current_pointing.ra,
                                    current_pointing.dec,
                                    unit=u.deg, frame='icrs')
        new_position = SkyCoord(new_pointing.ra,
                                new_pointing.dec,
                                unit=u.deg, frame='icrs')
        slew_distance = current_position.separation(new_position)
        slew_time = slew_distance / simparams.SLEWRATE
    else:
        slew_time = 0 * u.s
    return slew_time + total_exptime
