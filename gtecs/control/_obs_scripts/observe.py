#!/usr/bin/env python3
"""Script to control observing a single pointing."""

import sys
import time
from argparse import ArgumentParser

from gtecs.control import params
from gtecs.control.daemons import daemon_function
from gtecs.control.focusing import refocus
from gtecs.control.misc import NeatCloser, execute_command, ut_mask_to_string, ut_string_to_list
from gtecs.control.observing import (prepare_for_images, slew_to_radec,
                                     wait_for_exposure_queue, wait_for_mount)
from gtecs.control.scheduling import get_pointing_info, mark_pointing


class InterruptedPointingCloser(NeatCloser):
    """A class to neatly handle shutdown requests."""

    def __init__(self, pointing_id, min_time=None):
        super().__init__(taskname='Script')
        self.pointing_id = pointing_id
        self.min_time = min_time
        self.start_time = time.time()

    def tidy_up(self):
        """Mark the pointing as interrupted."""
        print('Interrupt caught')

        elapsed_time = time.time() - self.start_time
        print('Elapsed time: {:.0f}s'.format(elapsed_time))

        if self.min_time is None:
            # Mark the pointing as interrupted
            mark_pointing(self.pointing_id, 'interrupted')
            print('Pointing {} marked as interrupted'.format(self.pointing_id))
        else:
            if elapsed_time > self.min_time:
                # We observed enough, mark the pointing as completed
                print('Passed min time ({:.0f}s)'.format(self.min_time))
                mark_pointing(self.pointing_id, 'completed')
                print('Pointing {} marked as completed'.format(self.pointing_id))
            else:
                # We didn't observe enough, mark the pointing as interrupted
                print('Did not pass min time ({:.0f}s)'.format(self.min_time))
                mark_pointing(self.pointing_id, 'interrupted')
                print('Pointing {} marked as interrupted'.format(self.pointing_id))


def run(pointing_id):
    """Run the observe routine."""
    # make sure hardware is ready
    prepare_for_images()
    refocus(params.FOCUS_COMPENSATION_TEST, params.FOCUS_COMPENSATION_VERBOSE)

    # Clear & pause queue to make sure
    execute_command('exq clear')
    execute_command('exq pause')
    execute_command('cam abort')

    # Get the Pointing infomation from the scheduler
    pointing_info = get_pointing_info(pointing_id)

    # Mark the Pointing as running
    print('Observing pointing ID: ', pointing_id)
    mark_pointing(pointing_id, 'running')
    print('Pointing {} marked as running'.format(pointing_id))

    # Catch any interrupts from now (only after we've marked the pointing as running)
    # This also tracks the elapsed time for tracking any min time
    InterruptedPointingCloser(pointing_id, min_time=pointing_info['min_time'])

    # Start slew
    print('Moving to target')
    slew_to_radec(pointing_info['ra'], pointing_info['dec'])

    # Add exposures while slewing to save time
    print('Adding exposures to queue')
    time_estimate = 0
    for expset_info in pointing_info['exposure_sets']:
        # Format UT mask
        if expset_info['ut_mask'] is not None:
            ut_string = ut_mask_to_string(expset_info['ut_mask'])
            ut_list = ut_string_to_list(ut_string)
        else:
            ut_list = []

        # Add to queue
        # Use the daemon_function to include database IDs
        args = [ut_list,
                expset_info['exptime'],
                expset_info['num_exp'],
                expset_info['filt'],
                expset_info['binning'],
                'normal',
                pointing_info['name'],
                'SCIENCE',
                False,
                expset_info['id'],
                pointing_info['id'],
                ]
        daemon_function('exq', 'add', args=args)

        # Add to time estimate
        time_estimate += (expset_info['exptime'] + 30) * expset_info['num_exp']

    # Wait for the mount to slew (timeout 120s)
    wait_for_mount(pointing_info['ra'], pointing_info['dec'], timeout=120)

    print('In position: starting exposures')
    # Resume the queue
    execute_command('exq resume')

    # Wait for the queue to empty
    # NB We deliberately use a pesamistic timeout, it will raise an error if it takes too long
    wait_for_exposure_queue(time_estimate * 1.5)

    # Mark as completed
    mark_pointing(pointing_id, 'completed')
    print('Pointing {} marked as completed'.format(pointing_id))
    sys.exit(0)


if __name__ == '__main__':
    parser = ArgumentParser(description='Observe the given database pointing.')
    parser.add_argument('pointing_id', type=int, help='Pointing Database ID')
    args = parser.parse_args()

    run(args.pointing_id)
