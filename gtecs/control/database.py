"""Control database functions and ORM."""

import datetime
from contextlib import contextmanager

from astropy.time import Time


from gtecs.common.database import get_session as get_session_common
try:
    # If the gtecs.obs package is available, build on the models from there
    from gtecs.obs.database.models import Base
    OBSDB_CONNECTION = True
except ImportError:
    # Create a new base class
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()
    OBSDB_CONNECTION = False

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import backref, relationship, validates

from . import params


def get_session(user=None, password=None, host=None, echo=None, pool_pre_ping=None):
    """Create a database connection session.

    All arguments are passed to `gtecs.common.database.get_session()`,
    with the defaults taken from the module parameters.

    Note it is generally better to use the session_manager() context manager,
    which will automatically commit or rollback changes when done.
    """
    # This means the user doesn't need to worry about the params, but can overwrite if needed.
    if user is None:
        user = params.DATABASE_USER
    if password is None:
        password = params.DATABASE_PASSWORD
    if host is None:
        host = params.DATABASE_HOST
    if echo is None:
        echo = params.DATABASE_ECHO
    if pool_pre_ping is None:
        pool_pre_ping = params.DATABASE_PRE_PING
    session = get_session_common(
        user=user,
        password=password,
        host=host,
        echo=echo,
        pool_pre_ping=pool_pre_ping,
    )
    return session


@contextmanager
def session_manager(**kwargs):
    """Create a session context manager connection to the database."""
    session = get_session(**kwargs)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class Exposure(Base):
    """A class to represent a single exposure taken by the camera daemon.

    Each "exposure" often contains multiple images, each taken by a different UT camera.

    Parameters
    ----------
    run_number : int
        The number of this exposure (assigned by the camera daemon).
    set_number : int or `None`
        The number of any set this exposure is part of (assigned by the exposure queue daemon),
        or `None` if it is not part of a set (i.e. taken manually via the camera daemon).
    exptime : float
        The time the cameras were exposing for.
    filt : str or `None`
        The filter used for the exposure.
        If a string it should be a valid filter name (by default 'L', 'R', 'G', 'B', 'C'),
        or `None` if the filter was not defined for this exposure (e.g. for dark frames).
    frametype : str
        Exposure type.
        Usual types include SCIENCE, FOCUS, FLAT, BIAS, DARK, MANUAL or GLANCE.
    ut_mask : int or `None`
        The UT cameras used to take this exposure.
        This is a binary mask, e.g. a value of 5 (binary 0101) will represents cameras 1 and 3.
        A value `None` means all cameras were used.

    start_time : string, `astropy.time.Time` or datetime.datetime
        The time the exposure was started by the camera daemon
    stop_time : string, `astropy.time.Time` or datetime.datetime
        The time the exposure finished (either completed or was aborted)
    completed : bool
        Whether the exposure was completed (True) or aborted (False)

    exposure_set_id : int or None, default=None
        The ExposureSet ID from the `gtecs.obs` database, if this exposure comes via the scheduler.
    pointing_id : int or None, default=None
        The pointing ID from the `gtecs.obs` database, if this exposure comes via the scheduler.

    When created the instance can be linked to the following other tables as parameters,
    otherwise they are populated when it is added to the database:

    Primary relationships
    ---------------------
    images : list of `Image`, optional
        the Images relating to this Exposure, if any

    Attributes
    ----------
    db_id : int
        primary database key
        only populated when the instance is added to the database

    Secondary relationships
    -----------------------
    exposure_sets : list of `gtecs.obs.database.ExposureSet`
        the ExposureSets relating to this Exposure, if any
    pointings : list of `gtecs.obs.database.Pointing`
        the Pointings relating to this Exposure, if any

    """

    # Set corresponding SQL table name
    __tablename__ = 'exposures'
    __table_args__ = {'schema': 'control'}

    # Primary key
    db_id = Column('id', Integer, primary_key=True)

    # Columns
    run_number = Column(Integer, nullable=False, unique=True, index=True)
    set_number = Column(Integer, nullable=True)
    exptime = Column(Float, nullable=False)
    filt = Column('filter',   # filter is a built in function in Python
                  String(1), nullable=True)
    frametype = Column('type',   # type is a built in function in Python
                       String(255), nullable=False)
    ut_mask = Column(Integer, nullable=True, default=None)
    start_time = Column(DateTime, nullable=False)
    stop_time = Column(DateTime, nullable=True)
    completed = Column(Boolean, nullable=False)

    # Psudo-foreign keys
    if OBSDB_CONNECTION:
        exposure_set_id = Column(Integer, ForeignKey('obs.exposure_sets.id'),
                                 nullable=True, index=True)
        pointing_id = Column(Integer, ForeignKey('obs.pointings.id'), nullable=True, index=True)
    else:
        exposure_set_id = Column(Integer, nullable=True)
        pointing_id = Column(Integer, nullable=True)

    # Foreign relationships
    images = relationship(
        'Image',
        order_by='Image.db_id',
        back_populates='exposure',
    )
    if OBSDB_CONNECTION:
        exposure_sets = relationship(
            'ExposureSet',
            order_by='ExposureSet.db_id',
            backref=backref(  # NB Use legacy backref to add corresponding relationship
                'exposures',
                uselist=True,
            ),
            viewonly=True,
        )
        pointings = relationship(
            'Pointing',
            order_by='Pointing.db_id',
            backref=backref(  # NB Use legacy backref to add corresponding relationship
                'exposures',
                uselist=True,
            ),
            viewonly=True,
        )

    def __repr__(self):
        strings = ['db_id={}'.format(self.db_id),
                   'run_number={}'.format(self.run_number),
                   'set_number={}'.format(self.set_number),
                   'exptime={}'.format(self.exptime),
                   'filt={}'.format(self.filt),
                   'frametype={}'.format(self.frametype),
                   'ut_mask={}'.format(self.ut_mask),
                   'start_time={}'.format(self.start_time),
                   'stop_time={}'.format(self.stop_time),
                   'completed={}'.format(self.completed),
                   'exposure_set_id={}'.format(self.exposure_set_id),
                   'pointing_id={}'.format(self.pointing_id),
                   ]
        return 'Exposure({})'.format(', '.join(strings))

    @validates('start_time', 'stop_time')
    def validate_times(self, key, field):  # noqa: U100
        """Use validators to allow various types of input for times."""
        if key == 'stop_time' and field is None:
            # stop_time is nullable, start_time isn't
            return None

        if isinstance(field, datetime.datetime):
            value = field.strftime('%Y-%m-%d %H:%M:%S.%f')
        elif isinstance(field, Time):
            value = field.iso
        else:
            # just hope the string works!
            value = str(field)
        return value


class Image(Base):
    """A class to represent a single image from a camera.

    Parameters
    ----------
    ut : int
        The UT number of the camera used to take this image.
    filename : str
        The name of the image FITS file.
    header : str
        The FITS image header, as a string.

    When created the instance can be linked to the following other tables as parameters,
    otherwise they are populated when it is added to the database:

    Primary relationships
    ---------------------
    exposure : `Exposure`
        the Exposure this Image is part of
        can also be added with the exposure_id parameter

    Attributes
    ----------
    db_id : int
        primary database key
        only populated when the instance is added to the database

    """

    # Set corresponding SQL table name
    __tablename__ = 'images'
    __table_args__ = {'schema': 'control'}

    # Primary key
    db_id = Column('id', Integer, primary_key=True)

    # Columns
    ut = Column(Integer, nullable=False)
    filename = Column(String(255), nullable=False)
    header = Column(String, nullable=False)

    # Foreign keys
    exposure_id = Column(Integer, ForeignKey('control.exposures.id'), nullable=False)

    # Foreign relationships
    exposure = relationship(
        'Exposure',
        uselist=False,
        back_populates='images',
    )

    # Secondary relationships
    if OBSDB_CONNECTION:
        exposure_sets = relationship(
            'ExposureSet',
            order_by='ExposureSet.db_id',
            secondary='control.exposures',
            primaryjoin='Exposure.db_id == Image.exposure_id',
            secondaryjoin='ExposureSet.db_id == Exposure.exposure_set_id',
            backref=backref(  # NB Use legacy backref to add corresponding relationship
                'images',
                uselist=True,
            ),
            viewonly=True,
        )
        pointings = relationship(
            'Pointing',
            order_by='Pointing.db_id',
            secondary='control.exposures',
            primaryjoin='Exposure.db_id == Image.exposure_id',
            secondaryjoin='Pointing.db_id == Exposure.pointing_id',
            backref=backref(  # NB Use legacy backref to add corresponding relationship
                'images',
                uselist=True,
            ),
            viewonly=True,
        )

    def __repr__(self):
        strings = ['filename={}'.format(self.filename),
                   'exposure_id={}'.format(self.exposure_id),
                   ]
        return 'Image({})'.format(', '.join(strings))
