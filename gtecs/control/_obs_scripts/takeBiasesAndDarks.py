#!/usr/bin/env python3
"""Script to take bias and dark frames."""

from argparse import ArgumentParser

from gtecs.common.system import execute_command
from gtecs.control.observing import prepare_for_images, wait_for_exposure_queue


def run(nexp=5):
    """Take biases and darks at start of night.

    Parameters
    ----------
    nexp : int
        number of each type of frame to take

    """
    print('Taking bias and dark frames.')

    # make sure hardware is ready
    prepare_for_images(open_covers=False)

    execute_command('exq multbias {} 1'.format(nexp))
    execute_command('exq multdark {} 60 1'.format(nexp))
    execute_command('exq multdark {} 90 1'.format(nexp))
    execute_command('exq multdark {} 120 1'.format(nexp))
    execute_command('exq resume')  # just in case

    # estimate a deliberately pessimistic timeout
    readout = 10
    total_time = (1 + readout +
                  60 + readout +
                  90 + readout +
                  120 + readout) * nexp
    total_time *= 1.5
    wait_for_exposure_queue(total_time)

    print('Biases and darks done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take bias and dark frames.')
    parser.add_argument('nexp', type=int, nargs='?', default=5,
                        help='number of frames to take (default=5)')
    args = parser.parse_args()

    run(args.nexp)
