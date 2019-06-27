"""Functions to help simulations with the ObsDB."""

from gototile.grid import SkyGrid

import obsdb as db


def prepare_database(grid=None):
    """Prepare a blank database for simulations.

    We need to define the default User, as well as the Grid and GridTiles.

    This function will also clear any existing Pointings or Mpointings.

    Unlike the obsdb script `add_allsky_survey` this won't add the all-sky pointings.

    Parameters
    ----------
    grid : `gototile.grid.SkyGrid`, optional
        the grid to base tiles on
        default is GOTO-4 (fov=(3.7, 4.9), overlap=(0.1, 0.1))

    """
    with db.open_session() as session:
        # Create the default User if it doesn't exist
        existing_user = session.query(db.User).filter(db.User.username == 'goto').one_or_none()
        if not existing_user:
            print('Creating database User')
            db_user = db.User('goto', 'gotoobs', 'GOTO Survey')
            session.add(db_user)

        # Use the GOTO-4 grid unless another is given
        if grid is None:
            grid = SkyGrid(fov=(3.7, 4.9), overlap=(0.1, 0.1))

        # Create the Grid and GridTiles if they don't exist
        existing_grid = session.query(db.Grid).filter(db.Grid.name == grid.name).one_or_none()
        if not existing_grid:
            print('Creating database Grid')
            db_grid = db.Grid(name=grid.name,
                              ra_fov=grid.fov['ra'].value,
                              dec_fov=grid.fov['dec'].value,
                              ra_overlap=grid.overlap['ra'],
                              dec_overlap=grid.overlap['dec'],
                              algorithm=grid.algorithm,
                              )
            session.add(db_grid)

            print('Creating database GridTiles')
            db_grid_tiles = []
            for coord, name in zip(grid.coords, grid.tilenames):
                db_grid_tile = db.GridTile(name=str(name),
                                           ra=coord.ra.value,
                                           dec=coord.dec.value,
                                           )
                db_grid_tile.grid = db_grid
                db_grid_tiles.append(db_grid_tile)
            db.insert_items(session, db_grid_tiles)

        # Set any existing Pointings or Mpointings to deleted so they don't interfere
        clear_database(session)

        # Commit
        session.commit()


def clear_database(session):
    """Delete all currently valid (m)pointings in the database.

    This ensures a blank slate for the simulations.

    Note it doesn't actually remove the table rows, just set the status to 'deleted'.
    It would be nicer to acutally blank the database, but that gets into complicated
    cascading and so on. This will do for now.
    """
    # Get all Mpointings and set them to deleted
    mps = session.query(db.Mpointing).all()
    if mps:
        print('Deleting {} previous Mpointings'.format(len(mps)))
        db.bulk_update_status(session, mps, 'deleted')

    # Get all Pointings and set them to deleted
    ps = session.query(db.Pointing).all()
    if ps:
        print('Deleting {} previous Pointings'.format(len(ps)))
        db.bulk_update_status(session, ps, 'deleted')
