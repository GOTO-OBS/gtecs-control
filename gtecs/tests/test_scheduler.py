"""
Test code: goes through a night and returns the current best job.
NOTE jobs are never completed so are never removed from the queue folder.
"""
import sys

from astropy.time import Time
import astropy.units as u

from gtecs import scheduler


write_html = 0
if len(sys.argv) > 1:
    write_html = bool(sys.argv[1])

now = Time('2016-08-31 22:00')
while True:
    now = now + 5*60*u.second
    print(now)
    newPointing = scheduler.check_queue(now, write_html)
    if newPointing is not None:
        newID = newPointing.id
        newPriority = newPointing.priority_now
        print('   job is', newID, 'with', newPriority)
    else:
        print('   nothing to do, parking')
