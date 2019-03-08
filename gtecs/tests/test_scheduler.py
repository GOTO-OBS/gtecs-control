"""Test code: goes through a night and returns the current best pointing.

NOTE pointings are never completed so are never removed from the queue folder.
"""

import sys

import astropy.units as u
from astropy.time import Time

from gtecs import scheduler


write_html = 0
if len(sys.argv) > 1:
    write_html = bool(sys.argv[1])

now = Time('2016-08-31 22:00')
while True:
    now = now + 5 * 60 * u.second
    print(now)
    new_pointing = scheduler.check_queue(now, write_html)
    if new_pointing is not None:
        new_id = new_pointing.db_id
        print('   pointing is', new_id)
    else:
        print('   nothing to do, parking')
