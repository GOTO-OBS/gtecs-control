import datetime
from operator import attrgetter

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import (Column, Integer, String, DateTime, Float,
                        ForeignKey, Enum)
from sqlalchemy.orm import relationship, validates
from sqlalchemy import func, select, and_
from sqlalchemy.orm import column_property

from astropy.time import Time
from astropy import units as u

Base = declarative_base()


class Event(Base):

    """
    A class to represent a transient Event.

    Like all SQLAlchemy model classes, these objects link to the
    underlying database. You can create an object, set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the eventID)
    will be None until the Event is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        ivo : string
            ivorn for the event
        name : string
            a human-readable identifier for the event
        source : string
            the event's origin, e.g. LVC
        skymap : string, optional
            the location of the source skymap file

    An Event also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        eventID : int
            primary key for events
        eventTiles : list of `EventTile`
            a list of any `EventTiles` associated with this Event
        mpointings : list of `Mpointing`
            a list of any `Mpointing`s associated with this Event
        pointings : list of `Pointing`
            a list of any `Pointing`s associated with this Event

    Examples
    --------

        >>> e = Event(ivo='ivo://pt5mTest', name='pt5mVar3', source='pt5m')

    """

    __tablename__ = "events"

    eventID = Column(Integer, primary_key=True)
    name = Column(String)
    source = Column(String)
    ivo = Column(String, unique=True)
    skymap = Column(String)

    eventTiles = relationship("EventTile", back_populates="event")

    def __repr__(self):
        return "Event(eventID={}, name={}, source={}, ivo={}, skymap={})".format(
            self.eventID, self.name, self.source, self.ivo, self.skymap
        )


class User(Base):

    """
    A class to represent a database User.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an User, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the userKey)
    will be None until the User is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        userName : string
            a short user name
        password : string
            password for authentication. stored as a hash in the database.
        fullName : string
            the user's full name.

    A User also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        userKey : int
            primary key for users
        mpointings : list of `Mpointing`
            a list of any `Mpointing`s associated with this User
        pointings : list of `Pointing`
            a list of any `Pointing`s associated with this User

    Examples
    --------

        >>> from gtecs.database import *
        >>> from gtecs.database import _make_random_pointing
        >>>
        >>> bob = User(userName='bob', password='1234', fullName="Bob Marley")
        >>> session = load_session()
        >>> session.add(bob)  # add bob to database
        >>> session.commit()  # commit changes (otherwise DB not changed)
        >>> bob
        User(userKey=25, username=bob, fullName=Bob Marley)
        >>> bob.pointings
        []
        >>> pointing = _make_random_pointing(25)  # make a pointing for bob
        >>> session.add(pointing)
        >>> session.commit()
        >>> bob.pointings
        [Pointing(pointingID=19073, objectName=randObj, ra=228.687,
        decl=-53.5477, rank=67, minAlt=30.0, maxSunAlt=-15.0,
        minTime=428.848, maxMoon=D, ToO=1, startUTC=2016-08-17 14:45:15,
        stopUTC=2016-08-26 14:45:15, status=pending, eventID=None,
        userKey=25, mpointingID=None, repeatID=None, eventTileID=None)]
        >>> session.close()

    """

    __tablename__ = "users"

    userKey = Column(Integer, primary_key=True)
    userName = Column('user_name', String)
    password = Column(String)
    fullName = Column(String)

    def __repr__(self):
        return "User(userKey={}, username={}, fullName={})".format(
            self.userKey, self.userName, self.fullName
        )


class EventTile(Base):

    """
    A class to represent a Tile from an Event (e.g. a LVC skymap).

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an EventTile, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the tileID)
    will be None until the User is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        probability : float
            contained target probability within this tile
        ra : float, optional
            J2000 right ascension in decimal degrees
            if ra is not given and this EventTile is linked to a SurveyTile
            then the ra will be extracted from the SurveyTile
        decl : float, optional
            J2000 declination in decimal degrees
            if dec is not given and this EventTile is linked to a SurveyTile
            then the dec will be extracted from the SurveyTile
        eventID : int, optional
            the ID number of the Event this tile is associated with
        surveyTileID : int, optional
            the SurveyTile this tile is associated with, if any

    An EventTile also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        tileID : int
            primary key for event tiles
        mpointing : `Mpointing`
            the `Mpointing` associated with this EventTile if any
        pointings : list of `Pointing`
            a list of any `Pointing`s associated with this tile, if any

    Examples
    --------

        >>> from gtecs.database import *

        make a LIGO event to associate our tile with

        >>> e = Event(ivo='ivo://GW150914', name='GW150914', source='LVC')
        >>> session = load_session()
        >>> session.add(e)  # add event
        >>> session.commit()  # commit changes (otherwise DB not changed)
        >>> e.eventID
        1

        construct without eventID

        >>> event_tile = EventTile(ra=122.34, decl=22.01, probability=0.01)
        >>> event_tile
        EventTile(tileID=None, ra=122.34, decl=22.01, probability=0.01, eventID=None, surveytileID=None)

        set the eventID

        >>> event_tile.eventID = 1
        >>> event_tile
        EventTile(tileID=None, ra=122.34, decl=22.01, probability=0.01, eventID=None, surveytileID=None)

        add to the database

        >>> session.add(event_tile)
        >>> session.commit()
        >>> event_tile  # note how tileID is populated now tile is in DB
        EventTile(tileID=1, ra=122.34, decl=22.01, probability=0.01, eventID=1, surveytileID=None)
        >>> e.eventTiles  # and event knows about all associated tiles
        [EventTile(tileID=1, ra=122.34, decl=22.01, probability=0.01, eventID=1, surveytileID=None)]
        >>> session.close()

        make a survey and a survey tile, and add them to the database

        >>> s = Survey(name='GOTO survey')
        >>> st = SurveyTile(ra=22, decl=-2, name='Tile1')
        >>> st.survey = s
        >>> session.add(s, st)
        >>> session.commit()

        make a new event tile that has no ra and decl,
        but is linked to the survey tile

        >>> et2 = EventTile(probability=0.01)
        >>> et2.event = e
        >>> et2.surveyTile = st
        >>> et2
        EventTile(tileID=None, ra=None, decl=None, probability=0.01, eventID=None, surveytileID=None)

        add to the database

        >>> session.add(event_tile)
        >>> session.commit()
        >>> et2  # note how the ra and decl have been copied from the surveyTile
        EventTile(tileID=2, ra=22.0, decl=-2.0, probability=0.01, eventID=1, surveytileID=1)
        >>> st.eventTiles # and the surveyTile knows about the linked eventTiles
        [EventTile(tileID=2, ra=22.0, decl=-2.0, probability=0.01, eventID=1, surveytileID=1)]

    """

    __tablename__ = "event_tiles"

    tileID = Column(Integer, primary_key=True)
    ra = Column(Float)
    decl = Column(Float)
    probability = Column(Float)

    # handle relationships
    pointings = relationship("Pointing", back_populates="eventTile")
    mpointing = relationship("Mpointing", back_populates="eventTile")

    eventID = Column('events_eventID', Integer, ForeignKey('events.eventID'),
                     nullable=False)
    event = relationship("Event", back_populates="eventTiles", uselist=False)

    surveyTileID = Column('survey_tiles_tileID', Integer,
                          ForeignKey('survey_tiles.tileID'), nullable=True)
    surveyTile = relationship("SurveyTile", back_populates="eventTiles", uselist=False)

    def __repr__(self):
        template = ("EventTile(tileID={}, ra={}, decl={}, " +
                    "probability={}, eventID={}, surveytileID={})")
        return template.format(
            self.tileID, self.ra, self.decl, self.probability, self.eventID,
            self.surveyTileID)

class Survey(Base):

    """
    A class to represent an observation survey.

    Like all SQLAlchemy model classes, these objects link to the
    underlying database. You can create an object, set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the surveyID)
    will be None until the Survey is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        name : string
            a human-readable identifier for the survey

    A Survey also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        surveyID : int
            primary key for surveys
        surveyTiles : list of `SurveyTile`
            a list of any `SurveyTiles` associated with this Survey
        mpointings : list of `Mpointing`
            a list of any `Mpointing`s associated with this Survey
        pointings : list of `Pointing`
            a list of any `Pointing`s associated with this Survey

    Examples
    --------

        >>> s = Survey(name='GOTO4-allsky')

    """

    __tablename__ = "surveys"

    surveyID = Column(Integer, primary_key=True)
    name = Column(String)

    surveyTiles = relationship("SurveyTile", back_populates="survey")

    def __repr__(self):
        return "Survey(surveyID={}, name={})".format(
            self.surveyID, self.name
        )


class SurveyTile(Base):

    """
    A class to represent a Survey Tile.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an SurveyTile, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the tileID)
    will be None until the User is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        ra : float
            J2000 right ascension in decimal degrees
        decl : float
            J2000 declination in decimal degrees
        surveyID : int, optional
            the ID number of the Survey this tile is associated with
        name : str, optional
            a human-readable identifier for the tile

    A SurveyTile also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        tileID : int
            primary key for survey tiles
        eventTiles : list of `EventTile`
            a list of any `EventTiles` associated with this SurveyTiles, if any
        mpointing : `Mpointing`
            the `Mpointing` associated with this SurveyTile if any
        pointings : list of `Pointing`
            a list of any `Pointing`s associated with this tile, if any

    Examples
    --------

        >>> from gtecs.database import *

        make a survey to associate our tile with

        >>> s = Survey(name='GOTO4-allsky')
        >>> session = load_session()
        >>> session.add(s)  # add survey
        >>> session.commit()  # commit changes (otherwise DB not changed)
        >>> s.surveyID
        1

        construct without surveyID

        >>> tile = SurveyTile(ra=100, decl=20)
        >>> tile
        SurveyTile(tileID=None, ra=100.00, decl=20.00)

        set the surveyID

        >>> event_tile.eventID = 1
        >>> event_tile
        EventTile(tileID=None, ra=122.34, decl=22.01, probability=0.01, eventID=None)

        add to the database, demonstrating the open_session context manager,
        which will handle committing changes for you:

        >>> with open_session as session():
        >>>     insert_items([tile])
        >>>     tile  # note how tileID is populated now tile is in DB
        SurveyTile(tileID=1, ra=100.00, decl=20.00, surveyID=1)
        >>> with open_session as session():
        >>>     s.surveyTiles  # and survey knows about all associated tiles
        [SurveyTile(tileID=1, ra=100.00, decl=20.00, surveyID=1)]

    """

    __tablename__ = "survey_tiles"

    tileID = Column(Integer, primary_key=True)
    ra = Column(Float)
    decl = Column(Float)
    name = Column(String)

    eventTiles = relationship("EventTile", back_populates="surveyTile")
    mpointing = relationship("Mpointing", back_populates="surveyTile")
    pointings = relationship("Pointing", back_populates="surveyTile")

    surveyID = Column('surveys_surveyID', Integer, ForeignKey('surveys.surveyID'),
                     nullable=False)
    survey = relationship("Survey", back_populates="surveyTiles", uselist=False)

    def __repr__(self):
        template = ("SurveyTile(tileID={}, ra={}, decl={}, " +
                    ", name={}, surveyID={})")
        return template.format(
            self.tileID, self.ra, self.decl, self.name, self.surveyID)


status_list = Enum(
    'pending',
    'aborted',
    'completed',
    'running',
    'deleted',
    'upcoming',
    'interrupted',
    'expired'
)


class Repeat(Base):

    """
    A class to represent a Repeat observation.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an Repeat, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the repeatID)
    will be None until the Repeat is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        repeatNum : int
            an integer indicating which repeat in a sequence this is
        waitTime : float
            time to wait since the previous repeat before observing, in minutes
        valid_duration : float
            amount of time repeat should stay valid in the queue, in minutes.
            must be less than waitTime
        mpointingID : int
            unique key linking this repeat to an Mpointing
        status : string, optional
            status indicator. default='upcoming', meaning repeat will be attempted
            at some point in the future

    A Repeat also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        ts : `datetime.datetime`
            timestamp indicating last time this repeat was changed in DB
        mpointing : `Mpointing`
            the `Mpointing` associated with this SurveyTile if any

    Examples
    --------

        >>> from gtecs.database import *

        make a Repeat (an Mpointing with mpointingID = 1 is already in the database)

        >>> r = Repeat(repeatNum=1, waitTime=100, valid_duration=99, mpointingID=1)
        >>> session = load_session()
        >>> session.add(r)
        Repeat(repeatID=7, repeatNum=1, waitTime=100.0, valid_duration=99.0, status=upcoming, mpointingID=1)

    """

    __tablename__ = "repeats"

    repeatID = Column(Integer, primary_key=True)
    repeatNum = Column(Integer)
    waitTime = Column(Integer)
    valid_duration = Column(Integer)
    status = Column(status_list, server_default='upcoming')
    ts = Column(DateTime)
    mpointingID = Column('mpointings_mpointingID', Integer,
                         ForeignKey('mpointings.mpointingID'),
                         nullable=False)

    @validates('waitTime', 'valid_duration')
    def validate_timings(self, key, field):
        if key == 'waitTime' and self.valid_duration is not None:
            if field < self.valid_duration and field > 0:
                raise AssertionError('waitTime must be > valid_duration')
        elif key == 'valid_duration' and self.waitTime is not None:
            if self.waitTime < field and self.waitTime > 0:
                raise AssertionError('waitTime must be > valid_duration')
        return field

    # relationships
    mpointing = relationship("Mpointing", back_populates="repeats", uselist=False)
    pointing = relationship("Pointing", back_populates="repeat", uselist=False)

    def __repr__(self):
        template = ("Repeat(repeatID={}, repeatNum={}, waitTime={}, " +
                    "valid_duration={}, status={}, mpointingID={})")
        return template.format(self.repeatID, self.repeatNum, self.waitTime,
                               self.valid_duration, self.status, self.mpointingID)


class Mpointing(Base):

    """
    A class to represent an Mpointing.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an Mpointing, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the mpointingID)
    will be None until the Mpointing is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        userKey : int
            unique key identifying user to whom this Mpointing belongs
        objectName : String
            object name
        ra : float, optional
            J2000 right ascension in decimal degrees
            if ra is not given and this Mpointing is linked to a SurveyTile
            then the ra will be extracted from the SurveyTile
        decl : float, optional
            J2000 declination in decimal degrees
            if decl is not given and this Mpointing is linked to a SurveyTile
            then the decl will be extracted from the SurveyTile
        start_rank : Integer
            rank to use for first pointing in series
        minAlt : float
            minimum altitude to observer at
        minTime : float
            minimum time needed to schedule pointing
        maxSunAlt : float
            altitude constraint on Sun
        maxMoon : string
            Moon constraint. one of 'D', 'G', 'B'.
        ToO : int
            0 or 1 to indicate if this is a ToO or not
        num_repeats : int
            number of times to attempt pointing. less than or equal to zero means repeat infinitely.
        intervals : float or list of float
            intervals between repeats in minutes. This can
            be specified during initialisation along with
            `valid_durations`. If a list, should be one less than `num_repeats`.
        valid_durations : float or list of float
            the amount of time the pointing should be valid in
            the queue. Must be less than the corresponding interval.

        eventTileID : int, optional
            unique key linking to `EventTile`
        surveyTileID : int, optional
            unique key linking to `SurveyTile`
        eventID : int, optional
            unique key linking to `Event`
        surveyID : int, optional
            unique key linking to `Survey`

    An Mpointing also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        mpointingID : int
            primary key for mpointings
        rank : int
            rank for next pointing to be scheduled
        scheduled : bool
            True is a pointing is currently in the queue
        num_remain : int
            number of remaining pointings
        num_completed : int
            number of successfully completed pointings
        exposure_sets : list of `ExposureSet`
            the `ExposureSet` objects associated with this `Mpointing`, if any
        repeats : list of `Repeat`
            the `Repeat` objects associated with this `Mpointing`, if any
        surveyTile : `SurveyTile`
            the `SurveyTile` associated with this `Mpointing`, if any
        eventTile : `EventTile`
            the `EventTile` associated with this `Mpointing`, if any
        event : `Event`
            the `Event` associated with this `Mpointing`, if any
        survey : `Survey`
            the `Survey` associated with this `Mpointing`, if any

    Examples
    --------

        >>> from gtecs.database import *
        >>> mp = Mpointing()
        >>> mp
        Mpointing(mpointingID=None, objectName=None, ra=None, decl=None, rank=None,
        start_rank=None, minAlt=None, maxSunAlt=None, minTime=None, maxMoon=None,
        ToO=None, num_repeats=None, num_completed=None, num_remain=None, scheduled=False, eventID=None,
        userKey=None, eventTileID=None, surveyTile=None)

        make a more useful Mpointing - repeating on increasing intervals, which stay in the
        queue for 5 minutes.

        >>> mp = Mpointing(objectName='M31', ra=22, decl=-5, start_rank=9, minAlt=30, minTime=3600,
        ... ToO=0, maxMoon='B', num_repeats=6, userKey=24, intervals=[10,20,30,40,50],
        ... valid_durations=5, maxSunAlt=-15)
        >>> mp
        Mpointing(mpointingID=None, objectName=M31, ra=22, decl=-5, rank=9, start_rank=9, minAlt=30, maxSunAlt=-15, minTime=3600,
        maxMoon=B, ToO=0, num_repeats=None, num_completed=None, scheduled=None, eventID=False, userKey=24, eventTileID=None,
        surveyTile=None)

        notice how `num_repeats` and `num_remain` are None, even though when we look at the repeats attribute we find

        >>> mp.repeats
        [Repeat(repeatID=None, repeatNum=0, waitTime=0, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=None, repeatNum=1, waitTime=10, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=None, repeatNum=2, waitTime=20, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=None, repeatNum=3, waitTime=30, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=None, repeatNum=4, waitTime=40, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=None, repeatNum=5, waitTime=50, valid_duration=1, status=upcoming, mpointingID=None),
        ]

        That is because `num_repeats` and `num_remain` are queried from the database. If we add the Mpointing
        we see that these values are populated, and the repeats are added to the database and get IDs

        >>> session.add(mp)
        >>> session.commit()
        >>> mp
        Mpointing(mpointingID=1, objectName=M31, ra=22.0, decl=-5.0, rank=9, start_rank=9, minAlt=30.0, maxSunAlt=-15.0,
        minTime=3600.0, maxMoon=B, ToO=0, num_repeats=6, num_completed=0, scheduled=0, eventID=0, userKey=24,
        eventTileID=None, surveyTile=None)
        >>> mp.repeats
        [Repeat(repeatID=1, repeatNum=0, waitTime=0, valid_duration=1, status=upcoming, mpointingID=None)
         Repeat(repeatID=2, repeatNum=1, waitTime=10, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=3, repeatNum=2, waitTime=20, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=4, repeatNum=3, waitTime=30, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=5, repeatNum=4, waitTime=40, valid_duration=1, status=upcoming, mpointingID=None),
         Repeat(repeatID=6, repeatNum=5, waitTime=50, valid_duration=1, status=upcoming, mpointingID=None),
        ]

        we can add more repeats directly.

        >>> mp.repeats.append(Repeat(repeatNum=6, waitTime=100, valid_duration=5, status='upcoming'))
        >>> session.commit()
        >>> mp.num_repeats
        7

        To be useful, an Mpointing should have a list of `ExposureSet`s associated with it. We
        can either add these to the `exposure_sets` attribute directly:

        >>> e1 = ExposureSet(typeFlag='SCIENCE', filt='L', expTime=20, numexp=20, binning=2)
        >>> mp.exposure_sets.append(e1)

        or create `ExposureSet` instances with the `mpointingID` attribute set, and the database will take
        care of the rest:

        >>> e2 = ExposureSet(typeFlag='SCIENCE', filt='G', expTime=20, numexp=20, binning=2, mpointingID=1)
        >>> e3 = ExposureSet(typeFlag='SCIENCE', filt='R', expTime=20, numexp=20, binning=2, mpointingID=1)
        >>> insert_items(session, [e2, e3])
        >>> session.commit()
        >>> mp.exposure_sets
        [ExposureSet(expID=126598, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=L, expTime=20.0, numexp=20, binning=2, otaMask=None, pointingID=None, mpointingID=1),
         ExposureSet(expID=126599, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=G, expTime=20.0, numexp=20, binning=2, otaMask=None, pointingID=None, mpointingID=1),
         ExposureSet(expID=126600, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=R, expTime=20.0, numexp=20, binning=2, otaMask=None, pointingID=None, mpointingID=1)]

        >>> session.close()

    """

    __tablename__ = "mpointings"

    mpointingID = Column(Integer, primary_key=True)
    objectName = Column('object', String)
    ra = Column(Float)
    decl = Column(Float)
    rank = Column(Integer)
    start_rank = Column(Integer)
    minAlt = Column(Float)
    maxSunAlt = Column(Float)
    minTime = Column(Float)
    maxMoon = Column(String(1))
    ToO = Column(Integer)
    infinite = Column(Integer, default=False)
    scheduled = Column(Integer, default=False)

    eventID = Column('events_eventID', Integer, ForeignKey('events.eventID'),
                     nullable=True)
    event = relationship("Event", backref="mpointings")

    userKey = Column('users_userKey', Integer, ForeignKey('users.userKey'),
                     nullable=False)
    user = relationship("User", backref="mpointings", uselist=False)

    eventTileID = Column('event_tiles_tileID', Integer,
                        ForeignKey('event_tiles.tileID'), nullable=True)
    eventTile = relationship("EventTile", back_populates="mpointing", uselist=False)

    surveyID = Column('surveys_surveyID', Integer, ForeignKey('surveys.surveyID'),
                     nullable=True)
    survey = relationship("Survey", backref="mpointings")

    surveyTileID = Column('survey_tiles_tileID', Integer,
                          ForeignKey('survey_tiles.tileID'), nullable=True)
    surveyTile = relationship("SurveyTile", back_populates="mpointing", uselist=False)

    repeats = relationship("Repeat", back_populates="mpointing", viewonly=True)
    num_repeats = column_property(
        select([func.count(Repeat.repeatID)]).where(Repeat.mpointingID == mpointingID).correlate_except(Repeat)
    )
    num_remain = column_property(
        select([func.count(Repeat.repeatID)]).where(and_(Repeat.mpointingID == mpointingID,
                                                    Repeat.status == 'upcoming')).correlate_except(Repeat)
    )
    num_completed = column_property(
        select([func.count(Repeat.repeatID)]).where(and_(Repeat.mpointingID == mpointingID,
                                                    Repeat.status == 'completed')).correlate_except(Repeat)
    )

    def __repr__(self):
        template = ("Mpointing(mpointingID={}, objectName={}, ra={}, decl={}, " +
                    "rank={}, start_rank={}, minAlt={}, maxSunAlt={}, " +
                    "minTime={}, maxMoon={}, ToO={}, num_repeats={}, num_completed={}, " +
                    "num_remain={}, scheduled={}, eventID={}, userKey={}, eventTileID={}, surveyID={}, surveyTileID={})")
        return template.format(
            self.mpointingID, self.objectName, self.ra, self.decl, self.rank, self.start_rank,
            self.minAlt, self.maxSunAlt, self.minTime, self.maxMoon, self.ToO,
            self.num_repeats, self.num_completed, self.num_remain, self.scheduled, self.eventID,
            self.userKey, self.eventTileID, self.surveyID, self.surveyTileID
        )

    def __init__(self, objectName=None, ra=None, decl=None,
                 start_rank=None, minAlt=None, minTime=None,
                 maxMoon=None, maxSunAlt=None, ToO=None, num_repeats=None,
                 intervals=None, valid_durations=None, **kwargs):
        self.ra = ra
        self.decl = decl
        self.objectName = objectName
        self.start_rank = start_rank
        self.maxMoon = maxMoon
        self.minAlt = minAlt
        self.minTime = minTime
        self.maxSunAlt = maxSunAlt
        self.ToO = ToO
        self.rank = self.start_rank
        self.scheduled = False
        self.infinite = False

        # now add repeats and intervals
        if intervals is not None and valid_durations is not None:

            # first convert to lists
            try:
                if len(intervals) != num_repeats-1:
                    raise ValueError("number of intervals should be one less than number of repeats or be scalar")
                intervals = [0] + intervals
            except TypeError:
                # intervals was scalar
                if num_repeats <= 0:
                    self.infinite = True
                    num_repeats = 1

                if num_repeats == 1:
                    intervals = [intervals]
                else:
                    intervals = [0] + [intervals] * (num_repeats-1)
            try:
                if len(valid_durations) != num_repeats:
                    raise ValueError("number of durations should match number of repeats or be scalar")
            except TypeError:
                valid_durations = [valid_durations] * num_repeats

            # now add
            repeatNum = 0
            for interval, duration in zip(intervals, valid_durations):
                self.repeats.append(
                    Repeat(repeatNum=repeatNum, waitTime=interval,
                           valid_duration=duration, status='upcoming')
                )
                repeatNum += 1

        if 'eventID' in kwargs:
            self.eventID = kwargs['eventID']
        if 'event' in kwargs:
            self.event = kwargs['event']
        if 'user' in kwargs:
            self.user = kwargs['user']
        if 'userKey' in kwargs:
            self.userKey = kwargs['userKey']
        if 'surveyID' in kwargs:
            self.surveyID = kwargs['surveyID']
        if 'survey' in kwargs:
            self.survey = kwargs['survey']
        if 'surveyTile' in kwargs:
            self.surveyTile = kwargs['surveyTile']
        if 'surveyTileID' in kwargs:
            self.surveyTileID = kwargs['surveyTileID']
        if 'eventTile' in kwargs:
            self.eventTile = kwargs['eventTile']
        if 'eventTileID' in kwargs:
            self.eventTileID = kwargs['eventTileID']

    def get_next_last_repeats(self):
        """
        Return the next repeat to be executed, and the last one executed.

        Assumes this object is still associated to an active session.

        Returns
        -------
        next, last : `gtecs.database.Repeat`
            The next repeat to schedule and the last repeat done (may be None).
        """
        sorted_repeats = sorted([rp for rp in self.repeats if rp.status == 'upcoming'], key=attrgetter('repeatNum'))
        if sorted_repeats:
            nr = sorted_repeats[0]
        else:
            nr = None
        sorted_repeats = sorted([rp for rp in self.repeats if rp.status not in ['upcoming', 'running']], key=attrgetter('repeatNum'), reverse=True)
        if sorted_repeats:
            lr = sorted_repeats[0]
        else:
            lr = None
        return nr, lr

    def get_next_pointing(self):
        """
        Retrieve the next pointing which needs to be scheduled.

        The start and stop UTC of the pointing are determined from
        the status of the previous pointing.

        Assumes this object is still associated with an active session.

        Returns
        -------
        pointing : `Pointing`
            the next pointing that should be sent to the database. Is None
            if there is no suitable pointing remaining, or if there is
            a pointing from this Mpointing already scheduled
        """

        # case A: already scheduled, return None
        if self.scheduled:
            return None

        next_repeat, last_repeat = self.get_next_last_repeats()

        if next_repeat is None:
            return None

        if last_repeat is None:
            # no completed or aborted repeats yet
            last_repeat = self.repeats[0]

        last_repeat_status = last_repeat.status

        if last_repeat_status == 'completed':
            startUTC = Time.now() + next_repeat.waitTime * u.minute
        else:
            startUTC = Time.now()
        stopUTC = startUTC + next_repeat.valid_duration * u.minute

        # now create a pointing
        p = Pointing(
            objectName=self.objectName, ra=self.ra,
            decl=self.decl, rank=self.rank, minAlt=self.minAlt,
            maxSunAlt=self.maxSunAlt, minTime=self.minTime, maxMoon=self.maxMoon,
            startUTC=startUTC, stopUTC=stopUTC, ToO=self.ToO, status='pending',
            repeatID=next_repeat.repeatID, userKey=self.userKey, eventID=self.eventID,
            mpointingID=self.mpointingID, eventTileID=self.eventTileID, surveyID=self.surveyID, surveyTileID=self.surveyTileID
        )
        # add the exposures
        p.exposure_sets = self.exposure_sets
        return p


class Pointing(Base):
    """
    A class to represent an Pointing.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an Pointing, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the pointingID)
    will be None until the Pointing is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        userKey : int
            unique key identifying user to whom this Pointing belongs
        objectName : String
            object name
        ra : float, optional
            J2000 right ascension in decimal degrees
            if ra is not given and this Pointing is linked to a SurveyTile
            then the ra will be extracted from the SurveyTile
        decl : float, optional
            J2000 declination in decimal degrees
            if decl is not given and this Pointing is linked to a SurveyTile
            then the decl will be extracted from the SurveyTile
        rank : Integer
            rank to use for pointing
        minAlt : float
            minimum altitude to observer at
        minTime : float
            minimum time needed to schedule pointing
        maxSunAlt : float
            altitude constraint on Sun
        maxMoon : string
            Moon constraint. one of 'D', 'G', 'B'.
        ToO : int
            0 or 1 to indicate if this is a ToO or not
        startUTC : string, `astropy.time.Time` or datetime.datetime
            UTC time from which pointing is considered valid and can be started
        stopUTC : string, `astropy.time.Time` or datetime.datetime
            the latest UTC time at which pointing may be started

        status : string, optional
            status of pointing, default 'pending'
        eventTileID : int, optional
            unique key linking to a `EventTile`
        surveyTileID : int, optional
            unique key linking to a `SurveyTile`
        eventID : int, optional
            unique key linking to an `Event`
        surveyID : int, optional
            unique key linking to a `Survey`
        mpointingID : int, optional
            unique key linking to an `Mpointing`

    An Pointing also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        pointingID : int
            primary key for mpointings
        exposure_sets : list of `ExposureSet`
            the `ExposureSet` objects associated with this `Pointing`, if any
        eventTile : `EventTile`
            the `EventTile` associated with this `Pointing`, if any
        surveyTile : `SurveyTile`
            the `SurveyTile` associated with this `Pointing`, if any
        event : `Event`
            the `Event` associated with this `Pointing`, if any
        survey : `Survey`
            the `Survey` associated with this `Pointing`, if any
        mpointing : `Mpointing`
            the `Mpointing` associated with this `Pointing`, if any

    Examples
    --------

        >>> from gtecs.database import *
        >>> from astropy import units as u
        >>> from astropy.time import Time
        >>> p = Pointing(objectName='IP Peg', ra=350.785625, decl=18.416472, rank=9, minAlt=30, maxSunAlt=-15,
        ... minTime=3600, maxMoon='G', ToO=0, startUTC=Time.now(), stopUTC=Time.now()+3*u.day, userKey=24)
        >>> p
        Pointing(pointingID=None, objectName=IP Peg, ra=350.785625, decl=18.416472, rank=9, minAlt=30,
        maxSunAlt=-15, minTime=3600, maxMoon=G, ToO=0, startUTC=2016-08-16 20:27:57, stopUTC=2016-08-19 20:27:57,
        status=None, eventID=None, userKey=24, mpointingID=None, repeatID=None, eventTileID=None, surveyID=None, surveyTileID=None)

        we can insert it into the database and the status and pointingID will be set:

        >>> session.add()
        >>> session.commit()
        >>> p.status, p.pointingID
        ('pending', 17073)

        At the moment, this pointing has no exposure sets. We can either add these to the `exposure_sets`
        attribute directly:

        >>> e1 = ExposureSet(typeFlag='SCIENCE', filt='L', expTime=20, numexp=20, binning=2)
        >>> p.exposure_sets.append(e1)

        or create `ExposureSet` instances with the `pointingID` attribute set, and the database will take
        care of the rest:

        >>> e2 = ExposureSet(typeFlag='SCIENCE', filt='G', expTime=20, numexp=20, binning=2, pointingID=17073)
        >>> e3 = ExposureSet(typeFlag='SCIENCE', filt='R', expTime=20, numexp=20, binning=2, pointingID=17073)
        >>> insert_items(session, [e2, e3])
        >>> session.commit()
        >>> p.exposure_sets
        [ExposureSet(expID=126601, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=L, expTime=20.0, numexp=20, binning=2, otaMask=None, pointingID=17073, mpointingID=None),
         ExposureSet(expID=126602, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=G, expTime=20.0, numexp=20, binning=2, otaMask=None, pointingID=17073, mpointingID=None),
         ExposureSet(expID=126603, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=R, expTime=20.0, numexp=20, binning=2, otaMask=None, pointingID=17073, mpointingID=None)]

        >>> session.close()

    """
    __tablename__ = "pointings"

    pointingID = Column(Integer, primary_key=True)
    objectName = Column("object", String)
    ra = Column(Float)
    decl = Column(Float)
    rank = Column(Integer)
    minAlt = Column(Float)
    maxSunAlt = Column(Float, default=-15)
    minTime = Column(Float)
    maxMoon = Column(String(1))
    startUTC = Column(DateTime)
    stopUTC = Column(DateTime)
    ToO = Column(Integer)
    status = Column(status_list, default='pending')

    # use validators to allow various types of input for UTC
    # also enforce stopUTC > startUTC
    @validates('startUTC', 'stopUTC')
    def munge_times(self, key, field):
        if isinstance(field, datetime.datetime):
            value = field.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(field, Time):
            field.precision = 0  # no D.P on seconds
            value = field.iso
        else:
            # just hope the string works!
            value = str(field)

        if (key == 'startUTC' and self.stopUTC is not None):
            if Time(value) >= Time(self.stopUTC):
                raise AssertionError("stopUTC must be later than startUTC")
        elif key == 'stopUTC' and self.startUTC is not None:
            if Time(self.startUTC) >= Time(value):
                raise AssertionError("stopUTC must be later than startUTC")

        return value

    # now include relationships to other tables
    eventID = Column('events_eventID', Integer, ForeignKey('events.eventID'),
                     nullable=True)
    event = relationship("Event", backref="pointings")

    userKey = Column('users_userKey', Integer, ForeignKey('users.userKey'),
                     nullable=False)
    user = relationship("User", backref="pointings", uselist=False)

    mpointingID = Column('mpointings_mpointingID', Integer,
                         ForeignKey('mpointings.mpointingID'),
                         nullable=True)
    mpointing = relationship("Mpointing", backref="pointings", uselist=False)

    repeatID = Column('repeats_repeatID', Integer,
                      ForeignKey('repeats.repeatID'), nullable=True)
    repeat = relationship("Repeat", back_populates="pointing", uselist=False)

    eventTileID = Column('event_tiles_tileID', Integer,
                        ForeignKey('event_tiles.tileID'), nullable=True)
    eventTile = relationship("EventTile", back_populates="pointings", uselist=False)

    surveyID = Column('surveys_surveyID', Integer, ForeignKey('surveys.surveyID'),
                     nullable=True)
    survey = relationship("Survey", backref="pointings")

    surveyTileID = Column('survey_tiles_tileID', Integer,
                        ForeignKey('survey_tiles.tileID'), nullable=True)
    surveyTile = relationship("SurveyTile", back_populates="pointings", uselist=False)

    def __repr__(self):
        template = ("Pointing(pointingID={}, objectName={}, ra={}, decl={}, " +
                    "rank={}, minAlt={}, maxSunAlt={}, " +
                    "minTime={}, maxMoon={}, ToO={}, startUTC={}, " +
                    "stopUTC={}, status={}, eventID={}, userKey={}, " +
                    "mpointingID={}, repeatID={}, eventTileID={}, surveyID={}, surveyTileID={})")
        return template.format(
            self.pointingID, self.objectName, self.ra, self.decl, self.rank,
            self.minAlt, self.maxSunAlt, self.minTime, self.maxMoon, self.ToO,
            self.startUTC, self.stopUTC, self.status, self.eventID,
            self.userKey, self.mpointingID, self.repeatID, self.eventTileID, self.surveyID, self.surveyTileID
        )


class ExposureSet(Base):

    """
    A class to represent an Exposure Set: a set of repeated identical exposures.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an ExposureSet, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the expID)
    will be None until the ExposureSet is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        typeFlag : string
            indicates the type of exposure set.
            one of SCIENCE, FOCUS, STD, FLAT, BIAS, DARK
        filt : string
            filter to use
        expTime : float
            exposure time in seconds
        numexp : int
            number of exposures within the set
        binning : int
            binning to apply

        raoff : float, optional
            the size of the random offset to apply between each exposure
            if not set, no offset will be made
        decoff : float, optional
            the size of the random offset to apply between each exposure
            if not set, no offset will be made
        otaMask : int, optional
            if set, this is a binary mask which will determine which OTAs
            carry out the exposure. So a value of 5 (binary 0101) will be
            carried out on OTAs 1 and 3.
        mpointingID : int, optional
            unique key linking to an `Mpointing`
        pointingID : int, optional
            unique key linking to an `Pointing`

    An ExposureSet also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        expID : int
            primary key for ExposureSets
        mpointing : `Mpointing`
            the `Mpointing` associated with this `ExposureSet`, if any
        pointing : `Pointing`
            the `Pointing` associated with this `ExposureSet`, if any

    """
    __tablename__ = "exposure_sets"

    expID = Column(Integer, primary_key=True)
    raoff = Column(Float, server_default='0.0')
    decoff = Column(Float, server_default='0.0')
    typeFlag = Column(Enum('SCIENCE', 'FOCUS', 'DARK', 'BIAS', 'FLAT', 'STD'))
    filt = Column('filter', String(2))
    expTime = Column(Float)
    numexp = Column(Integer)
    binning = Column(Integer)
    otaMask = Column(Integer, nullable=True)

    pointingID = Column('pointings_pointingID', Integer,
                        ForeignKey('pointings.pointingID'),
                        nullable=False)
    pointing = relationship("Pointing", backref="exposure_sets", uselist=False)

    mpointingID = Column('mpointings_mpointingID', Integer,
                         ForeignKey('mpointings.mpointingID'),
                         nullable=False)
    mpointing = relationship("Mpointing", backref="exposure_sets", uselist=False)

    def __repr__(self):
        template = ("ExposureSet(expID={}, raoff={}, decoff={}, typeFlag={}, " +
                    "filt={}, expTime={}, numexp={}, binning={}, otaMask={}, " +
                    "pointingID={}, mpointingID={})")
        return template.format(
            self.expID, self.raoff, self.decoff, self.typeFlag, self.filt,
            self.expTime, self.numexp, self.binning, self.otaMask,
            self.pointingID, self.mpointingID
        )


class ObslogEntry(Base):

    """
    A class to represent an entry in the Obslog table.

    The Obslog is a duplication of a subset of the information in the
    FITS headers of a single file, allowing easy retrieval of information.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an ObslogEntry, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the obsID)
    will be None until the exposure is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        frame : string
            the frame ID number which identifies the corresponding FITS file
        utStart : string
            UT time at start of exposure (format 2016-08-22 12:00:01)
        objectName : string
            the same object name as listed in FITS headers
        frameType : string
            see `ExposureSet.typeFlag`
        ra : float
            J2000 right ascension in decimal degrees
        decl : float
            J2000 declination in decimal degrees
        expTime : float
            exposure time in seconds
        airmass : float
            airmass at start of observation
        filt : string
            filter used
        binning : int
            binning used
        focus : int
            position of focuser
        temp : float
            external temperature, degrees celsius
        pointingID : int
            the unique key of the pointing which generated this frame
        otaID : int
            the unique key identifying the OTA
        camID : int
            a unique key identifying the camera

    An ObslogEntry also has the following property which is
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        pointing : `Pointing`
            the `Pointing` associated with this `ObslogEntry`

    """
    __tablename__ = "obslog"

    obsID = Column(Integer, primary_key=True)
    otaID = Column(Integer)
    camID = Column(Integer)
    frame = Column(String)
    utStart = Column("UTstart", DateTime)
    objectName = Column("object", String)
    frameType = Column("type", String)
    ra = Column(Float)
    decl = Column(Float)
    expTime = Column(Float)
    airmass = Column(Float)
    filt = Column('filter', String(2))
    binning = Column(Integer)
    focus = Column(Float)
    temp = Column(Float)

    pointingID = Column('pointings_pointingID', Integer,
                        ForeignKey('pointings.pointingID'),
                        nullable=False)
    pointing = relationship("Pointing", backref="obslog", uselist=False)

    def __repr__(self):
        template = ("ObslogEntry(obsID={}, frame={}, utStart={}, " +
                    "objectName={}, frameType={}, ra={}, decl={}, " +
                    "expTime={}, airmass={}, filt={}, binning={}, focus={}, " +
                    "temp={}, pointingID={}, otaID={}, camID={})")
        return template.format(
            self.obsID, self.frame, self.utStart, self.objectName, self.frameType,
            self.ra, self.decl, self.expTime, self.airmass, self.filt, self.binning,
            self.focus, self.temp, self.pointingID, self.otaID, self.camID
        )
