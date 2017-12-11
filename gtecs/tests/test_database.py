"""
Demonstrate the use of the database modules.
Assume we start from a clean database.
"""

import time

from astropy import units as u
from astropy.time import Time

from gtecs.database import *
from gtecs.database import _make_random_pointing


# add a user
with open_session() as session:
    add_user(session, 'sl', 'dowhhvs5a', "Stuart Littlefair")

    # create an event
    e = Event(ivo='ivo://pt5mTest', name='pt5mVar3', source='pt5m')

    # and an event tile
    et = EventTile(ra=100, decl=20, probability=0.1)
    et.event = e

    # and a survey
    s = Survey(name='GOTO survey')

    # and a survey tile
    st = SurveyTile(ra=22, decl=-2, name='Tile1')
    st.survey = s

    # and an event tile linked to that survey tile
    et2 = EventTile(probability=0.2)
    et2.event = e
    et2.surveyTile = st

    # add them
    insert_items(session, [e, et, s, st, et2])
    session.commit()

    # check the second event tile updated correctly
    assert et2.ra == st.ra
    assert et2.decl == st.decl

# OK, new Session. Let's make a Pointing
with open_session() as session:
    userKey = get_userkey(session, 'sl')
    p = Pointing(objectName='IP Peg', ra=350.785625, decl=18.416472, rank=9, minAlt=30, maxSunAlt=-15,
                 minTime=3600, maxMoon='G', ToO=0, startUTC=Time.now(),
                 stopUTC=Time.now()+3*u.day, userKey=userKey)
    e = ExposureSet(typeFlag='SCIENCE', filt='G', expTime=20, numexp=20, binning=2)
    p.exposure_sets.append(e)
    session.add(p)

# new session, add random pointings
with open_session() as session:
    pointings = [_make_random_pointing(userKey) for i in range(10)]
    insert_items(session, pointings)

    more_pointings = [_make_random_pointing(userKey) for i in range(10)]
    insert_items(session, more_pointings)

# now check how many we have
with open_session() as session:
    npoints = len(get_pointings(session))
    current, pending = get_queue(session)
    stale = get_stale_pointing_ids(session)
    nqueue = len(pending)
print("{} points in database".format(npoints))
print("{} points in queue".format(nqueue))
print("{} stale pointings\n".format(len(stale)))

# clean stale pointings
print('Cleaning stale pointings')
with open_session() as session:
    bulk_update_pointing_status(session, stale, 'expired')

# now check how many we have
with open_session() as session:
    npoints = len(get_pointings(session))
    current, pending = get_queue(session)
    stale = get_stale_pointing_ids(session)
    expired = get_pointings(session, status='expired')
    nqueue = len(pending)
print("{} points in database".format(npoints))
print("{} points in queue".format(nqueue))
print("{} stale pointings".format(len(stale)))
print("{} expired pointings\n".format(len(expired)))

# create an Mpointing
mp = Mpointing(objectName='M31', ra=10.685, decl=41.2875, start_rank=9, minAlt=30,
               minTime=3600, ToO=0, maxMoon='B', num_repeats = 5, userKey=24,
               intervals=1440, valid_durations=1400, maxSunAlt=-15)
# and add RGBL exposure set
L = ExposureSet(typeFlag='SCIENCE', filt='L', expTime=120, numexp=3, binning=2)
R = ExposureSet(typeFlag='SCIENCE', filt='R', expTime=120, numexp=3, binning=2)
G = ExposureSet(typeFlag='SCIENCE', filt='G', expTime=120, numexp=3, binning=2)
B = ExposureSet(typeFlag='SCIENCE', filt='B', expTime=120, numexp=3, binning=2)
mp.exposure_sets = [L, R, G, B]
with open_session() as s:
    s.add(mp)

time.sleep(5)

# now let's go through a caretaker step
with open_session() as s:
    # first remove stale pointings
    stale = get_stale_pointing_ids(s)
    if len(stale) > 0:
        bulk_update_pointing_status(s, stale, 'expired')
    print('Marked {} pointings as stale'.format(len(stale)))

    # which Mpointings need pointings submitting?
    mps_to_schedule = get_mpointings(s, only_active=True, scheduled=False)
    print('There are {} Mpointings to schedule'.format(len(mps_to_schedule)))

    pointings_to_add = []
    for mp in mps_to_schedule:
        print('Scheduling mp #{}'.format(mp.mpointingID))
        next_pointing = mp.get_next_pointing()
        if next_pointing:
            pointings_to_add.append(next_pointing)
    insert_items(s, pointings_to_add)

# check that scheduling worked the way we expected
def summary(mp):
    if mp.scheduled:
        print('Scheduled, num_remain = {}'.format(mp.num_remain))
    else:
        print('Unscheduled, num_remain = {}'.format(mp.num_remain))
    print([p.status for p in mp.pointings])
    print([(r.repeatNum, r.status) for r in mp.repeats], '\n')

with open_session() as s:
    mp = get_mpointing_by_id(s, 1)
    summary(mp)

# now lets pretend we're observing the repeat object and check the triggers
s = load_session()
mp = get_mpointing_by_id(s, 1)
keepGoing = True
while keepGoing:
    print('marking job as running')
    mp.pointings[-1].status = 'running'
    s.commit()
    time.sleep(1)
    summary(mp)

    print('marking as completed')
    mp.pointings[-1].status = 'completed'
    s.commit()
    time.sleep(1)
    summary(mp)

    # schedule next
    next = mp.get_next_pointing()
    if next:
        s.add(next)
    else:
        keepGoing = False
    s.commit()
    time.sleep(1)
    summary(mp)
s.close()

with open_session() as s:
    # check the list of mps to schedule is empty
    mps_to_schedule = get_mpointings(s, only_active=True, scheduled=False)
    print('There are {} Mpointings to schedule'.format(len(mps_to_schedule)))

    npoints = len(get_pointings(s))
    current, pending = get_queue(s)
    stale = get_stale_pointing_ids(s)
    expired = get_pointings(s, status='expired')
    nqueue = len(pending)
print("{} points in database".format(npoints))
print("{} points in queue".format(nqueue))
print("{} stale pointings".format(len(stale)))
print("{} expired pointings\n".format(len(expired)))
