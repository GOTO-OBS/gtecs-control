"""
A fake scheduler for testing.

Initialised with a list of job IDs and their durations,
the Scheduler class can be called to get the next job.

If the current job has elapsed, a new jobID is returned.

With each call, there is a finite chance a ToO occurs,
and a new jobID is returned, even if the current job has
not elapsed.
"""
from __future__ import absolute_import
from __future__ import print_function
from .database import open_session, get_queue

from astropy.time import Time
from astropy import units as u
import numpy as np


# dummmy scheduler
class Scheduler:
    """
    Makes random choices from the queue
    """
    def __init__(self):
        self.last_called = Time.now()
        self.currID = None
        self.currMinTime = None
        self.currPriority = None

    def __call__(self):

        wait_time = np.random.uniform(20, 120)
        time_check = (Time.now() - self.last_called) > wait_time*u.s
        if time_check:
            self.last_called = Time.now()
            with open_session() as s:
                curr, pointings = get_queue(s)
                new = np.random.choice(pointings)
                self.currID = new.pointingID
                self.currMinTime = new.minTime
                self.currPriority = new.rank
        return self.currID, self.currMinTime, self.currPriority
