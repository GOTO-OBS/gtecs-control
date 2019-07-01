"""Miscellaneous functions for simulations."""

from astroplan import Observer

from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time

import obsdb as db

from ..astronomy import observatory_location


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


def get_selected_tiles(event, grid):
    """Get which tiles will be selected by GOTO-alert to add to the ObsDB."""
    if hasattr(event, '_selected_tiles'):
        return event._selected_tiles

    event.get_skymap()
    event.get_strategy()

    if not hasattr(grid, 'skymap') or grid.skymap.object != event.skymap.object:
        grid.apply_skymap(event.skymap)

    # This matches what GOTO-alert will do - see `gotoalert.database.get_grid_tiles()`
    if grid.tile_area < 20:
        # GOTO-4
        contour_level = 0.9
    else:
        # GOTO-8
        contour_level = 0.95
    table = grid.select_tiles(contour=contour_level,
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

    source_tiles = grid.get_tile(event.source_coord, overlap=True)
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


def get_visible_tiles(event, grid, time_range=None, sites=None):
    """Get all the tiles that are visible from the given sites within the given times."""
    if sites is None:
        sites = observatory_location()
    # Get the visible tiles from the grid for the given times
    min_alt = float(event.strategy['constraints_dict']['min_alt'])
    max_sunalt = float(event.strategy['constraints_dict']['max_sunalt'])
    visible_tiles = grid.get_visible_tiles(sites,
                                           time_range=time_range,
                                           alt_limit=min_alt,
                                           sun_limit=max_sunalt,
                                           )
    return visible_tiles


def source_visible(event, grid, start_time, stop_time, sites=None):
    """Return True if the source is visible between the given times."""
    # Get the visble and source tiles
    visible_tiles = get_visible_tiles(event, grid, (start_time, stop_time), sites)
    source_tiles = get_source_tiles(event, grid)

    # Is the source visible during the night?
    # If not we're never going to observe it, so might as well end the simulation here
    source_visible = any(tile in visible_tiles for tile in source_tiles)
    return source_visible


def source_ever_visible(event, grid, sites=None):
    """Return True if the source is ever visible from the given sites."""
    # Get the visble and source tiles
    ever_visible_tiles = get_visible_tiles(event, grid, None, sites)
    source_tiles = get_source_tiles(event, grid)

    # Is the source visible during the night?
    # If not we're never going to observe it, so might as well end the simulation here
    source_visible = any(tile in ever_visible_tiles for tile in source_tiles)
    return source_visible


def get_source_pointings(event, grid):
    """Get the IDs of the pending pointings for the given tiles."""
    # Get the source tiles
    source_tiles = get_source_tiles(event, grid)

    with db.open_session() as session:
        # Format the object names based on what's added by GOTO-alert
        object_names = [event.name + '_' + tile for tile in source_tiles]

        # Find the pointings in the database
        pointings = session.query(db.Pointing).filter(db.Pointing.object_name.in_(object_names),
                                                      db.Pointing.status == 'pending',
                                                      ).all()

        # Get the pointing IDs
        pointing_ids = [pointing.db_id for pointing in pointings]
        return pointing_ids


def get_pointing_obs_details(event, site, pointing_id):
    """Get details for a specific observed pointing."""
    # Get position, tilename and obs time from the database
    with db.open_session() as session:
        db_pointing = db.get_pointing_by_id(session, pointing_id)
        coord = SkyCoord(db_pointing.ra, db_pointing.dec, unit='deg')
        tilename = db_pointing.grid_tile.name
        if db_pointing.status != 'completed':
            raise ValueError('Pointing {} is not yet completed'.format(pointing_id))
        obs_time = Time(db_pointing.stopped_time)

    # Get how long it had been visible for
    observer = Observer(site)
    min_alt = event.strategy['constraints_dict']['min_alt']
    rise_time = observer.target_rise_time(obs_time, coord, 'previous', horizon=min_alt * u.deg)

    # Get the airmass
    altaz = observer.altaz(obs_time, coord)
    airmass = altaz.secz.value

    return tilename, obs_time, rise_time, airmass
