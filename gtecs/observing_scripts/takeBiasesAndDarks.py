#!/usr/bin/env python
"""Script to take bias and dark frames.

takeBiasesAndDarks [nExp]
"""

import sys

from gtecs.misc import execute_command
from gtecs.observing import prepare_for_images, wait_for_exposure_queue


def run(nexp=5):
    """Take biases and darks at start of night.

    Parameters
    ----------
    nexp : int
        number of each type of frame to take

    """
    print('Taking bias and dark frames.')

    # make sure hardware is ready
    prepare_for_images()

    execute_command('exq multbias {} 1'.format(nexp))
    execute_command('exq multdark {} 120 1'.format(nexp))
    execute_command('exq resume')  # just in case

    # estimate a deliberately pessimistic timeout
    readout = 30 * nexp
    total_exp = 120 * nexp
    total_time = 1.5 * (readout + total_exp)
    wait_for_exposure_queue(total_time)

    print('Biases and darks done')


if __name__ == "__main__":
    if len(sys.argv) > 1:
        nexp = int(sys.argv[1])
    else:
        nexp = 5

    run(nexp)
