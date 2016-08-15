import datetime

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import (Column, Integer, String, DateTime, Float,
                        ForeignKey, Enum)
from sqlalchemy.orm import relationship, validates, backref

from astropy.time import Time
from astropy import units as u

Base = declarative_base()

# TODO: docstrings


class Event(Base):
    __tablename__ = "events"

    eventID = Column(Integer, primary_key=True)
    ivo = Column(String, unique=True)
    name = Column(String)
    source = Column(String)

    ligoTile = relationship("LigoTile", back_populates="event", uselist=False)

    def __repr__(self):
        return "Event(eventID={}, ivo={}, name={}, source={})".format(
            self.eventID, self.ivo, self.name, self.source
        )


class User(Base):
    __tablename__ = "users"

    userKey = Column(Integer, primary_key=True)
    userName = Column('user_name', String)
    password = Column(String)
    fullName = Column(String)

    def __repr__(self):
        return "User(userKey={}, ivo={}, password={}, fullName={})".format(
            self.userKey, self.userName, self.password, self.fullName
        )


class LigoTile(Base):
    __tablename__ = "ligo_tiles"

    tileID = Column(Integer, primary_key=True)
    ra = Column(Float)
    decl = Column(Float)
    probability = Column(Float)

    # handle relationships
    pointings = relationship("Pointing", back_populates="ligoTile")
    mpointing = relationship("Mpointing", back_populates="ligoTile", uselist=False)

    eventID = Column('events_eventID', Integer, ForeignKey('events.eventID'),
                     nullable=False)
    event = relationship("Event", back_populates="ligoTile", uselist=False)

    def __repr__(self):
        template = ("LigoTile(tileID={}, ra={}, decl={}, " +
                    "probability={}, eventID={})")
        return template.format(
            self.tileID, self.ra, self.decl, self.probability, self.eventID)


class SurveyTile(Base):
    __tablename__ = "survey"

    tileID = Column(Integer, primary_key=True)

    def __repr__(self):
        return "SurveyTile(tileID={})".format(self.tileID)


class Mpointing(Base):
    __tablename__ = "mpointings"

    rpID = Column(Integer, primary_key=True)
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
    num_repeats = Column(Integer)
    num_remain = Column(Integer)
    scheduled = Column(Integer, default=False)

    eventID = Column('events_eventID', Integer, ForeignKey('events.eventID'),
                     nullable=True)
    event = relationship("Event", backref="mpointings")

    userKey = Column('users_userKey', Integer, ForeignKey('users.userKey'),
                     nullable=False)
    user = relationship("User", backref="mpointings", uselist=False)

    ligoTileID = Column('ligo_tiles_tileID', Integer,
                        ForeignKey('ligo_tiles.tileID'), nullable=True)
    ligoTile = relationship("LigoTile", back_populates="mpointing", uselist=False)

    def __repr__(self):
        template = ("Mpointing(rpID={}, objectName={}, ra={}, decl={}, " +
                    "rank={}, start_rank={}, minAlt={}, maxSunAlt={}, " +
                    "minTime={}, maxMoon={}, ToO={}, num_repeats={}, " +
                    "num_remain={}, scheduled={}, eventID={}, userKey={}, ligoTileID={})")
        return template.format(
            self.rpID, self.objectName, self.ra, self.decl, self.rank, self.start_rank,
            self.minAlt, self.maxSunAlt, self.minTime, self.maxMoon, self.ToO,
            self.num_repeats, self.num_remain, self.scheduled, self.eventID,
            self.userKey, self.ligoTileID
        )

    def __init__(self, objectName=objectName, ra=ra, decl=decl,
                 start_rank=start_rank, minAlt=minAlt, minTime=minTime,
                 maxMoon=maxMoon, ToO=ToO, num_repeats=num_repeats,
                 intervals=None, valid_durations=None, **kwargs):
        self.ra = ra
        self.decl = decl
        self.objectName = objectName
        self.start_rank = start_rank
        self.maxMoon = maxMoon
        self.minAlt = minAlt
        self.minTime = minTime
        self.ToO = ToO
        self.num_repeats = num_repeats
        self.rank = self.start_rank
        self.scheduled = False
        self.num_remain = self.num_repeats

        # now add repeats and intervals
        if intervals is not None and valid_durations is not None:

            # first convert to lists
            try:
                if len(intervals) != num_repeats:
                    raise ValueError("number of intervals should match number of repeats or be scalar")
            except TypeError:
                # intervals was scalar
                intervals = [intervals] * num_repeats
            try:
                if len(valid_durations) != num_repeats:
                    raise ValueError("number of durations should match number of repeats or be scalar")
            except TypeError:
                valid_durations = [valid_durations] * num_repeats

            # now add
            repeatNum = 0
            for duration, interval in zip(intervals, valid_durations):
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
        if 'surveyTile' in kwargs:
            self.surveyTile = kwargs['surveyTile']
        if 'survey_tileID' in kwargs:
            self.survey_tileID = kwargs['survey_tileID']

    def get_next_pointing(self, session):
        next_repeat = session.query(Repeat).filter(
            Repeat.mpointingID == self.rpID,
            Repeat.status == 'upcoming').order_by(Repeat.repeatNum).first()
        if next_repeat is None:
            return None

        # get the status of the last repeat. If it was completed,
        # schedule the next_repeat for now+waitTime. Otherwise,
        # schedule it now
        last_repeat_status = session.query(Repeat.status).filter(
            Repeat.mpointingID == self.rpID,
        ).filter(
            ~Repeat.status.in_(['upcoming', 'running'])
        ).order_by(Repeat.repeatNum.desc()).first()

        if last_repeat_status == 'completed':
            startUTC = Time.now() + next_repeat.waitTime * u.minute
        else:
            startUTC = Time.now()
        stopUTC = startUTC + next_repeat.valid_duration * u.minute
        print(startUTC, stopUTC)
        # now create a pointing
        p = Pointing(
            objectName=self.objectName, ra=self.ra,
            decl=self.decl, rank=self.rank, minAlt=self.minAlt,
            maxSunAlt=self.maxSunAlt, minTime=self.minTime, maxMoon=self.maxMoon,
            startUTC=startUTC, stopUTC=stopUTC, ToO=self.ToO, status='pending',
            repeatID=next_repeat.repeatID, userKey=self.userKey, eventID=self.eventID,
            mpointingID=self.rpID, ligoTileID=self.ligoTileID
        )
        return p


status_list = [
    'pending',
    'aborted',
    'completed',
    'running',
    'deleted',
    'upcoming',
    'interrupted',
    'expired'
]


class Repeat(Base):
    __tablename__ = "repeats"

    repeatID = Column(Integer, primary_key=True)
    repeatNum = Column(Integer)
    waitTime = Column(Integer)
    valid_duration = Column(Integer)
    status = Column(Enum(status_list), default='upcoming')
    ts = Column(DateTime)
    mpointingID = Column('mpointings_rpID', Integer,
                         ForeignKey('mpointings.rpID'),
                         nullable=False)

    @validates('waitTime', 'valid_duration')
    def validate_timings(self, key, field):
        if key == 'waitTime' and self.valid_duration is not None:
            if field < self.valid_duration:
                raise AssertionError('waitTime must be > valid_duration')
        elif key == 'valid_duration' and self.waitTime is not None:
            if self.waitTime < field:
                raise AssertionError('waitTime must be > valid_duration')
        return field

    # relationships
    mpointing = relationship("Mpointing",
                             backref=backref("repeats", cascade="all, delete-orphan"),
                             uselist=False)
    pointing = relationship("Pointing", back_populates="repeat", uselist=False)

    def __repr__(self):
        template = ("Repeat(repeatID={}, repeatNum={}, waitTime={}, " +
                    "valid_duration={}, status={}, mpointingID={})")
        return template.format(self.repeatID, self.repeatNum, self.waitTime,
                               self.valid_duration, self.status, self.mpointingID)


class Pointing(Base):
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
    status = Column(Enum(status_list), default='pending')

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

    mpointingID = Column('mpointings_rpID', Integer,
                         ForeignKey('mpointings.rpID'),
                         nullable=True)
    mpointing = relationship("Mpointing", backref="pointings", uselist=False)

    repeatID = Column('repeats_repeatID', Integer,
                      ForeignKey('repeats.repeatID'), nullable=True)
    repeat = relationship("Repeat", back_populates="pointing", uselist=False)

    ligoTileID = Column('ligo_tiles_tileID', Integer,
                        ForeignKey('ligo_tiles.tileID'), nullable=True)
    ligoTile = relationship("LigoTile", back_populates="pointings", uselist=False)

    def __repr__(self):
        template = ("Pointing(pointingID={}, objectName={}, ra={}, decl={}, " +
                    "rank={}, minAlt={}, maxSunAlt={}, " +
                    "minTime={}, maxMoon={}, ToO={}, startUTC={}, " +
                    "stopUTC={}, status={}, eventID={}, userKey={}, " +
                    "mpointingID={}, repeatID={}, ligoTileID={})")
        return template.format(
            self.pointingID, self.objectName, self.ra, self.decl, self.rank,
            self.minAlt, self.maxSunAlt, self.minTime, self.maxMoon, self.ToO,
            self.startUTC, self.stopUTC, self.status, self.eventID,
            self.userKey, self.mpointingID, self.repeatID, self.ligoTileID
        )


class Exposure(Base):
    __tablename__ = "exposures"

    expID = Column(Integer, primary_key=True)
    raoff = Column(Float)
    decoff = Column(Float)
    typeFlag = Column(Enum(['SCIENCE', 'FOCUS', 'DARK', 'BIAS', 'FLAT', 'STD']))
    filt = Column('filter', String(2))
    expTime = Column(Float)
    numexp = Column(Integer)
    binning = Column(Integer)
    unitTelescope = Column(Integer, nullable=True)

    pointingID = Column('pointings_pointingID', Integer,
                        ForeignKey('pointings.pointingID'),
                        nullable=False)
    pointing = relationship("Pointing", backref="exposures", uselist=False)

    mpointingID = Column('mpointings_rpID', Integer,
                         ForeignKey('mpointings.rpID'),
                         nullable=False)
    mpointing = relationship("Mpointing", backref="exposures", uselist=False)

    def __repr__(self):
        template = ("Exposure(expID={}, raoff={}, decoff={}, typeFlag={}, " +
                    "filt={}, expTime={}, numexp={}, binning={}, unitTelescope={}, " +
                    "pointingID={}, mpointingID={})")
        return template.format(
            self.expID, self.raoff, self.decoff, self.typeFlag, self.filt,
            self.expTime, self.numexp, self.binning, self.unitTelescope,
            self.pointingID, self.mpointingID
        )


class ObslogEntry(Base):
    __tablename__ = "obslog"

    obsID = Column(Integer, primary_key=True)
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
                    "temp={}, pointingID={})")
        return template.format(
            self.obsID, self.frame, self.utStart, self.objectName, self.frameType,
            self.ra, self.decl, self.expTime, self.airmass, self.filt, self.binning,
            self.focus, self.temp, self.pointingID
        )
