from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


# TODO: build name from params
def loadSession(echo=False):
    """
    Create a DB session.

    Parameters
    ----------
    echo : bool (default=False)
        if True, session will print SQL statements issued.

    Returns
    -------
    session : `sqlalchemy.orm.session.Session`
        a database session
    """
    engine = create_engine(
        'mysql+pymysql://goto:gotoobs@localhost/goto_obs',
        echo=echo
        )
    Session = sessionmaker(bind=engine)
    session = Session()
    return session
