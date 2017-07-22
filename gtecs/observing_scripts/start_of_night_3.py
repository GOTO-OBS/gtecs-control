"""
Script to run the tasks for Start Night Phase 3.

This script should perform the following simple tasks:
* take bias and dark frames (the dome should still be closed)
"""
from __future__ import absolute_import
from __future__ import print_function
import time

import pandas as pd
import numpy as np

from gtecs.tecs_modules.misc import execute_command as cmd
from gtecs.tecs_modules.observing import wait_for_exposure_queue, get_cam_temps


def run(nexp=5):
    """
    Take biasses and darks at start of night.

    Parameters
    ----------
    nexp : int
        number of each type of frame to take
    """
    print('Start of Night Phase 3')

    print('Cooling CCDs')
    cmd('cam temp -25')
    cool = False
    while not cool:
        cool = np.any(pd.Series(get_cam_temps()) > -24.5)
        time.sleep(1)

    cmd('exq multbias {} 1'.format(nexp))  # 1x1 binning
    cmd('exq multbias {} 2'.format(nexp))  # 2x2 binning
    cmd('exq multbias {} 3'.format(nexp))  # 3x3 binning
    cmd('exq multdark {} 120 1'.format(nexp))
    cmd('exq resume')  # just in case

    wait_for_exposure_queue()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        nexp = int(sys.argv[1])
    else:
        nexp = 5
    run(nexp)
    print("Biasses and darks done")
