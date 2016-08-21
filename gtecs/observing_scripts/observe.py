"""
Dummy helper script to simulate observing.

Simply logs the fact that it started and whether
it completed or was killed.
"""
from __future__ import absolute_import
from __future__ import print_function
import sys
import time

from gtecs.tecs_modules.misc import neatCloser
from gtecs.database import (markJobCompleted, markJobAborted,
                            open_session, get_pointing_by_id)
from gtecs.tecs_modules.misc import execute_command as cmd
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          goto, wait_for_telescope)


class Closer(neatCloser):
    """
    A class to neatly handle shutdown requests.

    We mark the job as aborted
    """
    def __init__(self, taskName, jobID):
        super().__init__(taskName)
        self.jobID = jobID

    def tidyUp(self):
        print('Received cancellation order for job {}'.format(self.jobID))


def get_position(pointingID):
    with open_session() as session:
        pointing = get_pointing_by_id(session, pointingID)
        ra = pointing.ra
        decl = pointing.decl
    return ra, decl


def get_exq_commands(pointingID):
    command_template = "exq multimage {numexp} {expTime:.1f} {filt} {binning} {objectName} SCIENCE"
    commands = []
    with open_session() as session:
        pointing = get_pointing_by_id(session, pointingID)
        for exposure in pointing.exposures:
            keywords = pointing.__dict__.copy()
            keywords.update(exposure.__dict__)
            commands.append(command_template.format(**keywords))
    return commands

if __name__ == "__main__":

    pID = int(sys.argv[1])
    minTime = int(sys.argv[2])
    closer = Closer(pID, pID)
    print('Observing pointingID: ', pID)

    try:
        # clear & pause queue to make sure
        cmd('exq clear')
        cmd('exq pause')

        # start slew
        print('Moving to target')
        goto(*get_position(pID))

        print('Adding commands to exposure queue')
        exq_command_list = get_exq_commands(pID)
        for exq_command in exq_command_list:
            print(exq_command)
            cmd(exq_command)

        # wait for telescope (timeout 240s)
        time.sleep(10)
        wait_for_telescope(240)

        print('In position: starting exposures')
        # resume the queue
        cmd('exq resume')

        # wait for the queue to empty, no timeout
        wait_for_exposure_queue()

    except:
        # something went wrong
        markJobAborted(pID)
        raise

    # hey, if we got here no-one else will mark as completed
    markJobCompleted(pID)
    print('Pointing {} completed'.format(pID))
