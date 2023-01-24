#!/usr/bin/env python3
"""Script to take bias and dark frames."""

from argparse import ArgumentParser

from gtecs.common.system import execute_command
from gtecs.control.observing import prepare_for_images, wait_for_exposure_queue


def run(num_exp=5, extras=False):
    """Take biases and darks at start of night.

    Parameters
    ----------
    num_exp : int
        number of each type of frame to take

    extras : bool, optional
        if True, take extra dark frames
        default is False

    """
    print('Taking bias and dark frames.')

    # make sure hardware is ready
    prepare_for_images(open_covers=False)

    # TODO: Get set of exposure times from the database?
    #       We'd need a camera/exposure database...
    execute_command('exq multbias {} 1'.format(num_exp))
    execute_command('exq multdark {} 45 1'.format(num_exp))
    execute_command('exq multdark {} 60 1'.format(num_exp))
    execute_command('exq multdark {} 90 1'.format(num_exp))
    execute_command('exq multdark {} 120 1'.format(num_exp))
    if extras:
        # take a few extra long dark frames to test for hot pixels
        execute_command('exq multdark 2 600 1')
    execute_command('exq resume')  # just in case

    # estimate a deliberately pessimistic timeout
    readout = 10
    total_time = (1 + readout +
                  60 + readout +
                  90 + readout +
                  120 + readout) * num_exp
    if extras:
        total_time += (600 + readout) * 2
    total_time *= 1.5
    wait_for_exposure_queue(total_time)

    print('Biases and darks done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take bias and dark frames.')
    # Optional arguments
    parser.add_argument('numexp',
                        type=int,
                        nargs='?', default=5,
                        help=('number of frames to take for each exposure time'
                              ' (default=%(default)d)'),
                        )
    # Flags
    parser.add_argument('-x', '--take-extras',
                        action='store_true',
                        help=('take two extra long dark frames to test for hot pixels'),
                        )

    args = parser.parse_args()
    num_exp = args.numexp
    extras = args.take_extras

    run(num_exp, extras)
