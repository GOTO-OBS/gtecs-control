#!/usr/bin/env python
"""Script to control observing a single pointing.

observe [pointing_id]
"""

import sys
import time

from gtecs.misc import NeatCloser, execute_command, ut_mask_to_string, ut_string_to_list
from gtecs.observing import goto, prepare_for_images, wait_for_exposure_queue, wait_for_telescope

from obsdb import get_pointing_by_id, mark_aborted, mark_completed, mark_running, open_session


class Closer(NeatCloser):
    """A class to neatly handle shutdown requests."""

    def __init__(self, taskname, pointing_id):
        super().__init__(taskname)
        self.pointing_id = pointing_id

    def tidy_up(self):
        """Cancel the job."""
        print('Received cancellation order for job {}'.format(self.pointing_id))


def get_position(pointing_id):
    """Get the RA and Dec of a pointing from its database ID."""
    with open_session() as session:
        pointing = get_pointing_by_id(session, pointing_id)
        ra = pointing.ra
        decl = pointing.decl
    return ra, decl


def get_exq_commands(pointing_id):
    """Get the exposure queue command for a given pointing."""
    command_template = 'exq multimage {numexp} {tels}{expTime:.1f} '\
                       '{filt} {binning} "{objectName}" SCIENCE {db_id}'
    commands = []
    with open_session() as session:
        pointing = get_pointing_by_id(session, pointing_id)
        for exposure_set in pointing.exposure_sets:
            keywords = pointing.__dict__.copy()
            keywords.update(exposure_set.__dict__)
            if exposure_set.utMask is not None:
                ut_string = ut_mask_to_string(exposure_set.utMask)
                ut_list = ut_string_to_list(ut_string)
                keywords['tels'] = ','.join([str(i) for i in ut_list]) + ' '
            else:
                keywords['tels'] = ''
            commands.append(command_template.format(**keywords))
    return commands


def run(pointing_id):
    """Run the observe routine."""
    Closer(pointing_id, pointing_id)

    try:
        # make sure hardware is ready
        prepare_for_images()

        print('Observing pointing ID: ', pointing_id)
        mark_running(pointing_id)

        # clear & pause queue to make sure
        execute_command('exq clear')
        execute_command('exq pause')

        # start slew
        print('Moving to target')
        goto(*get_position(pointing_id))

        print('Adding commands to exposure queue')
        exq_command_list = get_exq_commands(pointing_id)
        for exq_command in exq_command_list:
            execute_command(exq_command)

        # wait for telescope (timeout 120s)
        time.sleep(10)
        wait_for_telescope(120)

        print('In position: starting exposures')
        # resume the queue
        execute_command('exq resume')

        # wait for the queue to empty, no timeout
        wait_for_exposure_queue()

    except Exception:
        # something went wrong
        mark_aborted(pointing_id)
        raise

    # hey, if we got here no-one else will mark as completed
    mark_completed(pointing_id)
    print('Pointing {} completed'.format(pointing_id))


if __name__ == "__main__":
    pointing_id = int(sys.argv[1])
    run(pointing_id)
