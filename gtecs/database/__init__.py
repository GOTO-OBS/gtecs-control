import hashlib

from .engine import load_session, open_session
from .models import (User, Event, SurveyTile, LigoTile,
                     Pointing, Mpointing, Repeat, Exposure, ObslogEntry)
from sqlalchemy.orm.exc import NoResultFound

from astropy.time import Time
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


def get_queue(session):
    """
    Get the currently valid queue.

    The queue is defined as all pending pointings with a valid date range.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details

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
    >>>     exposure_list = current_job.exposures
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
    now = Time.now().iso
    pending_queue = session.query(Pointing).filter(
        Pointing.status == 'pending'
    ).filter(Pointing.startUTC < now, now < Pointing.stopUTC).all()
    current_pointing = session.query(Pointing).filter(Pointing.status == 'running').one_or_none()
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


def get_mpointings(session, rpIDs=None, only_active=True,
                   scheduled=False):
    """
    Get mpointings, filtered by rpID or status.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    rpIDs : int or list
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
    if rpIDs is not None:
        query = query.filter(
            Mpointing.rpID.in_(list(rpIDs))
        )
    if only_active:
        query = query.filter(
            Mpointing.num_remain > 0
        )
    if scheduled is not None:
        query = query.filter(Mpointing.scheduled == scheduled)
    return query.all()


def get_mpointing_by_id(session, rpID):
    """
    Get a single Mpointing, filtered by ID.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details
    rpID : int
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
        Mpointing.rpID == rpID
    ).one_or_none()


def get_stale_pointing_ids(session):
    """
    Finds all the pointings still pending whose valid period has expired.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session` for details

    Returns
    -------
    staleIDs : list
        a list of all matching Pointing IDs
    """
    query = session.query(Pointing.pointingID).filter(
        Pointing.status == 'pending',
        Pointing.stopUTC < Time.now().iso
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


def bulk_insert_items(session, items):
    """
    Insert a large number of pointings.

    Adding pointings using `insert_items` can be slow for large numbers (>100)
    of pointings. Instead, use this function. Objects are not updated after
    insert, so create new objects to check state.

    Parameters
    -----------
    session : `sqlalchemy.Session.session`
        the session object
    items : list
        A list of model instances, e.g `Pointing`s
    """
    session.bulk_save_objects(items)


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

    Parameters
    ----------
    numexps : int
        if None, a random number of exposures between 1 and 5 will be added

    time : `~astropy.time.Time`
        The time to centre the pointings around
        if None, the current time is used
    """
    import numpy as np
    from astropy import units as u
    if time is None:
        time = Time.now()
    t1 = time + np.random.randint(-12, 24) * u.hour
    t2 = t1 + np.random.randint(1, 10) * u.day
    p = Pointing(objectName='randObj', ra=np.random.uniform(0, 359),
                 decl=np.random.uniform(-89, 89), minAlt=30,
                 minTime=np.random.normal(100, 3600),
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
        p.exposures.append(
            Exposure(
                raoff=0, decoff=0, typeFlag="SCIENCE",
                filt=np.random.choice(['L', 'R', 'G', 'B']),
                expTime=np.random.uniform(10., 360.),
                numexp=1,
                binning=1
            )
        )
    return p
