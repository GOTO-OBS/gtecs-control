from .engine import load_session, open_session
from .models import (User, Event, SurveyTile, LigoTile,
                     Pointing, Mpointing, Repeat, Exposure, ObslogEntry)

from astropy.time import Time


def get_queue(session):
    """
    Get the currently valid queue.

    Parameters
    ----------
    session : `sqlalchemy.Session.session`
        a session object - see `load_session` or `open_session`

    Returns
    -------
    current_pointing : `Pointing`
        Pointing being observed now
    pending_pointings : `Pointing`
        all current pending Pointings
    """
    now = Time.now().iso
    pending_queue = session.query(Pointing).filter(
        Pointing.status == 'pending'
    ).filter(Pointing.startUTC < now, now < Pointing.stopUTC).all()
    current_pointing = session.query(Pointing).filter(Pointing.status == 'running').one_or_none()
    return current_pointing, pending_queue


