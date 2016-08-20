"""
Dummy helper script to simulate observing.

Simply logs the fact that it started and whether
it completed or was killed.
"""
from __future__ import absolute_import
from __future__ import print_function
import sys
import time

import numpy as np

from gtecs.tecs_modules.misc import neatCloser
from gtecs.database import markJobCompleted


class Closer(neatCloser):
    """
    A class to neatly handle shutdown requests.

    We mark the job as aborted
    """
    def __init__(self, taskName, jobID):
        super().__init__(taskName)
        self.jobID = jobID

    def tidyUp(self):
        print('Received cancellation of job {}'.format(self.jobID))


if __name__ == "__main__":

    pID = int(sys.argv[1])
    minTime = int(sys.argv[2])
    closer = Closer(pID, pID)
    print('Observing pointingID: ', pID)

    extra_time = np.random.uniform(10,20)
    time.sleep(minTime + extra_time)

    # hey, if we got here no-one else will mark as completed
    markJobCompleted(pID)
    print('Pointing {} completed'.format(pID))
