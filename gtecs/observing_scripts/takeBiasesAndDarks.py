"""
takeBiasesAndDarks [nExp]
Script to take bias and dark frames
"""

import sys
import time

import numpy as np

from gtecs import params
from gtecs.misc import execute_command
from gtecs.observing import (wait_for_exposure_queue, prepare_for_images)

def run(nexp=5):
    """
    Take biases and darks at start of night.

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

    readout = 30*nexp
    total_exp = 120*nexp
    total_time = 1.5*(readout + total_exp)
    wait_for_exposure_queue(total_time)

    print('Biases and darks done')


if __name__ == "__main__":
    if len(sys.argv) > 1:
        nexp = int(sys.argv[1])
    else:
        nexp = 5

    run(nexp)
