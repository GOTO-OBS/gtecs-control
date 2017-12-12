from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from gtecs import params

# TODO: build name from params

engine_string = 'mysql+pymysql://' + str(params.DATABASE_LOCATION)
engine = create_engine(engine_string, echo=params.DATABASE_ECHO,
                       pool_pre_ping=True) # needs sqlalchemy >= 1.2


@contextmanager
def open_session():
    """
    Create a DB session context manager.

    Automatically takes care of commiting changes
    to DB when scope closes and rolls back on exceptions.

    Needless to say it also closes the session when it goes out of scope.

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
    Session = sessionmaker(bind=engine)
    session = Session()
    return session
