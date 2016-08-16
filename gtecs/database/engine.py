from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# TODO: build name from params

@contextmanager
def open_session(echo=False):
    """
    Create a DB session context manager.

    Automatically takes care of commiting changes
    to DB when scope closes and rolls back on exceptions.

    Needless to say it also closes the session when it goes out of scope.

    Parameters
    ----------
    echo : bool (default=False)
        if True, session will print SQL statements issued.

    Returns
    -------
    session : `sqlalchemy.orm.session.Session`
        a database session context manager

    Examples
    --------
    To create a new user, and add to the database:

    >>> with open_session() as session:
    >>>     newUser = User('sl', '1234', 'Stuey')
    >>>     session.add(newUser)
    """
    engine = create_engine(
        'mysql+pymysql://goto:gotoobs@localhost/goto_obs',
        echo=echo
        )
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
        session.commit()
    except:
        session.rollback()
        raise
    finally:
        session.close()


def load_session(echo=False):
    """
    Create a DB session.

    By making a DB session this way, you must commit and rollback changes
    yourself.

    Parameters
    ----------
    echo : bool (default=False)
        if True, session will print SQL statements issued.

    Returns
    -------
    session : `sqlalchemy.orm.session.Session`
        a database session

    Examples
    --------
    To create a new user, and add to the database:

    >>> session = load_session()
    >>> newUser = User('sl', '1234', 'Stuey')
    >>> try:
    >>>    session.add(newUser)
    >>> except:
    >>>    session.rollback()
    >>> finally:
    >>>    session.close()
    """
    engine = create_engine(
        'mysql+pymysql://goto:gotoobs@localhost/goto_obs',
        echo=echo
        )
    Session = sessionmaker(bind=engine)
    session = Session()
    return session
