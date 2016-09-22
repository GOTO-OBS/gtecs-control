"""
Script to run the tasks for Start Night Phase 3.

This script should perform the following simple tasks:
* take bias and dark frames (the dome should still be closed)
"""
from __future__ import absolute_import
from __future__ import print_function

from gtecs.tecs_modules.misc import execute_command as cmd
from gtecs.tecs_modules.observing import wait_for_exposure_queue


def run(nexp=5):
    """
    Take biasses and darks at start of night.

    Parameters
    ----------
    nexp : int
        number of each type of frame to take
    """
    print('Start of Night Phase 3')

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
