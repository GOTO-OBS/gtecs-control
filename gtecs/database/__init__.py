import hashlib

from .engine import load_session, open_session
from .models import (User, Event, EventTile, Survey, SurveyTile,
                     Pointing, Mpointing, ObservingBlock, ExposureSet,
                     ImageLog)
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy import or_

from astropy.time import Time
from astropy.coordinates import Longitude
from astropy import units as u


def add_user(session, userName, password, fullName):
    """
    Add a user to the database.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session`
    userName : string
        short user name
    password : string
        plain text password. stored in DB using a sha512 hash for security
    fullName : string
        full name of user
    """
    password_hash = hashlib.sha512(password.encode()).hexdigest()
    new_user = User(userName=userName, password=password_hash, fullName=fullName)
    session.add(new_user)


def get_userkey(session, userName):
    """
    Returns the userKey for a given userName.

    The userKey must be supplied as an argument to create a
    Pointing.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session`
    userName : string
        short name of user

    Returns
    --------
    userKey : int
        id of user in database

    Raises
    ------
    ValueError : if no matching User is found in the database
    """
    query = session.query(User)
    query = query.filter(User.userName == userName)
    user = query.one_or_none()
    if not user:
        raise ValueError('No matching User found')
    return user.userKey


def get_username(session, userKey):
    """
    Returns the userName for a given userKey.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session`
    userKey : int
        id of user in database.

    Returns
    --------
    userName : string
        short name of user

    Raises
    ------
    ValueError : if no matching User is found in the database
    """
    query = session.query(User)
    query = query.filter(User.userKey == userKey)
    user = query.one_or_none()
    if not user:
        raise ValueError('No matching User found')
    return user.userName


def validate_user(session, userName, password):
    """
    Check user exists and password is correct.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session`
    userName : string
        short name of user
    password : string
        plain text password (stored in DB using a sha512 hash for security)

    Returns
    -------
    ok : bool
        True if user exists and password is correct

    Raises
    ------
    ValueError : if userName is not found in DB
    """
    password_hash = hashlib.sha512(password.encode()).hexdigest()

    query = session.query(User)
    query = query.filter(User.userName == userName)
    user = query.one_or_none()
    if not user:
        raise ValueError('No matching User found')

    actual_hash = user.password
    if password_hash == actual_hash:
        return True
    else:
        return False


def get_filtered_queue(session, time=None, rank_limit=None, location=None,
                       altitude_limit=20, hourangle_limit=6, limit_number=None):
    """
    Get the currently valid queue, and filter against visibility and rank.

    The queue is defined as all pending pointings with a valid date range. We then filter
    out tiles that are not visible, order by rank and limit by rank if requested.

    Note that rank limits are designed to not return low rank tiles, but *only* if higher
    ranked tiles exist. If all tiles are below the rank limit, all tiles are returned.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details

    time : `~astropy.time.Time`
        If given, the time to fetch the queue for.
        Defaults to the current time.

    rank_limit: int or None, default None
        Only return pointings with rank greater than given value.
        If all tiles are below the rank limit, all tiles are returned.

    location : `~astropy.coordinates.EarthLocation`
        Location of observatory. If provided, only visible pointings are returned.

    altitude_limit : float
        If filtering by visibility, only pointings which ever rise above this altitude are returned

    hourangle_limit : float
        If filtering by visibility, only pointings with hour angles closer to transit than this
        limit are returned.

    limit_number : int or None
        If not None, limit the number of results.

    Returns
    -------
    current_pointing : `Pointing`
        Pointing being observed now
    pending_pointings : `Pointing`
        all current pending Pointings, after filtering

    Examples
    --------

    An example using `open_session`. The items retrieved won't function outside the
    scope of the with block, since they rely on a session to access the underlying
    database.

    >>> with open_session() as session:
    >>>     current_job, pending_jobs = get_filtered_queue(session, limit_results=100)
    >>>     njobs = len(pending_jobs)
    >>>     exposure_list = current_job.exposure_sets
    >>> current_job
    DetachedInstanceError: Instance <Pointing at 0x10a00ac50> is not bound to a Session; attribute refresh operation cannot proceed

    An example using `load_session`. This will return a session object. With this method
    you don't have to worry so about about scope, but you do have to be careful about
    trapping errors and commiting any changes you make to the database. See the help for
    `load_session` for more details.

    >>> session = load_session()
    >>> current_job, pending_jobs = get_filtered_queue(session, limit_results=100)

    """
    queue = session.query(Pointing)
    queue = queue.filter(Pointing.status == 'pending')

    if time is None:
        time = Time.now()
    now = time.iso

    # are we after the start time?
    queue = queue.filter(Pointing.startUTC < now)
    # are we before the stop time (if any)?
    queue = queue.filter(or_(Pointing.stopUTC > now, Pointing.stopUTC==None))

    # now limit by RA and Dec
    if location is not None:
        # local sidereal time, units of degrees
        lst = time.sidereal_time('mean', location.lon)
        lo_lim = Longitude(lst - hourangle_limit*u.hourangle).deg
        up_lim = Longitude(lst + hourangle_limit*u.hourangle).deg
        if up_lim > lo_lim:
            queue = queue.filter(Pointing.ra < up_lim,
                                 Pointing.ra > lo_lim)
        else:
            queue = queue.filter(or_(Pointing.ra < up_lim,
                                     Pointing.ra > lo_lim))

        # is latitude ever greater than limit?
        lat = location.lat.deg
        queue = queue.filter(Pointing.decl > lat - 90 + altitude_limit,
                             Pointing.decl < lat + 90 - altitude_limit)

    queue = queue.order_by('rank')

    if rank_limit:
        number_passing_rank = queue.filter(Pointing.rank < rank_limit).count()

        # if there are results which pass the rank limit, filter by rank
        if number_passing_rank > 0:
            queue = queue.filter(Pointing.rank < rank_limit)

    if limit_number:
        queue = queue.limit(limit_number)

    # actually get the queue
    pending_pointings = queue.all()

    # find the current pointing too, with a seperate query
    query = session.query(Pointing)
    query = query.filter(Pointing.status == 'running')
    current_pointing = query.one_or_none()

    return current_pointing, pending_pointings


def get_queue(session, time=None):
    """
    Get the currently valid queue.

    The queue is defined as all pending pointings with a valid date range.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details

    time : `~astropy.time.Time`
        If given, the time to fetch the queue for.
        Defaults to the current time.

    Returns
    -------
    current_pointing : `Pointing`
        Pointing being observed now
    pending_pointings : `Pointing`
        all current pending Pointings

    Examples
    --------

    An example using `open_session`. The items retrieved won't function outside the
    scope of the with block, since they rely on a session to access the underlying
    database.

    >>> with open_session() as session:
    >>>     current_job, pending_jobs = get_queue(session)
    >>>     njobs = len(pending_jobs)
    >>>     exposure_list = current_job.exposure_sets
    >>>     sorted_ranks = sorted([job.rank for job in pending_jobs])
    >>> current_job
    DetachedInstanceError: Instance <Pointing at 0x10a00ac50> is not bound to a Session; attribute refresh operation cannot proceed

    An example using `load_session`. This will return a session object. With this method
    you don't have to worry so about about scope, but you do have to be careful about
    trapping errors and commiting any changes you make to the database. See the help for
    `load_session` for more details.

    >>> session = load_session()
    >>> current_job, pending_jobs = get_queue(session)

    """
    # call get_filtered_queue with no filtering
    current_pointing, pending_queue = get_filtered_queue(session, time)
    return current_pointing, pending_queue


def get_pointings(session, pointingIDs=None, status=None):
    """
    Get pointings, filtered by ID or status.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    pointingIDs : int or list
        supply a pointingID or list of IDs to filter by id
    status : string or list
        supply a status or list of statuses to filter by status

    Returns
    -------
    pointings : list
        a list of all matching Pointings
    """
    query = session.query(Pointing)
    if pointingIDs is not None:
        query = query.filter(Pointing.pointingID.in_(list(pointingIDs)))
    if status is not None:
        if isinstance(status, str):
            status = [status]
        query = query.filter(Pointing.status.in_(status))
    pointings = query.all()
    return pointings


def get_pointing_by_id(session, pointingID):
    """
    Get a single Pointing, filtered by ID.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    pointingID : int
        the id number of the pointingID

    Returns
    -------
    pointing : `Pointing`
        the matching Pointing

    Raises
    ------
    ValueError : if no matching Pointing is found in the database
    """
    query = session.query(Pointing)
    query = query.filter(Pointing.pointingID == pointingID)
    pointing = query.one_or_none()
    if not pointing:
        raise ValueError('No matching Pointing found')
    return pointing


def get_mpointings(session, mpointingIDs=None, status=None):
    """
    Get mpointings, filtered by mpointingID or status.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    mpointingIDs : int or list
        supply a ID or list of IDs to filter results
    status : string or list
        supply a status or list of statuses to filter by status

    Returns
    -------
    mpointings : list
        a list of all matching Mpointings
    """
    query = session.query(Mpointing)
    if mpointingIDs is not None:
        query = query.filter(Mpointing.mpointingID.in_(list(mpointingIDs)))
    if status is not None:
        if isinstance(status, str):
            status = [status]
        query = query.filter(Mpointing.status.in_(status))
    mpointings = query.all()
    return mpointings


def get_mpointing_by_id(session, mpointingID):
    """
    Get a single Mpointing, filtered by ID.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    mpointingID : int
        the id number of the Mpointing

    Returns
    -------
    mpointing : `Mpointing`
        the matching Mpointing

    Raises
    ------
    ValueError : if no matching Mpointing is found in the database
    """
    query = session.query(Mpointing)
    query = query.filter(Mpointing.mpointingID == mpointingID)
    mpointing = query.one_or_none()
    if not mpointing:
        raise ValueError('No matching Mpointing found')
    return mpointing


def get_exposure_set_by_id(session, expID):
    """
    Get a single ExposureSet, filtered by ID.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    expID : int
        the id number of the ExposureSet

    Returns
    -------
    exposure_set : `ExposureSet`
        the matching ExposureSet

    Raises
    ------
    ValueError : if no matching ExposureSet is found in the database
    """
    query = session.query(ExposureSet)
    query = query.filter(ExposureSet.expID == expID)
    exposure_set = query.one_or_none()
    if not exposure_set:
        raise ValueError('No matching ExposureSet found')
    return exposure_set


def get_survey_tile_by_name(session, survey_name, tile_name):
    """
    Get a tile in a survey from the name of the survey and tile.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    survey_name : str
        the name of the survey
    tile_name : str
        the name of the tile

    Returns
    -------
    tile : `SurveyTile`
        the matching SurveyTile

    Raises
    ------
    ValueError : if no matching SurveyTile is found in the database
    """
    query = session.query(Survey, SurveyTile)
    query = query.filter(Survey.name == survey_name,
                         SurveyTile.name == tile_name)
    survey, tile = query.one_or_none()
    if not tile:
        raise ValueError('No matching SurveyTile found')
    return tile


def get_expired_pointing_ids(session, time=None):
    """
    Finds all the pointings still pending whose valid period has expired.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    time : `~astropy.time.Time`
        If given, the time to evaluate time period at.
        Defaults to the current time.

    Returns
    -------
    pointingIDs : list
        a list of all matching Pointing IDs
    """
    if time is None:
        now = Time.now().iso
    else:
        now = time.iso

    query = session.query(Pointing.pointingID)
    query = query.filter(Pointing.status == 'pending',
                         Pointing.stopUTC < now)

    # return values, unpacking tuples
    return [pID for (pID,) in query.all()]


def get_expired_mpointing_ids(session, time=None):
    """
    Finds all the mpointings still unscheduled whose valid period has expired.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    time : `~astropy.time.Time`
        If given, the time to evaluate time period at.
        Defaults to the current time.

    Returns
    -------
    mpointingIDs : list
        a list of all matching Mpointing IDs
    """
    if time is None:
        now = Time.now().iso
    else:
        now = time.iso

    query = session.query(Mpointing.mpointingID)
    query = query.filter(Mpointing.status == 'unscheduled',
                         Mpointing.stopUTC < now)

    # return values, unpacking tuples
    return [mpID for (mpID,) in query.all()]


def insert_items(session, items):
    """
    Insert one or more items into the database.

    This routine is slow for large numbers of items,
    use one of the bulk_insert routines instead.

    Parameters
    -----------
    session : `sqlalchemy.Session.session`
        the session object
    items : `gtecs.database.model.Base`
        A model instance, e.g `Pointing`
    """
    items = list(items)
    for i, item in enumerate(items):
        session.add(item)
        if i % 1000 == 0:
            session.flush()


def bulk_update_pointing_status(session, pointingIDs, status):
    """
    Set the status of a large number of pointings.

    Setting the status of a pointing, or updating any DB item, does not
    need this function. One can use:

    >>> with open_session() as session:
    >>>     pointing = get_pointing_by_id(session, 17074)
    >>>     pointing.status = 'completed'

    However, for large numbers of pointings this is inefficient. Use this
    routine instead.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        the session object
    pointingIDs : list
        a list of pointingIDs to update
    status : string
        status to set pointings to
    """
    mappings = [dict(pointingID=pID, status=status) for pID in pointingIDs]
    session.bulk_update_mappings(Pointing, mappings)


def bulk_update_mpointing_status(session, mpointingIDs, status):
    """
    Set the status of a large number of mpointings.

    Setting the status of an mpointing, or updating any DB item, does not
    need this function. One can use:

    >>> with open_session() as session:
    >>>     mpointing = get_mpointing_by_id(session, 17074)
    >>>     mpointing.status = 'completed'

    However, for large numbers of mpointings this is inefficient. Use this
    routine instead.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        the session object
    mpointingIDs : list
        a list of mpointingIDs to update
    status : string
        status to set mpointings to
    """
    mappings = [dict(mpointingID=mpID, status=status) for mpID in mpointingIDs]
    session.bulk_update_mappings(Mpointing, mappings)


def _make_random_pointing(userKey, numexps=None, time=None):
    """
    Make a random pointing for testing.

    It should be observable from La Palma at the time of creation.
    However not all pointings will be valid in the queue immedietly due to
    random start and stop times.

    Parameters
    ----------
    numexps : int
        if None, a random number of exposure_sets between 1 and 5 will be added

    time : `~astropy.time.Time`
        The time to centre the pointings around
        if None, the current time is used

    Returns
    -------
    pointing : `Pointing`
        the new Pointing
    """
    import numpy as np
    from astropy.coordinates import EarthLocation
    from astropy import units as u

    lapalma = EarthLocation(lat=28*u.deg, lon=-17*u.deg)
    if time is None:
        time = Time.now()
    # LST in degrees
    lst = time.sidereal_time('mean', longitude=lapalma.lon).deg
    t1 = time + np.random.randint(-5, 2) * u.day
    t2 = t1 + np.random.randint(1, 10) * u.day
    p = Pointing(objectName='randObj',
                 ra=np.random.uniform(lst-3, lst+3),
                 decl=np.random.uniform(10, 89),
                 rank=np.random.randint(1, 100),
                 minAlt=30,
                 maxSunAlt=-15,
                 minTime=np.random.uniform(100, 3600),
                 maxMoon=np.random.choice(['D', 'G', 'B']),
                 minMoonSep=30,
                 ToO=np.random.randint(0, 2),
                 startUTC=t1,
                 stopUTC=t2,
                 status='pending',
                 userKey=userKey,
                 )

    if numexps is None:
        numexps = np.random.randint(1, 6)
    for i in range(numexps):
        exposure_set = ExposureSet(expTime=np.random.uniform(10., 360.),
                                   numexp=1,
                                   typeFlag="SCIENCE",
                                   filt=np.random.choice(['L', 'R', 'G', 'B']),
                                   binning=1,
                                   raoff=0,
                                   decoff=0,
                                   )
        p.exposure_sets.append(exposure_set)

    return p


def markJobCompleted(pID):
    with open_session() as s:
        pointing = get_pointing_by_id(s, pID)
        pointing.status = 'completed'


def markJobAborted(pID):
    with open_session() as s:
        pointing = get_pointing_by_id(s, pID)
        pointing.status = 'aborted'


def markJobInterrupted(pID):
    with open_session() as s:
        pointing = get_pointing_by_id(s, pID)
        pointing.status = 'interrupted'


def markJobRunning(pID):
    with open_session() as s:
        pointing = get_pointing_by_id(s, pID)
        pointing.status = 'running'
