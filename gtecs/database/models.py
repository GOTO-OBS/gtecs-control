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

pointing_status_list = Enum('pending', 'running', 'completed',
                            'aborted', 'interrupted', 'expired', 'deleted')

mpointing_status_list = Enum('unscheduled', 'scheduled', 'completed',
                             'aborted', 'expired', 'deleted')

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
        [Pointing(pointingID=None, status='pending', objectName=randObj, ra=352.133, decl=28.464, rank=84,
        minAlt=30, maxSunAlt=-15, minTime=1575.8310236068696, maxMoon=D, minMoonSep=30, ToO=True,
        startUTC=2018-01-16 16:46:12, stopUTC=2018-01-18 16:46:12, startedUTC=None, stoppedUTC=None,
        userKey=25, mpointingID=None, blockID=None, eventID=None, eventTileID=None, surveyID=None,
        surveyTileID=None)]
        >>> session.close()

    """

    __tablename__ = "users"

    userKey = Column(Integer, primary_key=True)
    userName = Column('user_name', String)
    password = Column(String)
    fullName = Column(String)

    def __repr__(self):
        return "User(userKey={}, userName={}, fullName={})".format(
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
        unobserved_probability : float
            the total probability in this tile that hasn't been observed
            should be updated whenever any overlapping tile is observed
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
    unobserved_probability = Column(Float)

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
        utMask : int, optional
            if set, this is a binary mask which will determine which unit
            telescopes carry out the exposure. A value of 5 (binary 0101) will
            be exposed by cameras 1 and 3.
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
    utMask = Column(Integer, nullable=True)

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
                    "filt={}, expTime={}, numexp={}, binning={}, utMask={}, " +
                    "pointingID={}, mpointingID={})")
        return template.format(
            self.expID, self.raoff, self.decoff, self.typeFlag, self.filt,
            self.expTime, self.numexp, self.binning, self.utMask,
            self.pointingID, self.mpointingID
        )


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
        minMoonSep : float
            distance constraint from the Moon, degrees
        ToO : int
            0 or 1 to indicate if this is a ToO or not
        startUTC : string, `astropy.time.Time` or datetime.datetime
            UTC time from which pointing is considered valid and can be started
        stopUTC : string, `astropy.time.Time` or datetime.datetime, or None
            the latest UTC time at which pointing may be started
            can be None, if so the pointing will stay in the queue indefinitely
            (it can't be marked as expired) and will only leave when observed

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
            primary key for pointings
        startedUTC : datetime.datetime, or None
            if the pointing has started (been marked running)
            this will give the time it was updated
        stoppedUTC : datetime.datetime, or None
            if the pointing has finished (either completed or cancelled for
            some reason) this will give the time it was updated
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
        >>> session = load_session()

        Create a pointing:

        >>> p = Pointing(objectName='IP Peg', ra=350.785625, decl=18.416472, rank=9, minAlt=30, maxSunAlt=-15,
        ... minTime=3600, maxMoon='G', minMoonSep=30, ToO=0, startUTC=Time.now(), stopUTC=Time.now()+3*u.day, userKey=24)
        >>> p
        Pointing(pointingID=None, status='None', objectName=IP Peg, ra=350.785625, decl=18.416472, rank=9, minAlt=30,
        maxSunAlt=-15, minTime=3600, maxMoon=G, minMoonSep=None, ToO=False, startUTC=2018-01-12 17:39:54,
        stopUTC=2018-01-15 17:39:54, startedUTC=None, stoppedUTC=None, userKey=24, mpointingID=None, blockID=None,
        eventID=None, eventTileID=None, surveyID=None, surveyTileID=None)

        We can insert it into the database and the status and pointingID will be set:

        >>> session.add(p)
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
        [ExposureSet(expID=126601, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=L, expTime=20.0, numexp=20, binning=2, utMask=None, pointingID=17073, mpointingID=None),
         ExposureSet(expID=126602, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=G, expTime=20.0, numexp=20, binning=2, utMask=None, pointingID=17073, mpointingID=None),
         ExposureSet(expID=126603, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=R, expTime=20.0, numexp=20, binning=2, utMask=None, pointingID=17073, mpointingID=None)]

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
    minMoonSep = Column(Float, default=30)
    ToO = Column(Integer)
    startUTC = Column(DateTime)
    stopUTC = Column(DateTime)
    startedUTC = Column(DateTime, default=None)
    stoppedUTC = Column(DateTime, default=None)
    status = Column(pointing_status_list, default='pending')

    # use validators to allow various types of input for UTC
    # also enforce stopUTC > startUTC
    # NB stopUTC can be None, for never-expiring pointings
    @validates('startUTC', 'stopUTC')
    def munge_times(self, key, field):
        if key == 'stopUTC' and field is None:
            value = None
        elif isinstance(field, datetime.datetime):
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
        elif key == 'stopUTC' and value is not None and self.startUTC is not None:
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

    blockID = Column('observing_blocks_blockID', Integer,
                      ForeignKey('observing_blocks.blockID'), nullable=True)
    observing_block = relationship("ObservingBlock", back_populates="pointings", uselist=False)

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
        template = ("Pointing(pointingID={}, status='{}', " +
                    "objectName={}, ra={}, decl={}, rank={}, " +
                    "minAlt={}, maxSunAlt={}, minTime={}, maxMoon={}, minMoonSep={}, " +
                    "ToO={}, startUTC={}, stopUTC={}, startedUTC={}, stoppedUTC={}, " +
                    "userKey={}, mpointingID={}, blockID={}, " +
                    "eventID={}, eventTileID={}, surveyID={}, surveyTileID={})")
        return template.format(
            self.pointingID, self.status, self.objectName, self.ra, self.decl, self.rank,
            self.minAlt, self.maxSunAlt, self.minTime, self.maxMoon, self.minMoonSep,
            bool(self.ToO), self.startUTC, self.stopUTC, self.startedUTC, self.stoppedUTC,
            self.userKey, self.mpointingID, self.blockID,
            self.eventID, self.eventTileID, self.surveyID, self.surveyTileID
        )


class ObservingBlock(Base):

    """
    A class to represent a block of observing time.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create a ObservingBlock, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the blockID)
    will be None until the ObservingBlock is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Note you shouldn't ever have to manually create ObservingBlocks.
    They're used internally by Mpointings to keep track of Pointings and all
    the ones an Mpointing requires are created when the Mpointing is.

    Args
    ----
        blockNum : int
            an integer indicating which block in a sequence this is
        valid_time : float
            amount of time a pointing in this block should stay valid in the queue, in minutes.
        wait_time : float
            time to wait after this block before allowing the next pointing, in minutes

        mpointingID : int, optional
            unique key linking this ObservingBlock to an Mpointing
        current : bool, optional
            True if this ObservingBlock is the one that is currently linked to
            a Pointing in the queue

    An ObservingBlock also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        pointings : list of `Pointing`
            a list of any `Pointing`s associated with this block, if any

    Examples
    --------

        >>> from gtecs.database import *

        make an ObservingBlock
        (an Mpointing with mpointingID = 1 is already in the database)

        >>> b = ObservingBlock(blockNum=1, valid_time=60, wait_time=120, mpointingID=1)
        >>> session = load_session()
        >>> session.add(b)
        ObservingBlock(blockID=7, blockNum=1, valid_time=60.0, wait_time=120.0, current=False, mpointingID=1)

    """

    __tablename__ = "observing_blocks"

    blockID = Column(Integer, primary_key=True)
    blockNum = Column(Integer)
    valid_time = Column(Integer)
    wait_time = Column(Integer)
    current = Column(Integer, default=False)
    mpointingID = Column('mpointings_mpointingID', Integer,
                         ForeignKey('mpointings.mpointingID'),
                         nullable=False)

    # relationships
    mpointing = relationship("Mpointing", back_populates="observing_blocks", uselist=False)
    pointings = relationship("Pointing", back_populates="observing_block")

    def __repr__(self):
        template = ("ObservingBlock(blockID={}, blockNum={}, valid_time={}, " +
                    "wait_time={}, current={}, mpointingID={})")
        return template.format(self.blockID, self.blockNum, self.valid_time,
                               self.wait_time, bool(self.current), self.mpointingID)


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
        minMoonSep : float
            distance constraint from the Moon, degrees
        ToO : int
            0 or 1 to indicate if this is a ToO or not
        num_todo : int
            number of (sucsessful) observations required.
            less than zero means repeat infinitely.
        valid_time : float or list of float
            the amount of time the pointing(s) should be valid in the queue.
            if num_todo is greater than times given the list will be looped.
        wait_time : float or list of float
            time to wait between pointings in minutes.
            if num_todo is greater than times given the list will be looped.

        status : string, optional
            status of mpointing, default 'unscheduled'
        startUTC : string, `astropy.time.Time` or datetime.datetime, optional
            UTC time from which Mpointing is considered valid and can be started
            if not given then set to now, so the Mpointing will start immediately
        stopUTC : string, `astropy.time.Time` or datetime.datetime, optional
            the latest UTC time after which pointings must stop
            if not given the Mpointing will continue creating pointings until
            it is completed
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
        num_completed : int
            number of successfully completed pointings
        num_remaining : int
            number of pointings still to do (same as num_todo - num_completed)
        exposure_sets : list of `ExposureSet`
            the `ExposureSet` objects associated with this `Mpointing`, if any
        observing_blocks : list of `ObservingBlock`
            the `ObservingBlock` objects associated with this `Mpointing`, if any
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
        >>> from astropy.time import Time

        make an Mpointing - starting at midnight, 5 pointings that stay in the queue
        for 5 minutes and the next one is valid 10 minutes after the previous.

        >>> mp = Mpointing(objectName='M31', ra=22, decl=-5, start_rank=9, minAlt=30, minTime=3600,
        ... maxSunAlt=-15, ToO=0, maxMoon='B', minMoonSep=30, num_todo=5, userKey=24, valid_time=5,
        ... wait_time=10, startUTC=Time('2018-01-01 00:00:00'))
        >>> mp
        Mpointing(mpointingID=None, status='unscheduled', num_todo=5, num_completed=0, num_remaining=5,
        infinite=False, objectName=M31, ra=22, decl=-5, rank=9, start_rank=9, minAlt=30, maxSunAlt=-15,
        minTime=3600, maxMoon=B, minMoonSep=30, ToO=False, startUTC=2018-01-01 00:00:00, stopUTC=None,
        userKey=24, eventID=None, eventTileID=None, surveyID=None, surveyTileID=None)

        Note that the mpointingID is None, because that will be filled out when we add it to the database

        Looking at the ObservingBlocks you can see that we only need one, and it has been generated

        >>> mp.observing_blocks
        [ObservingBlock(blockID=None, blockNum=1, valid_time=5, wait_time=10, current=1, mpointingID=None)]

        This block will be repeated 5 times, as requested.

        ~~~~~~~~~~~~~~~~~~~~~

        For a more complicated example, give a list to wait_time to have the intervals between pointings increase.

        >>> mp = Mpointing(objectName='M31', ra=22, decl=-5, start_rank=9, minAlt=30, minTime=3600,
        ... maxSunAlt=-15, ToO=0, maxMoon='B', minMoonSep=30, num_todo=5, userKey=24, valid_time=5,
        ... wait_time=[10,20,30], startUTC=Time('2018-01-01 00:00:00'))
        >>> mp
        Mpointing(mpointingID=None, status='unscheduled', num_todo=5, num_completed=0, num_remaining=5,
        infinite=False, objectName=M31, ra=22, decl=-5, rank=9, start_rank=9, minAlt=30, maxSunAlt=-15,
        minTime=3600, maxMoon=B, minMoonSep=30, ToO=False, startUTC=2018-01-01 00:00:00, stopUTC=None,
        userKey=24, eventID=None, eventTileID=None, surveyID=None, surveyTileID=None)
        >>> mp.observing_blocks
        [ObservingBlock(blockID=None, blockNum=1, valid_time=5, wait_time=10, current=1, mpointingID=None),
         ObservingBlock(blockID=None, blockNum=2, valid_time=5, wait_time=20, current=None, mpointingID=None),
         ObservingBlock(blockID=None, blockNum=3, valid_time=5, wait_time=30, current=None, mpointingID=None)]

        Note this time that we only created 3 blocks, but are asking for 5 observations.
        That's not a problem, as the blocks will simply repeat.
        In this case the sequence will look like this:
        [note we set the Mpointing start time to midnight]
        Pointing 1: startUTC=00:00, stopUTC=00:05 (valid for 5 minutes)
        Pointing 2: startUTC=00:15, stopUTC=00:20 (wait for 10, then valid for 5)
        Pointing 3: startUTC=00:40, stopUTC=00:45 (wait for 20, then valid for 5)
        Pointing 4: startUTC=01:15, stopUTC=01:20 (wait for 30, then valid for 5)
        Pointing 5: startUTC=01:30, stopUTC=01:35 (wait for 10, then valid for 5)

        We can see what would happen by manually running through the pointings and pretending to be the caretaker.

        First add the Mpointing to the database:

        >>> s = load_session()
        >>> s.add(mp)
        >>> s.commit()

        Then create the first pointing and add it to the database.

        >>> p = mp.get_next_pointing()
        >>> s.add(p)
        >>> s.commit()
        >>> mp.pointings
        [Pointing(pointingID=17100, status='pending', objectName=M31, ra=22.0, decl=-5.0, rank=9, minAlt=30.0,
        maxSunAlt=-15.0, minTime=3600.0, maxMoon=B, minMoonSep=30.0, ToO=False, startUTC=2018-01-01 00:00:00,
        stopUTC=2018-01-01 00:05:00, startedUTC=None, stoppedUTC=None, userKey=24, mpointingID=2, blockID=1,
        eventID=None, eventTileID=None, surveyID=None, surveyTileID=None)]

        Note that the pointingID, mpointingID and blockID have been filled out,
        and this Pointing has blockID=1.
        Also that the start time is midnight and the stop time is 5 past, as expected.
        See what happens if we mark the pointing as completed:

        >>> mp.pointings[0].status = 'completed'
        >>> s.commit()
        >>> mp
        Mpointing(mpointingID=2, status='unscheduled', num_todo=5, num_completed=1, num_remaining=4,
        infinite=False, objectName=M31, ra=22.0, decl=-5.0, rank=19, start_rank=9, minAlt=30.0,
        maxSunAlt=-15.0, minTime=3600.0, maxMoon=B, minMoonSep=30.0, ToO=False, startUTC=2018-01-01 00:00:00,
        stopUTC=None, userKey=24, eventID=None, eventTileID=None, surveyID=None, surveyTileID=None)

        See that the num_completed atribute has gone up, the num_remaining has gone down
        and the mpointing status has changed to 'unscheduled'.

        Create the next pointing and add it:

        >>> p = mp.get_next_pointing()
        >>> s.add(p)
        >>> s.commit()
        >>> p
        Pointing(pointingID=17102, status='pending', objectName=M31, ra=22.0, decl=-5.0, rank=19, minAlt=30.0,
        maxSunAlt=-15.0, minTime=3600.0, maxMoon=B, minMoonSep=30.0, ToO=False, startUTC=2018-01-01 00:15:00,
        stopUTC=2018-01-01 00:20:00, startedUTC=None, stoppedUTC=None, userKey=24, mpointingID=2, blockID=2,
        eventID=None, eventTileID=None, surveyID=None, surveyTileID=None)
        >>> mp
        Mpointing(mpointingID=2, status='scheduled', num_todo=5, num_completed=1, num_remaining=4, infinite=False,
        objectName=M31, ra=22.0, decl=-5.0, rank=19, start_rank=9, minAlt=30.0, maxSunAlt=-15.0, minTime=3600.0,
        maxMoon=B, minMoonSep=30.0, ToO=False, startUTC=2018-01-01 00:00:00, stopUTC=None, userKey=24, eventID=None,
        eventTileID=None, surveyID=None, surveyTileID=None)

        See that the Mpointing is back to scheduled.
        Also, note that the new pointing has start time of 00:15 and stop of 00:20. That's as we expected, because
        it's linked to the second observing block not the first (see blockID=2).

        If you look at the observing blocks you can see the next one is now marked as current.

        >>> mp.observing_blocks
        [ObservingBlock(blockID=1, blockNum=1, valid_time=5.0, wait_time=10.0, current=0, mpointingID=2),
        ObservingBlock(blockID=2, blockNum=2, valid_time=5.0, wait_time=20.0, current=1, mpointingID=2),
        ObservingBlock(blockID=3, blockNum=3, valid_time=5.0, wait_time=30.0, current=0, mpointingID=2)]

        Mark this one as completed, and you'll see the Mpointing is updated.

        >>> p.status = 'completed'
        >>> s.commit()
        >>> mp.num_completed
        2
        >>> mp.status
        'unscheduled'

        Let's run through the remaining pointings:

        >>> p = mp.get_next_pointing() # Pointing 3 of 5
        >>> s.add(p)
        >>> s.commit()
        >>> p.blockID
        3
        >>> mp.status
        'scheduled'

        >>> p.status = 'completed'
        >>> s.commit()
        >>> mp.num_completed
        3
        >>> mp.status
        'unscheduled'

        >>> p = mp.get_next_pointing() # Pointing 4 of 5
        >>> s.add(p)
        >>> s.commit()
        >>> p.blockID
        1
        >>> mp.status
        'scheduled'

        >>> p.status = 'completed'
        >>> s.commit()
        >>> mp.num_completed
        4
        >>> mp.status
        'unscheduled'

        >>> p = mp.get_next_pointing() # Pointing 5 of 5
        >>> s.add(p)
        >>> s.commit()
        >>> p.blockID
        2
        >>> mp.status
        'scheduled'
s
        >>> p.status = 'completed'
        >>> s.commit()
        >>> mp.num_completed
        5
        >>> mp.status
        'completed'
        >>> mp
        Mpointing(mpointingID=2, status='completed', num_todo=5, num_completed=5, num_remaining=0,
        infinite=False, objectName=M31, ra=22.0, decl=-5.0, rank=59, start_rank=9, minAlt=30.0,
        maxSunAlt=-15.0, minTime=3600.0, maxMoon=B, minMoonSep=30.0, ToO=False, startUTC=2018-01-01 00:00:00,
        stopUTC=None, userKey=24, eventID=None, eventTileID=None, surveyID=None, surveyTileID=None)

        And the Mpointing is completed.

        ~~~~~~~~~~~~~~~~~~~~~

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
        [ExposureSet(expID=126598, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=L, expTime=20.0, numexp=20, binning=2, utMask=None, pointingID=None, mpointingID=1),
         ExposureSet(expID=126599, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=G, expTime=20.0, numexp=20, binning=2, utMask=None, pointingID=None, mpointingID=1),
         ExposureSet(expID=126600, raoff=0.0, decoff=0.0, typeFlag=SCIENCE, filt=R, expTime=20.0, numexp=20, binning=2, utMask=None, pointingID=None, mpointingID=1)]

        These exposure sets will be copied to the Pointings when they're created.

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
    minMoonSep = Column(Float)
    ToO = Column(Integer)
    startUTC = Column(DateTime)
    stopUTC = Column(DateTime)
    infinite = Column(Integer, default=False)
    num_todo = Column(Integer)
    num_completed = Column(Integer)
    status = Column(mpointing_status_list, default='unscheduled')

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

    observing_blocks = relationship("ObservingBlock", back_populates="mpointing", viewonly=True)

    def __repr__(self):
        template = ("Mpointing(mpointingID={}, status='{}', num_todo={}, num_completed={}, num_remaining={}, infinite={}, " +
                    "objectName={}, ra={}, decl={}, rank={}, start_rank={}, " +
                    "minAlt={}, maxSunAlt={}, minTime={}, maxMoon={}, minMoonSep={}, " +
                    "ToO={}, startUTC={}, stopUTC={}, " +
                    "userKey={}, eventID={}, eventTileID={}, surveyID={}, surveyTileID={})")
        return template.format(
            self.mpointingID, self.status, self.num_todo, self.num_completed,
            self.num_remaining, bool(self.infinite),
            self.objectName, self.ra, self.decl, self.rank, self.start_rank,
            self.minAlt, self.maxSunAlt, self.minTime, self.maxMoon, self.minMoonSep,
            bool(self.ToO), self.startUTC, self.stopUTC,
            self.userKey, self.eventID, self.eventTileID, self.surveyID, self.surveyTileID
        )

    def __init__(self, objectName=None, ra=None, decl=None,
                 start_rank=None, minAlt=None, minTime=None,
                 maxMoon=None, minMoonSep=None, maxSunAlt=None, ToO=None, startUTC=Time.now(),
                 stopUTC=None, num_todo=None, valid_time=None, wait_time=None,
                 status='unscheduled', **kwargs):
        self.ra = ra
        self.decl = decl
        self.objectName = objectName
        self.start_rank = start_rank
        self.maxMoon = maxMoon
        self.minMoonSep = minMoonSep
        self.minAlt = minAlt
        self.minTime = minTime
        self.maxSunAlt = maxSunAlt
        self.ToO = ToO
        self.startUTC = startUTC
        self.stopUTC = stopUTC
        self.rank = self.start_rank
        self.status = status
        self.infinite = False
        self.num_todo = num_todo
        self.num_completed = 0

        # now add observing blocks
        if valid_time is not None and wait_time is not None:

            # first convert to lists
            if not isinstance(valid_time, list):
                valid_times = [valid_time]
            else:
                valid_times = valid_time
            if not isinstance(wait_time, list):
                wait_times = [wait_time]
            else:
                wait_times = wait_time

            # check if infinite
            if num_todo < 0:
                self.infinite = True
                self.num_todo = -1

            # create ObservingBlock objects
            for i in range(max(len(valid_times), len(wait_times))):
                valid = valid_times[i%len(valid_times)]
                wait = wait_times[i%len(wait_times)]

                # check if non-expiring
                if valid < 0:
                    valid = -1

                block = ObservingBlock(blockNum=i+1, valid_time=valid, wait_time=wait)
                self.observing_blocks.append(block)
            if len(self.observing_blocks):
                self.observing_blocks[0].current=1

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


    @validates('startUTC', 'stopUTC')
    def munge_times(self, key, field):
        """
        Use validators to allow various types of input for UTC
        Also enforce stopUTC > startUTC
        NB stopUTC is None be default
        """
        if key == 'stopUTC' and field is None:
            value = None
        elif isinstance(field, datetime.datetime):
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
        elif key == 'stopUTC' and value is not None and self.startUTC is not None:
            if Time(self.startUTC) >= Time(value):
                raise AssertionError("stopUTC must be later than startUTC")

        return value


    @property
    def num_remaining(self):
        if self.infinite:
            return -1
        else:
            return self.num_todo - self.num_completed


    def get_current_block(self):
        """
        Return the current observing block.

        Assumes this object is still associated to an active session.

        Returns
        -------
        current : `gtecs.database.ObservingBlock`
            The current observing block (may be None).
        """
        current_block = [block for block in self.observing_blocks if block.current]
        if len(current_block) == 0:
            return None
        elif len(current_block) > 1:
            raise ValueError('Multiple observing blocks marked as current')
        else:
            return current_block[0]


    def get_last_block(self):
        """
        Return the last observing block executed.

        Assumes this object is still associated to an active session.

        Returns
        -------
        last : `gtecs.database.ObservingBlock`
            The last block done (may be None).
        """
        current_block = self.get_current_block()
        if not current_block:
            return None

        current_num = current_block.blockNum
        if current_num == 1:
            last_num = len(self.observing_blocks)
        else:
            last_num = current_num - 1

        last_block = [block for block in self.observing_blocks
                      if block.blockNum == last_num][0]
        return last_block


    def get_next_block(self):
        """
        Return the next observing block to be executed.

        Assumes this object is still associated to an active session.

        Returns
        -------
        next : `gtecs.database.ObservingBlock`
            The next block to do after the current one (may be None).
        """
        current_block = self.get_current_block()
        if not current_block and self.num_completed > 0:
            # no current block (for some reason, aside from just starting)
            return None

        elif not current_block or len(current_block.pointings) == 0:
            # just starting
            next_num = 1

        elif self.num_remaining == 0:
            # current block is the last one
            return None

        else:
            current_num = current_block.blockNum
            latest_pointing = current_block.pointings[-1]
            if latest_pointing.status in ['completed', 'expired']:
                next_num = (current_num % len(self.observing_blocks)) + 1
            else:
                next_num = current_num

        next_block = [block for block in self.observing_blocks
                      if block.blockNum == next_num][0]
        return next_block


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

        # already scheduled or finished, return None
        if self.status != 'unscheduled':
            return None

        # all the observations have been completed
        if self.num_remaining == 0:
            return None

        current_block = self.get_current_block()
        next_block = self.get_next_block()

        # no current block or pointings, should only happen the first time
        if not current_block or len(current_block.pointings) == 0:
            startUTC = self.startUTC
        else:
            latest_pointing = current_block.pointings[-1]
            # decide to go onto the next block:
            #  - if the last block's pointing was completed
            #  - if the last block's pointing's valid time has expired
            if latest_pointing.status in ['completed', 'expired']:
                if latest_pointing.stopUTC:
                    startUTC = Time(latest_pointing.stopUTC) + current_block.wait_time * u.minute
                else:
                    # non-expiring pointings have no stopUTC
                    startUTC = Time.now() + current_block.wait_time * u.minute
            else:
                # the current block wasn't completed, and there's still time left
                # (e.g. aborted, interrupted)
                # need to re-insert the current block with a new pointing
                startUTC = latest_pointing.startUTC

        if next_block.valid_time < 0:
            # non-expiring pointings
            stopUTC = None
        else:
            stopUTC = Time(startUTC) + next_block.valid_time * u.minute
            if self.stopUTC and stopUTC > self.stopUTC:
                # force pointings to stop by an Mpointing's stopUTC, if given
                stopUTC = self.stopUTC

            if startUTC >= stopUTC:
                # can happen if the Mpointing has a stopUTC
                return None

        # now create a pointing
        p = Pointing(objectName=self.objectName,
                     ra=self.ra,
                     decl=self.decl,
                     rank=self.rank,
                     minAlt=self.minAlt,
                     maxSunAlt=self.maxSunAlt,
                     minTime=self.minTime,
                     maxMoon=self.maxMoon,
                     minMoonSep=self.minMoonSep,
                     ToO=self.ToO,
                     startUTC=startUTC,
                     stopUTC=stopUTC,
                     status='pending',
                     userKey=self.userKey,
                     mpointingID=self.mpointingID,
                     observing_block=next_block,
                     eventID=self.eventID,
                     eventTileID=self.eventTileID,
                     surveyID=self.surveyID,
                     surveyTileID=self.surveyTileID,
                     )
        # add the exposures
        p.exposure_sets = self.exposure_sets
        return p


class ImageLog(Base):

    """
    A class to store a record of a FITS image file created by the camera daemon.

    The ImageLog is a simple way to link pointings in the database to physical
    FITS files. An ImageLog should be created by the camera daemon each time
    an exposure is taken, even if taken manually rather than originating from
    the database.

    Like all SQLAlchemy model classes, this object links to the
    underlying database. You can create an ImageLog, and set its attributes
    without a database session. Accessing some attributes may require
    an active database session, and some properties (like the logID)
    will be None until the log is added to the database.

    The constructor must use keyword arguments and the arguments below
    should be supplied or set before insertion into the database.
    See Examples for details.

    Args
    ----
        filename : string
            the name of the image file
        runNumber : int
            the run ID number for this exposure
        ut : int
            the unit telescope this frame was captured on
        utMask : int
            a binary mask for which unit telescopes carried out this exposure.
            A value of 5 (binary 0101) will have been exposed by cameras 1 and 3.
        startUTC : string, `astropy.time.Time` or datetime.datetime
            the time that the exposure began
        writeUTC : string, `astropy.time.Time` or datetime.datetime
            the time that the image file was written

        set_position : int, optional
            position of this exposure in a set, if it's in one
            if not, it will default to 1
        set_total : int, optional
            total number of exposures in this set, if any
            if not given, it will default to 1
        expID : int, optional
            the unique key of the exposure set this frame was part of
        pointingID : int, optional
            the unique key of the pointing which generated this frame
        mpointingID : int, optional, optional
            unique key of the mpointing which generated the pointing which
            generated this frame

    An ImageLog also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        logID : int
            primary key for image logs
        exposure_set : `ExposureSet`
            the `ExposureSet` object associated with this `ImageLog`, if any
        pointing : `Pointing`
            the `Pointing` associated with this `ImageLog`, if any
        mpointing : `Mpointing`
            the `Mpointing` associated with this `ImageLog`, if any
    """
    __tablename__ = "image_logs"

    logID = Column(Integer, primary_key=True)
    filename = Column(String)
    runNumber = Column(Integer)
    ut = Column(Integer)
    utMask = Column(Integer)
    startUTC = Column(DateTime)
    writeUTC = Column(DateTime)
    set_position = Column(Integer, default=1)
    set_total = Column(Integer, default=1)

    expID = Column('exposure_sets_expID', Integer,
                    ForeignKey('exposure_sets.expID'), nullable=True)
    exposure_set = relationship("ExposureSet", backref="image_logs",
                   uselist=False)

    pointingID = Column('pointings_pointingID', Integer,
                    ForeignKey('pointings.pointingID'), nullable=True)
    pointing = relationship("Pointing", backref="image_logs", uselist=False)

    mpointingID = Column('mpointings_mpointingID', Integer,
                    ForeignKey('mpointings.mpointingID'), nullable=True)
    mpointing = relationship("Mpointing", backref="image_logs", uselist=False)


    def __repr__(self):
        template = ("ImageLog(logID={}, filename={}, runNumber={}, " +
                    "ut={}, utMask={}, startUTC={}, writeUTC={}, " +
                    "expID={}, pointingID={}, mpointingID={})")
        return template.format(
            self.logID, self.filename, self.runNumber, self.ut, self.utMask,
            self.startUTC, self.writeUTC, self.expID, self.pointingID,
            self.mpointingID
        )

    @validates('startUTC', 'writeUTC')
    def munge_times(self, key, field):
        """
        Use validators to allow various types of input for UTC
        Also enforce writeUTC > startUTC
        """
        if isinstance(field, datetime.datetime):
            value = field.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(field, Time):
            field.precision = 0  # no D.P on seconds
            value = field.iso
        else:
            # just hope the string works!
            value = str(field)

        if (key == 'startUTC' and self.writeUTC is not None):
            if Time(value) >= Time(self.writeUTC):
                raise AssertionError("writeUTC must be later than startUTC")
        elif key == 'writeUTC' and self.writeUTC is not None:
            if Time(self.startUTC) >= Time(value):
                raise AssertionError("writeUTC must be later than startUTC")

        return value
