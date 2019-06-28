"""Miscellaneous functions for simulations."""

from astroplan import AltitudeConstraint, AtNightConstraint, Observer, is_observable

from astropy import units as u
from astropy.coordinates import SkyCoord

import numpy as np

import obsdb as db

from . import params as simparams
from ..astronomy import observatory_location


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


def get_selected_tiles(event, grid):
    """Get which tiles will be selected by GOTO-alert to add to the ObsDB."""
    if hasattr(event, '_selected_tiles'):
        return event._selected_tiles

    event.get_skymap()
    event.get_strategy()

    if not hasattr(grid, 'skymap') or grid.skymap.object != event.skymap.object:
        grid.apply_skymap(event.skymap)

    # This matches what GOTO-alert will do
    table = grid.select_tiles(contour=0.9,
                              max_tiles=event.strategy['tile_limit'],
                              min_tile_prob=event.strategy['prob_limit'],
                              )

    selected_tiles = table['tilename']
    event._selected_tiles = selected_tiles
    return selected_tiles


def get_source_tiles(event, grid):
    """Get which tile(s) the source is contained within."""
    if hasattr(event, '_source_tiles'):
        return event._source_tiles

    event.get_skymap()
    event.get_strategy()

    source = SkyCoord(event.skymap.header['source_ra'],
                      event.skymap.header['source_dec'],
                      unit='deg')

    source_tiles = grid.get_tile(source, overlap=True)
    event._source_tiles = source_tiles
    return source_tiles


def source_selected(event, grid):
    """Return True if the source is within one of the selected tiles."""
    # Get the selected and source tiles
    selected_tiles = get_selected_tiles(event, grid)
    source_tiles = get_source_tiles(event, grid)

    # Is the source in any of the selected ones?
    # If not we're never going to observe it, so might as well end the simulation here
    source_selected = any(tile in selected_tiles for tile in source_tiles)
    return source_selected


def get_visible_tiles(event, grid, start_time, stop_time):
    """Get the tiles that are visible from La Palma within the given time."""
    if hasattr(event, '_visible_tiles'):
        return event._visible_tiles

    # Create the Observer
    observer = Observer(observatory_location())

    # Create the constraints
    min_alt = float(event.strategy['constraints_dict']['min_alt']) * u.deg
    max_sunalt = float(event.strategy['constraints_dict']['max_sunalt']) * u.deg
    alt_constraint = AltitudeConstraint(min=min_alt)
    night_constraint = AtNightConstraint(max_solar_altitude=max_sunalt)
    constraints = [alt_constraint, night_constraint]

    # Find which of the grid tiles will be visible
    mask = is_observable(constraints, observer, grid.coords, time_range=(start_time, stop_time))

    # Return the names of which tiles are visible
    visible_tiles = np.array(grid.tilenames)[mask]
    event._visible_tiles = visible_tiles
    return visible_tiles


def source_visible(event, grid, start_time, stop_time):
    """Return True if the source is visible on the given night."""
    # Get the visble and source tiles
    visible_tiles = get_visible_tiles(event, grid, start_time, stop_time)
    source_tiles = get_source_tiles(event, grid)

    # Is the source visible during the night?
    # If not we're never going to observe it, so might as well end the simulation here
    source_visible = any(tile in visible_tiles for tile in source_tiles)
    return source_visible
