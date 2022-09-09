#!/usr/bin/env python3
"""Script to run during the night while the dome is closed."""

import time
from argparse import ArgumentParser

from gtecs.common.system import execute_command
from gtecs.control.observing import (prepare_for_images, mirror_covers_are_closed, slew_to_altaz,
                                     wait_for_exposure_queue)


def run(nexp=3):
    """Tasks to occupy the telescope while the dome is closed.

    Parameters
    ----------
    nexp : int
        number of each type of bias and dark frame to take

    """
    print('Running bad conditions tasks')

    # make sure hardware is ready
    prepare_for_images(open_covers=True)
    time.sleep(2)

    # close the covers again for darks (we open to stop them sticking)
    print('Closing mirror covers')
    execute_command('ota close')
    while not mirror_covers_are_closed():
        time.sleep(0.5)

    # move the mount around
    for az in [0, 90, 180, 270, 0]:
        slew_to_altaz(50, az, timeout=120)
        time.sleep(2)
    print('Mount tests complete')

    # take extra biases and darks
    execute_command('exq multbias {} 1'.format(nexp))
    execute_command('exq multdark {} 60 1'.format(nexp))
    execute_command('exq multdark {} 90 1'.format(nexp))
    execute_command('exq multdark {} 120 1'.format(nexp))
    execute_command('exq multdark {} 600 1'.format(nexp))
    execute_command('exq resume')  # just in case

    # estimate a deliberately pessimistic timeout
    readout = 10
    total_time = (1 + readout +
                  60 + readout +
                  90 + readout +
                  120 + readout +
                  600 + readout
                  ) * nexp
    total_time *= 1.5
    wait_for_exposure_queue(total_time)
    print('Biases and darks complete')

    print('Bad conditions tasks done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Tasks to run during the night while the dome is closed.')
    parser.add_argument('nexp', type=int, nargs='?', default=3,
                        help='number of bias and dark sets to take (default=3)')
    args = parser.parse_args()

    run(args.nexp)
