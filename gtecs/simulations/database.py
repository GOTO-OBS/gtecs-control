"""Functions to help simulations with the ObsDB."""

import obsdb as db


def prepare_database(grid, clear=False, add_allsky=False, allsky_start_time=None):
    """Prepare a blank database for simulations.

    We need to define the default User, as well as the Grid and GridTiles.

    Parameters
    ----------
    grid : `gototile.grid.SkyGrid`
        The grid to base tiles on.

    clear : bool, optional
        If True, clear out old pointings from the queue.
        Default is False.

    add_allsky : bool, optional
        If True, add pointings for the all-sky survey database.
        Default is False.

    allsky_start_time : `astropy.time.Time`
        Time for the all-sky survey pointings to be valid from.
        Defualt is Time.now()

    """
    with db.open_session() as session:
        # Create the default User if it doesn't exist
        try:
            db_user = db.get_user(session, username='goto')
        except ValueError:
            print('Creating database User')
            db_user = db.User('goto', 'gotoobs', 'GOTO Survey')
            session.add(db_user)

        # Create the Grid and GridTiles if they're not the current grid
        # The "current" grid is defined as the latest one added to the database, that's what
        # GOTO-alert will use.
        # We need to make sure the latest one is the grid that's given, even if that Grid already
        # exists in the database but isn't the "current" one.
        try:
            current_grid = db.get_current_grid(session)
        except ValueError:
            current_grid = None
        if current_grid is None or current_grid.name != grid.name:
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

        if clear:
            # Set any existing Pointings or Mpointings to deleted so they don't interfere
            clear_database(session)

        if add_allsky:
            # Create pointings for the all-sky survey.
            add_allsky_survey(session, db_user, allsky_start_time)

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
    mps = session.query(db.Mpointing).filter(db.Mpointing.status != 'deleted').all()
    if mps:
        # print('Deleting {} previous Mpointings'.format(len(mps)))
        db.bulk_update_status(session, mps, 'deleted')

    # Get all Pointings and set them to deleted
    ps = session.query(db.Pointing).filter(db.Pointing.status != 'deleted').all()
    if ps:
        # print('Deleting {} previous Pointings'.format(len(ps)))
        db.bulk_update_status(session, ps, 'deleted')


def add_allsky_survey(session, db_user, start_time=None):
    """Add Pointings for the all-sky survey to the database.

    Pointings will be added for the 'current' grid, which might have just been created through the
    prepare_database() function.
    """
    # Get the current grid
    db_grid = db.get_current_grid(session)

    # create a Survey
    print('Creating all-sky Survey')
    db_survey = db.Survey(name=db_grid.name)
    db_survey.grid = db_grid
    session.add(db_survey)

    # create SurveyTiles, one for each GridTile
    db_survey_tiles = []
    for db_grid_tile in db_grid.grid_tiles:
        db_survey_tile = db.SurveyTile(weight=1)  # Equal weights
        db_survey_tile.survey = db_survey
        db_survey_tile.grid_tile = db_grid_tile
        db_survey_tiles.append(db_survey_tile)
    db.insert_items(session, db_survey_tiles)

    # create Mpointings
    db_mpointings = []
    print('Creating Mpointings')
    for db_survey_tile in db_survey_tiles:
        name = db_survey_tile.grid_tile.name
        db_mpointing = db.Mpointing(object_name=db_survey.name + '_' + name,
                                    start_rank=999,
                                    num_todo=-1,
                                    start_time=start_time,
                                    wait_time=4320,
                                    min_time=(60 + 30) * 3,
                                    max_sunalt=-12,
                                    user=db_user,
                                    )
        db_mpointing.grid_tile = db_survey_tile.grid_tile
        db_mpointing.survey_tile = db_survey_tile
        db_mpointing.exposure_sets.append(db.ExposureSet(num_exp=3, exptime=60, filt='L'))

        # Create the first Pointing (i.e. preempt the caretaker)
        db_pointing = db_mpointing.get_next_pointing()
        db_mpointing.pointings.append(db_pointing)

        db_mpointings.append(db_mpointing)

    db.insert_items(session, db_mpointings)


def reschedule_pointing(pointing_id, time):
    """Run the caretaker step to make new Pointings from Mpointings."""
    with db.open_session() as session:
        # Get the previous pointing, which should be marked as completed already
        old_pointing = db.get_pointing_by_id(session, pointing_id)

        # We need to fake the stopped_time, otherwise the new Pointing won't be created
        # (it's due to the start_time >= stop_time in Mpointing.get_next_pointing)
        old_pointing.stopped_time = time.to_datetime()
        session.commit()

        # Get the Mpointing
        mpointing = old_pointing.mpointing

        # Create the next pointing, and add it to the database
        new_pointing = mpointing.get_next_pointing()
        if new_pointing is not None:
            session.add(new_pointing)
