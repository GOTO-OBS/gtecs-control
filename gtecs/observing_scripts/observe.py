"""
Dummy helper script to simulate observing.

Simply logs the fact that it started and whether
it completed or was killed.
"""
from __future__ import absolute_import
from __future__ import print_function
import sys
import time
from gtecs.tecs_modules.misc import neatCloser, python_command


class Closer(neatCloser):
    """
    An example class to handle shutdown.
    """
    def __init__(self, taskName, jobID):
        super().__init__(taskName)
        self.jobID = jobID

    def tidyUp(self):
        print('Marking {} as cancelled'.format(self.jobID))


if __name__ == "__main__":
    name = sys.argv[1]
    duration = float(sys.argv[2])
    # use closer class to handle interrupts
    closer = Closer(name, name)
    print('Running', name)
    time.sleep(duration)
    print('Task {} completed'.format(name))
