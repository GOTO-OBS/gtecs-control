"""Functions to simulate GOTO hardware."""

from astropy import units as u
from astropy.coordinates import SkyCoord

import obsdb as db


def estimate_completion_time(new_id, current_id, slew_rate=5, readout_time=10):
    """Extimate the exposure time for a new pointing.

    Based on the combined exposure times in all exposures,
    and the time to move into position.

    Parameters
    ----------
    new_id : int
        The new pointing database ID.
    current_id : int or None
        The current pointing database ID, or None if not currently observing.

    slew_rate : float, optional
        The slew rate of the telescope, in deg/second.
        Default is 5 deg/s
    readout_time : float, optional
        The estimated readout time of the cameras, in seconds.
        Default is 10 seconds.

    """
    with db.open_session() as session:
        total_exptime = 0 * u.s
        new_pointing = db.get_pointing_by_id(session, new_id)
        for exp in new_pointing.exposure_sets:
            total_exptime += ((exp.exptime * u.s + readout_time * u.s) * exp.num_exp)

        if current_id is not None:
            current_pointing = db.get_pointing_by_id(session, current_id)
            current_position = SkyCoord(current_pointing.ra,
                                        current_pointing.dec,
                                        unit=u.deg, frame='icrs')
            new_position = SkyCoord(new_pointing.ra,
                                    new_pointing.dec,
                                    unit=u.deg, frame='icrs')
            slew_distance = current_position.separation(new_position)
            slew_rate = slew_rate * u.degree / u.s
            slew_time = slew_distance / slew_rate
        else:
            slew_time = 0 * u.s
    return slew_time + total_exptime
