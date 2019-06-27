"""Miscellaneous functions for simulations."""

from astroplan import AltitudeConstraint, AtNightConstraint, Observer, is_observable

from astropy import units as u
from astropy.coordinates import SkyCoord

import numpy as np

import obsdb as db

from . import params as simparams


def estimate_completion_time(new_id, current_id):
    """Extimate the exposure time for a new pointing.

    Based on the combined exposure times in all exposures,
    and the time to move into position.
    """
    with db.open_session() as session:
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


def get_notvisible_tiles(event, start_time, stop_time):
    """A simple function that should be somewhere better."""

    observer = Observer.at_site('lapalma')

    min_alt = float(event.strategy['constraints_dict']['min_alt']) * u.deg
    max_sunalt = float(event.strategy['constraints_dict']['max_sunalt']) * u.deg
    alt_constraint = AltitudeConstraint(min=min_alt)
    night_constraint = AtNightConstraint(max_solar_altitude=max_sunalt)
    constraints = [alt_constraint, night_constraint]

    tiles_visible_mask = is_observable(constraints, observer, event.grid.coords,
                                       time_range=[start_time, stop_time])
    tiles_notvisible_mask = np.invert(tiles_visible_mask)
    tiles_notvisible = np.array(event.grid.tilenames)[tiles_notvisible_mask]

    return tiles_notvisible
