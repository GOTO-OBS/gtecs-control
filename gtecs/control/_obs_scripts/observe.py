#!/usr/bin/env python3
"""Script to control observing a single pointing."""

import sys
import time
import traceback
from argparse import ArgumentParser

from gtecs.common.system import NeatCloser, execute_command
from gtecs.control import params
from gtecs.control.daemons import daemon_function
from gtecs.control.focusing import refocus
from gtecs.control.misc import ut_mask_to_string, ut_string_to_list
from gtecs.control.observing import (prepare_for_images, slew_to_radec,
                                     wait_for_exposure_queue, wait_for_mount)
from gtecs.control.scheduling import get_pointing_info


def handle_interrupt(pointing_id, start_time, min_time):
    """Long and return the correct error code depending on min time."""
    print('Interrupt caught')
    elapsed_time = time.time() - start_time
    print('Elapsed time: {:.0f}s'.format(elapsed_time))
    if min_time is None:
        # Return retcode 1
        print('Pointing {} was interrupted'.format(pointing_id))
        return 1
    else:
        if elapsed_time > min_time:
            # We observed enough, return retcode 0
            print('Passed min time ({:.0f}s)'.format(min_time))
            print('Pointing {} was completed'.format(pointing_id))
            return 0
        else:
            # Return retcode 1
            print('Did not pass min time ({:.0f}s)'.format(min_time))
            print('Pointing {} was interrupted'.format(pointing_id))
            return 1


class InterruptedPointingCloser(NeatCloser):
    """A class to neatly handle shutdown requests."""

    def __init__(self, pointing_id, start_time, min_time=None):
        super().__init__(taskname='Script')
        self.pointing_id = pointing_id
        self.start_time = start_time
        self.min_time = min_time

    def tidy_up(self):
        """Mark the Pointing correctly."""
        retcode = handle_interrupt(self.pointing_id, self.start_time, self.min_time)
        sys.exit(retcode)


def run(pointing_id):
    """Run the observe routine."""
    # make sure hardware is ready
    prepare_for_images()
    if params.FOCUS_COMPENSATION_ENABLED:
        refocus(params.FOCUS_COMPENSATION_TEST, params.FOCUS_COMPENSATION_VERBOSE)

    # Clear & pause queue to make sure
    execute_command('exq clear')
    execute_command('exq pause')
    execute_command('cam abort')

    # Get the Pointing information from the scheduler
    pointing_info = get_pointing_info(pointing_id)
    print('Observing pointing ID: ', pointing_id)
    start_time = time.time()

    # Catch any interrupts or exceptions from now on
    InterruptedPointingCloser(pointing_id, start_time, min_time=pointing_info['min_time'])
    try:
        # Start slew
        print('Moving to target')
        slew_to_radec(pointing_info['ra'], pointing_info['dec'])

        # Add exposures while slewing to save time
        print('Adding exposures to queue')
        if len(pointing_info['exposure_sets']) == 0:
            raise ValueError('No exposure sets found')
        time_estimate = 0
        for expset_info in pointing_info['exposure_sets']:
            # Format UT mask
            if expset_info['ut_mask'] is not None:
                ut_string = ut_mask_to_string(expset_info['ut_mask'])
                ut_list = ut_string_to_list(ut_string)
            else:
                ut_list = params.UTS_WITH_CAMERAS

            # Add to queue
            # Use the daemon_function to include database IDs, rather than execute_command()
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

            # Print like execute_command()
            print('exq add', ' '.join([str(a) for a in args]) + ':')
            msg = '> Added {}{:.0f}s {} exposure{}'.format(
                '{}x '.format(expset_info['num_exp']) if expset_info['num_exp'] > 1 else '',
                expset_info['exptime'],
                expset_info['filt'].upper() if expset_info['filt'] else 'X',
                's' if expset_info['num_exp'] > 1 else '',
            )
            print(msg)

            # Add to time estimate
            time_estimate += (expset_info['exptime'] + 30) * expset_info['num_exp']

        # Wait for the mount to slew (timeout 120s)
        wait_for_mount(pointing_info['ra'], pointing_info['dec'], timeout=120)

        print('In position: starting exposures')
        # Resume the queue
        execute_command('exq resume')

        # Wait for the queue to empty
        # NB We deliberately use a pessimistic timeout, it will raise an error if it takes too long
        wait_for_exposure_queue(time_estimate * 1.5)

        # Exposures are done, return retcode 0
        print('Pointing {} is completed'.format(pointing_id))
        sys.exit(0)

    except Exception:
        traceback.print_exc()
        retcode = handle_interrupt(pointing_id, start_time, min_time=pointing_info['min_time'])
        sys.exit(retcode)


if __name__ == '__main__':
    parser = ArgumentParser(description='Observe the given database pointing.')
    parser.add_argument('pointing_id', type=int, help='Pointing Database ID')
    args = parser.parse_args()

    run(args.pointing_id)
