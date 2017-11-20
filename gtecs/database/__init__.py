import hashlib

from .engine import load_session, open_session
from .models import (User, Event, EventTile, Survey, SurveyTile,
                     Pointing, Mpointing, Repeat, ExposureSet, ObslogEntry)
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy import or_

from astropy.time import Time
from astropy.coordinates import Longitude
from astropy import units as u
import six


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
    Returns the userKey for a given username.

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
        id of user in database.

    Raises
    ------
    ValueError : if user not in DB
    """
    try:
        return session.query(User.userKey).filter(
            User.userName == userName
        ).one()[0]
    except NoResultFound:
        raise ValueError('User not in db')


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
    ValueError : if user not in DB
    """
    try:
        return session.query(User.userName).filter(
            User.userKey == userKey
        ).one()[0]
    except NoResultFound:
        raise ValueError('User not in db')


def validate_user(session, userName, password):
    """
    Check user exists and password is correct.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session`
    userName : string
        short user name
    password : string
        plain text password. stored in DB using a sha512 hash for security

    Returns
    -------
    ok : bool
        True if user exists and password is ok.

    Raises
    ------
    ValueError : if user is not found in DB.
    """
    password_hash = hashlib.sha512(password.encode()).hexdigest()
    try:
        actual_hash = session.query(User.password).filter(User.userName == userName).one()[0]
    except NoResultFound:
        raise ValueError("user does not exist in db")

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
    if time is None:
        time = Time.now()

    now = time.iso
    pending_queue = session.query(Pointing).filter(
        Pointing.status == 'pending'
    ).filter(Pointing.startUTC < now, now < Pointing.stopUTC)

    # now limit by RA and Dec
    if location is not None:
        # local sidereal time, units of degrees
        lst = time.sidereal_time('mean', location.lon)
        lo_lim = Longitude(lst - hourangle_limit*u.hourangle).deg
        up_lim = Longitude(lst + hourangle_limit*u.hourangle).deg
        if up_lim > lo_lim:
            pending_queue = pending_queue.filter(Pointing.ra < up_lim, Pointing.ra > lo_lim)
        else:
            pending_queue = pending_queue.filter(or_(Pointing.ra < up_lim, Pointing.ra > lo_lim))

        # is latitude ever greater than limit?
        lat = location.lat.deg
        pending_queue = pending_queue.filter(Pointing.decl > lat - 90 + altitude_limit, Pointing.decl < lat + 90 - altitude_limit)

    pending_queue = pending_queue.order_by('rank')

    if rank_limit:
        number_passing_rank = pending_queue.filter(Pointing.rank < rank_limit).count()

        # if there are results which pass the rank limit, filter by rank
        if number_passing_rank > 0:
            pending_queue = pending_queue.filter(Pointing.rank < rank_limit)

    if limit_number:
        pending_queue = pending_queue.limit(limit_number)

    # actually perform queue
    pending_queue = pending_queue.all()
    current_pointing = session.query(Pointing).filter(Pointing.status == 'running').one_or_none()
    return current_pointing, pending_queue


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


def get_pointings(session, pointingIDs=None,
                  status=None):
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
        query = query.filter(
            Pointing.pointingID.in_(list(pointingIDs))
        )
    if status is not None:
        if isinstance(status, six.string_types):
            status = [status]
        query = query.filter(
            Pointing.status.in_(status)
        )
    return query.all()


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
    NoResultFound : if pointingID not in DB
    """
    return session.query(Pointing).filter(
        Pointing.pointingID == pointingID
    ).one_or_none()


def get_mpointings(session, mpointingIDs=None, only_active=True,
                   scheduled=False):
    """
    Get mpointings, filtered by mpointingID or status.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    mpointingIDs : int or list
        supply a ID or list of IDs to filter results
    only_active : bool
        if True, only return `Mpointings` with pointings remaining
    scheduled : bool
        if True, only return `Mpointings` which are scheduled. If False
        only return `Mpointings which are not scheduled. Set to None
        to ignored scheduled status.

    Returns
    -------
    pointings : list
        a list of all matching Pointings
    """
    query = session.query(Mpointing)
    if mpointingIDs is not None:
        query = query.filter(
            Mpointing.mpointingID.in_(list(mpointingIDs))
        )
    if only_active:
        query = query.filter(
            Mpointing.num_remain > 0
        )
    if scheduled is not None:
        query = query.filter(Mpointing.scheduled == scheduled)
    return query.all()


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
    pointing : `Mpointing`
        the matching Mpointing

    Raises
    ------
    NoResultFound : if Mpointing not in DB
    """
    return session.query(Mpointing).filter(
        Mpointing.mpointingID == mpointingID
    ).one_or_none()


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
    NoResultFound : if SurveyTile not in DB
    """

    survey, tile = session.query(Survey, SurveyTile).filter(
        Survey.name == survey_name,
        SurveyTile.name == tile_name
    ).one_or_none()
    return tile


def get_stale_pointing_ids(session, time=None):
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
    staleIDs : list
        a list of all matching Pointing IDs
    """
    if time is None:
        now = Time.now().iso
    else:
        now = time.iso
    query = session.query(Pointing.pointingID).filter(
        Pointing.status == 'pending',
        Pointing.stopUTC < now
    )
    # return values, unpacking tuples
    return [pID for (pID,) in query.all()]


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
    mappings = [dict(pointingID=thisID, status=status) for thisID in pointingIDs]
    session.bulk_update_mappings(Pointing, mappings)


def _make_random_pointing(userKey, numexps=None, time=None):
    """
    make a random pointing for testing.

    all should be observable from la palma at the time of creation. not all will
    be valid in the queue due to start and stop times

    Parameters
    ----------
    numexps : int
        if None, a random number of exposure_sets between 1 and 5 will be added

    time : `~astropy.time.Time`
        The time to centre the pointings around
        if None, the current time is used
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
    p = Pointing(objectName='randObj', ra=np.random.uniform(lst-3, lst+3),
                 decl=np.random.uniform(10, 89), minAlt=30,
                 minTime=np.random.uniform(100, 3600),
                 rank=np.random.randint(1, 100),
                 ToO=np.random.randint(0, 2),
                 maxMoon=np.random.choice(['D', 'G', 'B']),
                 startUTC=t1,
                 stopUTC=t2,
                 userKey=userKey, status='pending', maxSunAlt=-15
                 )

    if numexps is None:
        numexps = np.random.randint(1, 6)
    for i in range(numexps):
        p.exposure_sets.append(
            ExposureSet(
                raoff=0, decoff=0, typeFlag="SCIENCE",
                filt=np.random.choice(['L', 'R', 'G', 'B']),
                expTime=np.random.uniform(10., 360.),
                numexp=1,
                binning=1
            )
        )
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
