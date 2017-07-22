from __future__ import absolute_import
from __future__ import print_function

import numpy as np
import pandas as pd

from astropy import units as u
from astropy.time import Time
from astropy.io import fits

from gtecs.tecs_modules.misc import execute_command as cmd, neatCloser
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          get_current_focus, set_new_focus,
                                          wait_for_focuser, last_written_image)
import gtecs.tecs_modules.astronomy as ast
from gtecs.tecs_modules import params
from gtecs.tecs_modules.time_date import nightStarting
from gtecs.observing_scripts.autoFocus import (take_frame, RestoreFocus,
                                               set_focus_carefully, get_hfd)
import time
import sys

# A script to take a series of images running through focus
# It assumes you're already on a reasonable patch of sky and that you're
# already focused (see autoFocus script)

if __name__ == "__main__":
    print("Starting focus run")

    total_diff = 5000
    large_step = 1000
    small_step = 100
    expT = 30
    filt = 'L'

    xslice = slice(3300, 5100)
    yslice = slice(2800, 4100)
    kwargs = {'xslice': xslice, 'yslice': yslice}

    orig_focus = get_current_focus()
    pos_master_list = {
        tel: np.arange(orig_focus[tel]-total_diff, orig_focus[tel]+total_diff, large_step)
        for tel in params.TEL_DICT
    }
    for tel in params.TEL_DICT:
        foc = orig_focus[tel]
        fine_grid = np.arange(foc-large_step, foc+large_step, small_step)
        new_array = np.unique(np.concatenate((pos_master_list[tel], fine_grid)))
        new_array.sort()
        pos_master_list[tel] = new_array
    pos_master_list = pd.DataFrame(pos_master_list)

    # from here any exception or attempt to close should move to old focus
    close_signal_handler = RestoreFocus(orig_focus)

    for runno, row in pos_master_list.iterrows():

        print('############')
        print('## RUN {} of {}'.format(runno+1, len(pos_master_list)))
        set_focus_carefully(row, orig_focus, 100)
        print('Focus: {!r}'.format(get_current_focus()))
        print('Taking frames')
        fnames = take_frame(expT, filt, 'FocusRun')
        hfd_values = get_hfd(fnames, **kwargs)
        print('Focus Data:\n{!r}'.format(hfd_values))
    print('Exposures finished')

    # and finish by restoring the origional focus
    print('############')
    print('Restoring original focus')
    set_new_focus(orig_focus)

    print("Done")
