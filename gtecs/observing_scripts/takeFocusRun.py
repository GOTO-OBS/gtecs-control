from __future__ import absolute_import
from __future__ import print_function

import numpy as np

from astropy import units as u
from astropy.time import Time
from astropy.io import fits

from gtecs.tecs_modules.misc import execute_command as cmd, neatCloser
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          get_current_focus, set_new_focus,
                                          wait_for_focuser)
import gtecs.tecs_modules.astronomy as ast
from gtecs.tecs_modules.time_date import nightStarting

import time
import sys

# A script to take a series of images running through focus
# It assumes you're already on a reasonable patch of sky and that you're
# already focused (see autoFocus script)

def take_frame(expT, current_filter):
    cmd('exq image {} {} 1 "NA" FOCUS'.format(expT, current_filter))
    time.sleep(0.1)
    wait_for_exposure_queue()
    time.sleep(0.1)
    return

class RestoreFocus(neatCloser):
    def __init__(self, focusVals):
        super(RestoreFocus, self).__init__('autofocus')
        self.focusVals = focusVals

    def tidyUp(self):
        print('Restoring original focus')
        set_new_focus(self.focusVals)

def set_focus_carefully(new_focus_values, orig_focus):
    """
    Move to focus, but restore old values if we fail
    """
    set_new_focus(new_focus_values)
    try:
        wait_for_focuser(30)
    except:
        set_new_focus(orig_focus)
        raise

if __name__ == "__main__":
    print("Starting focus run")

    total_diff = 20000
    large_step = 2000
    small_step = 500
    expT = 10
    filt = 'L'

    orig_focus_list = get_current_focus() # list length of number of focs
    pos_master_list = []
    for i, orig_focus in enumerate(orig_focus_list):
        print('UT{}:'.format(i+1))
        print('    Current focus: {!r}'.format(orig_focus))

        # Find focus position list
        out_small_list = list(range(orig_focus,
                                    orig_focus+large_step,
                                    small_step))
        out_large_list = list(range(orig_focus+large_step,
                                    orig_focus+total_diff+1,
                                    large_step))

        in_small_list = list(range(orig_focus-large_step,
                                   orig_focus,
                                   small_step))
        in_large_list = list(range(orig_focus-total_diff,
                                   orig_focus-large_step-1,
                                   large_step))

        pos_list = in_large_list+in_small_list+out_small_list+out_large_list
        #print(pos_list)

        print('    Taking {} frames'.format(len(pos_list)))
        print('    Focus varying from {} to {}'.format(orig_focus-total_diff,
                                                       orig_focus+total_diff))

        pos_master_list += [pos_list]

    positions = list(zip(*pos_master_list))

    # from here any exception or attempt to close should move to old focus
    close_signal_handler = RestoreFocus(orig_focus_list)

    for runno, pos_list in enumerate(positions):
        print('############')
        print('## RUN {} of {}'.format(runno+1, len(positions)))
        for i, pos in enumerate(pos_list):
            print('Setting UT{} to focus position {}'.format(i+1,pos))
        print('~~~~~~~~~~~~')
        set_focus_carefully(list(pos_list), orig_focus_list)
        print('~~~~~~~~~~~~')
        print('Taking frames')
        fnames = take_frame(expT, filt)
        print('Exposures finished')

    # and finish by restoring the origional focus
    print('############')
    print('Restoring original focus')
    set_new_focus(orig_focus_list)

    print("Done")
