"""Miscellaneous functions for simulations."""

import astroplan

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time, TimeDelta

import obsdb as db

from . import params as simparams


def set_pointing_status(db_id, status, session):
    """Mark a pointing as completed, aborted etc."""
    if status not in ['aborted', 'completed', 'interrupted', 'running']:
        print('Illegal status:', status)
        return 1
    else:
        print('    Marking pointing', db_id, 'as', status)
        try:
            pointing = db.get_pointing_by_id(session, db_id)
            pointing.status = status
            session.commit()
            return 0
        except Exception:
            session.rollback()
            print('Session error!!')
            return 1


def get_night_times(date):
    """Calculate the start and stop times of a given date.

    Defined as sunrise and sunset times for La Palma.
    """
    lapalma = astroplan.Observer.at_site('lapalma')
    # Time(date) gives start of date, add one day to get midnight that night
    midnight = Time(date) + TimeDelta(1 * u.day)
    sunset = lapalma.sun_set_time(midnight, which="previous", horizon=-8 * u.deg)
    sunrise = lapalma.sun_rise_time(midnight, which="next", horizon=-8 * u.deg)
    return sunset, sunrise


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
