#!/usr/bin/env python3
"""Script to control observing a single pointing."""

import sys
from argparse import ArgumentParser

from gtecs.misc import NeatCloser, execute_command, ut_mask_to_string, ut_string_to_list
from gtecs.observing import (prepare_for_images, slew_to_radec,
                             wait_for_exposure_queue, wait_for_mount)

from obsdb import get_pointing_by_id, mark_aborted, mark_completed, mark_running, open_session


class Closer(NeatCloser):
    """A class to neatly handle shutdown requests."""

    def __init__(self, taskname, db_id):
        super().__init__(taskname)
        self.db_id = db_id

    def tidy_up(self):
        """Cancel the pointing."""
        print('Received cancellation order for pointing {}'.format(self.db_id))
        mark_aborted(self.db_id)


def get_position(db_id):
    """Get the RA and Dec of a pointing from its database ID."""
    with open_session() as session:
        pointing = get_pointing_by_id(session, db_id)
        ra = pointing.ra
        dec = pointing.dec
    return ra, dec


def get_exq_commands(db_id):
    """Get the exposure queue command for a given pointing."""
    total_time = 0
    commands = []
    with open_session() as session:
        # Load pointing
        pointing = get_pointing_by_id(session, db_id)

        # Loop over all exposure sets
        for exposure_set in pointing.exposure_sets:
            # Store total time
            total_time += (exposure_set.num_exp * exposure_set.exptime)

            # Format UT mask
            if exposure_set.ut_mask is not None:
                ut_string = ut_mask_to_string(exposure_set.ut_mask)
                ut_list = ut_string_to_list(ut_string)
                uts = ','.join([str(i) for i in ut_list]) + ' '
            else:
                uts = ''

            # Format command
            command = 'exq multimage {} {}{:.1f} {} {} "{}" SCIENCE {}'.format(
                exposure_set.num_exp,
                uts,
                exposure_set.exptime,
                exposure_set.filt,
                exposure_set.binning,
                pointing.object_name,
                exposure_set.db_id,
            )

            # Add command to list
            commands.append(command)

    return commands, total_time


def run(db_id):
    """Run the observe routine."""
    Closer(db_id, db_id)

    try:
        # make sure hardware is ready
        prepare_for_images()

        print('Observing pointing ID: ', db_id)
        mark_running(db_id)

        # clear & pause queue to make sure
        execute_command('exq clear')
        execute_command('exq pause')
        execute_command('cam abort')

        # start slew
        print('Moving to target')
        ra, dec = get_position(db_id)
        slew_to_radec(ra, dec)

        print('Adding commands to exposure queue')
        exq_command_list, total_time = get_exq_commands(db_id)
        for exq_command in exq_command_list:
            execute_command(exq_command)

        # wait for the mount to slew (timeout 120s)
        wait_for_mount(ra, dec, timeout=120)

        print('In position: starting exposures')
        # resume the queue
        execute_command('exq resume')

        # wait for the queue to empty
        wait_for_exposure_queue(total_time * 1.5)

    except Exception:
        # something went wrong
        mark_aborted(db_id)
        raise

    # hey, if we got here no-one else will mark as completed
    mark_completed(db_id)
    print('Pointing {} completed'.format(db_id))
    sys.exit(0)


if __name__ == '__main__':
    parser = ArgumentParser(description='Observe the given database pointing.')
    parser.add_argument('db_id', type=int, help='ObsDB pointing ID')
    args = parser.parse_args()

    run(args.db_id)
