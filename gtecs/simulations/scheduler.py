"""A fake scheduler class to use with simulations."""

from astropy import units as u
from astropy.time import Time

import numpy as np

import obsdb as db


class FakeScheduler(object):
    """A fake scheduler for testing.

    Initialised with a list of pointing IDs and their durations,
    the Scheduler class can be called to get the next pointing.

    If the current pointing has elapsed, a new pointing ID is returned.

    With each call, there is a finite chance a ToO occurs,
    and a new pointing ID is returned, even if the current pointing has
    not elapsed.
    """

    def __init__(self):
        self.last_called = Time.now()
        self.current_id = None
        self.current_mintime = None
        self.current_priority = None

    def __call__(self):
        """Return a random pointing from the queue."""
        wait_time = np.random.uniform(20, 120)
        time_check = (Time.now() - self.last_called) > wait_time * u.s
        if time_check:
            self.last_called = Time.now()
            with db.open_session() as s:
                curr, pointings = db.get_queue(s)
                new = np.random.choice(pointings)
                self.current_id = new.db_id
                self.current_mintime = new.min_time
                self.current_priority = new.rank
        return self.current_id, self.current_mintime, self.current_priority
