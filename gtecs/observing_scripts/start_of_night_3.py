"""
Script to run the tasks for Start Night Phase 3.

This script should perform the following simple tasks:
* take bias and dark frames (the dome should still be closed)
"""
from __future__ import absolute_import
from __future__ import print_function
import time

import Pyro4

from gtecs.tecs_modules.misc import execute_command as cmd, ERROR
from gtecs.tecs_modules import params


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

    # we should not return straight away, but wait until queue is empty
    EXQ_DAEMON_ADDRESS = params.DAEMONS['exq']['ADDRESS']
    exq = Pyro4.Proxy(EXQ_DAEMON_ADDRESS)
    exq._pyroTimeout = params.PROXY_TIMEOUT
    while 1:
        time.sleep(5)
        try:
            queue_list = exq.get()
            if len(queue_list == 0):
                break
        except:
            print(ERROR('No response from exposure queue daemon'))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        nexp = int(sys.argv[1])
    else:
        nexp = 5
    run(nexp)
