"""
Script to take bias and dark frames.
"""
from __future__ import absolute_import
from __future__ import print_function

import sys
import time

import numpy as np

from gtecs.tecs_modules import params
from gtecs.tecs_modules.misc import execute_command as cmd
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          prepare_for_images)

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

    cmd('exq multbias {} 1'.format(nexp))  # 1x1 binning
    cmd('exq multbias {} 2'.format(nexp))  # 2x2 binning
    cmd('exq multbias {} 3'.format(nexp))  # 3x3 binning
    cmd('exq multdark {} 120 1'.format(nexp))
    cmd('exq resume')  # just in case

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
