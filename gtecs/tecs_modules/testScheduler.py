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
from astropy.time import Time
from astropy import units as u
import numpy as np


# dummmy scheduler
class Scheduler:
    def __init__(self):
        self.jobs = (job for job in (['jobID 1', 5],
                                     ['jobID 2', 10],
                                     ['jobID 3', 20],
                                     ['jobID 4', 5],
                                     ['jobID 5', 10],
                                     ['jobID 6', 4]))
        self.last_called = Time.now()
        self.wait_time = 0*u.s
        self.currJob = [None, None]

    def __call__(self):
        time_check = Time.now() - self.last_called > self.wait_time
        # there's a 5% chance that a ToO occurs at any call
        too = np.random.rand() > 0.95
        if time_check or too:
            try:
                self.currJob = next(self.jobs)
                self.wait_time = (self.currJob[1] + 1)*u.s
            except StopIteration:
                self.currJob = [None, None]
                self.wait_time = 10*u.s
            self.last_called = Time.now()
        return self.currJob
