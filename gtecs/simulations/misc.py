"""Miscellaneous functions for simulations."""

from astroplan import Observer

from astropy import units as u
from astropy.coordinates import EarthLocation, SkyCoord, get_sun
from astropy.time import Time

import numpy as np

import obsdb as db

from ..astronomy import observatory_location


def get_sites(site_codes):
    """Get `astropy.coordinates.EarthLocations` for given site codes."""
    sites = []
    for code in site_codes:
        if code.upper() == 'N':
            # Observatorio del Roque de los Muchachos, La Palma
            sites.append(EarthLocation.of_site('lapalma'))
        elif code.upper() == 'S':
            # Siding Spring Observatory, NSW
            sites.append(EarthLocation.of_site('sso'))
        elif code.upper() == 'K':
            # Mt Kent Observatory, Queensland
            sites.append(EarthLocation.from_geodetic(lat=-27.797989, lon=151.855476, height=682))
        else:
            raise ValueError('Invalid site code: "{}"'.format(sites))
    return sites


def prepare_event(event, grid):
    """Make sure an event is prepared."""
    event.get_skymap()
    event.get_strategy()

    if not hasattr(grid, 'skymap') or grid.skymap.object != event.skymap.object:
        grid.apply_skymap(event.skymap)


def get_selected_tiles(event, grid):
    """Get which tiles will be selected by GOTO-alert to add to the ObsDB."""
    if hasattr(event, '_selected_tiles'):
        return event._selected_tiles
    prepare_event(event, grid)

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
    prepare_event(event, grid)

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


def get_dark_tiles(event, grid, time_range=None):
    """Get all the tiles that are far enough from the Sun at the given times."""
    prepare_event(event, grid)

    # Get the location of the Sun at the given times
    sun_start = get_sun(time_range[0])
    sun_end = get_sun(time_range[1])

    # Get tiles that are too close to the Sun to observe
    # Are below the horizon when the Sun sets, or closer
    min_alt = float(event.strategy['constraints_dict']['min_alt'])
    max_sunalt = float(event.strategy['constraints_dict']['max_sunalt'])
    sunny_start_mask = sun_start.separation(grid.coords) < (-1 * max_sunalt + min_alt) * u.deg
    sunny_end_mask = sun_end.separation(grid.coords) < (-1 * max_sunalt + min_alt) * u.deg

    # Assume the Sun doesn't move that much between the given times, i.e. there ~24 hours, not weeks
    sunny_mask = sunny_start_mask & sunny_end_mask
    dark_mask = np.invert(sunny_mask)

    # Get the tile names
    dark_tiles = np.array(grid.tilenames)[dark_mask]

    return dark_tiles


def source_dark(event, grid, start_time, stop_time):
    """Return True if the source is not too close to the sun."""
    # Get the dark and source tiles
    dark_tiles = get_dark_tiles(event, grid, (start_time, stop_time))
    source_tiles = get_source_tiles(event, grid)

    # Is the source far enough from the Sun?
    # If not we're never going to observe it, so might as well end the simulation here
    source_visible = any(tile in dark_tiles for tile in source_tiles)
    return source_visible


def get_visible_tiles(event, grid, time_range=None, sites=None):
    """Get all the tiles that are visible from the given sites within the given times."""
    prepare_event(event, grid)

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


def get_pointing_obs_details(site, pointing_id, event=None):
    """Get details for a specific observed pointing."""
    # Get position, tilename and obs time from the database
    with db.open_session() as session:
        db_pointing = db.get_pointing_by_id(session, pointing_id)
        coord = SkyCoord(db_pointing.ra, db_pointing.dec, unit='deg')
        tilename = db_pointing.grid_tile.name
        if db_pointing.status != 'completed':
            raise ValueError('Pointing {} is not yet completed'.format(pointing_id))
        obs_time = Time(db_pointing.stopped_time)

    # Get the airmass
    observer = Observer(site)
    altaz = observer.altaz(obs_time, coord)
    airmass = altaz.secz.value

    if event is not None:
        # Get how long it had been visible for
        min_alt = event.strategy['constraints_dict']['min_alt']
        rise_time = observer.target_rise_time(obs_time, coord, 'previous', horizon=min_alt * u.deg)
        return tilename, obs_time, airmass, rise_time
    else:
        return tilename, obs_time, airmass
