"""
observe [pID] [minTime]
Script to control observing a single pointing
"""

import sys
import time

from obsdb import (markJobCompleted, markJobAborted, markJobRunning,
                   open_session, get_pointing_by_id)

from gtecs import params
from gtecs.misc import (execute_command as cmd, neatCloser,
                        ut_mask_to_string, ut_string_to_list)
from gtecs.observing import (wait_for_exposure_queue, prepare_for_images,
                             goto, wait_for_telescope)


class Closer(neatCloser):
    """
    A class to neatly handle shutdown requests.
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
    command_template = 'exq multimage {numexp} {tels}{expTime:.1f} {filt} {binning} "{objectName}" SCIENCE {expID}'
    commands = []
    with open_session() as session:
        pointing = get_pointing_by_id(session, pointingID)
        for exposure_set in pointing.exposure_sets:
            keywords = pointing.__dict__.copy()
            keywords.update(exposure_set.__dict__)
            if exposure_set.utMask is not None:
                utString = ut_mask_to_string(exposure_set.utMask)
                utList = ut_string_to_list(utString)
                keywords['tels'] = ','.join([str(i) for i in utList]) + ' '
            else:
                keywords['tels'] = ''
            commands.append(command_template.format(**keywords))
    return commands


def run(pID, minTime):
    closer = Closer(pID, pID)

    try:
        # make sure hardware is ready
        prepare_for_images()

        print('Observing pointingID: ', pID)
        markJobRunning(pID)

        # clear & pause queue to make sure
        cmd('exq clear')
        cmd('exq pause')

        # start slew
        print('Moving to target')
        goto(*get_position(pID))

        print('Adding commands to exposure queue')
        exq_command_list = get_exq_commands(pID)
        for exq_command in exq_command_list:
            cmd(exq_command)

        # wait for telescope (timeout 120s)
        time.sleep(10)
        wait_for_telescope(120)

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


if __name__ == "__main__":
    pID = int(sys.argv[1])
    minTime = int(sys.argv[2])
    run(pID, minTime)
