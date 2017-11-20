from __future__ import absolute_import
from __future__ import print_function

import os
import numpy as np
import pandas as pd
import argparse
from matplotlib import pyplot as plt
from astropy import units as u
from astropy.time import Time
from astropy.io import fits

from gtecs.tecs_modules.misc import execute_command as cmd, neatCloser
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          prepare_for_images,
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


def plot_results(df):
    tels = params.TEL_DICT.keys()
    fig, axes = plt.subplots(nrows=len(tels), ncols=2)
    kwargs = dict(
        color='k',
        ecolor='k',
        fmt='.'
    )
    for i, tel in enumerate(tels):
        ax_hfd = axes[i, 0]
        ax_fwhm = axes[i, 1]
        df_tel = df.loc[tel]

        x = df_tel['pos']
        yfw = df_tel['fwhm']
        yhfd = df_tel['median']

        sn_mask = yfw/df_tel['fwhm_std'] > 2
        e = df_tel['fwhm_std'][sn_mask]
        pars = np.polyfit(x[sn_mask], yfw[sn_mask], w=1/e, deg=2)
        best_focus = -pars[1]/2/pars[0]
        print('UT{} best focus @ {}'.format(tel, int(best_focus)))
        poly = np.poly1d(pars)

        ax_hfd.errorbar(x, yhfd, yerr=df_tel['std'], **kwargs)
        ax_fwhm.errorbar(x, yfw, yerr=df_tel['fwhm_std'], **kwargs)
        ax_fwhm.axvline(best_focus, color='r', ls='--')
        ax_fwhm.plot(x, poly(x), 'r-')
        ax_fwhm.set_xlabel('Pos')
        ax_hfd.set_xlabel('Pos')
        ax_hfd.set_ylabel('HFD')
        ax_fwhm.set_ylabel('FWHM')
    plt.show()


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('filter')
    args = parser.parse_args()
    filt = args.filter
    if filt not in params.FILTER_LIST:
        raise ValueError('filter not one of {!r}'.format(params.FILTER_LIST))

    # make sure hardware is ready
    prepare_for_images()

    print("Starting focus run")

    total_diff = 1500
    step = 150
    expT = 30

    xslice = slice(3300, 5100)
    yslice = slice(2800, 4100)
    kwargs = {'xslice': xslice, 'yslice': yslice,
              'filter_width': 4, 'threshold': 15}

    orig_focus = get_current_focus()
    pos_master_list = {
        tel: np.arange(orig_focus[tel]-total_diff, orig_focus[tel]+total_diff, step)
        for tel in params.TEL_DICT
    }
    pos_master_list = pd.DataFrame(pos_master_list)

    # from here any exception or attempt to close should move to old focus
    close_signal_handler = RestoreFocus(orig_focus)
    series_list = []

    for runno, row in pos_master_list.iterrows():

        print('############')
        print('## RUN {} of {}'.format(runno+1, len(pos_master_list)))
        set_focus_carefully(row, orig_focus, 100)
        print('Focus: {!r}'.format(get_current_focus()))
        print('Taking frames')
        fnames = take_frame(expT, filt, 'FocusRun')
        hfd_values = get_hfd(fnames, **kwargs)
        print('Focus Data:\n{!r}'.format(hfd_values))
        hfd_values['pos'] = pd.Series(get_current_focus())
        series_list.append(hfd_values)
    print('Exposures finished')

    # write out data
    path = os.path.join(params.CONFIG_PATH, 'focus_data')
    df = pd.concat(series_list)
    ofname = 'focusdata_{}.csv'.format(Time.now().isot)
    df.to_csv(os.path.join(path, ofname))
    plot_results(df)

    # and finish by restoring the origional focus
    print('############')
    print('Restoring original focus')
    set_new_focus(orig_focus)
    print("Done")
