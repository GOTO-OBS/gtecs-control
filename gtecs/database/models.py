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

    Args
    ----
        blockNum : int
            an integer indicating which block in a sequence this is
        valid_time : float
            amount of time a pointing in this block should stay valid in the queue, in minutes.
        wait_time : float
            time to wait after this block before allowing the next pointing, in minutes

        mpointingID : int
            unique key linking this repeat to an Mpointing

    An ObservingBlock also has the following properties which are
    populated through database queries, but not needed for
    object creation:

    Attributes
    ----------
        current : bool
            True if this ObservingBlock is the one that is currently linked to
            a Pointing in the queue
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
                               self.wait_time, self.current, self.mpointingID)


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

    NEEDS UPDATING
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

        # now add repeats and intervals
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

        # when created, also create the first pointing
        pointing = self.get_next_pointing()
        self.pointings.append(pointing)
        if pointing:
            self.status = 'scheduled'


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
                # (e.g. aborted, interrrupted)
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
