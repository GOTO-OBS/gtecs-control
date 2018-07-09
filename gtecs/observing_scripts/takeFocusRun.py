"""
takeFocusRun [width=2000] [step=200] [filter] [plot (y/n)]
Script to take a series of images running through focus

It assumes you're already on a reasonable patch of sky and that you're
already focused (see autoFocus script)
"""

import os
import sys
import time
import argparse

import numpy as np
import pandas as pd

from matplotlib import pyplot as plt

from astropy import units as u
from astropy.time import Time
from astropy.io import fits

from gtecs import params
from gtecs.misc import execute_command, neatCloser
from gtecs.astronomy import nightStarting
from gtecs.observing import (wait_for_exposure_queue, prepare_for_images,
                             get_current_focus, set_new_focus,
                             wait_for_focuser, last_written_image)
from gtecs.observing_scripts.autoFocus import (take_frame, RestoreFocus,
                                               set_focus_carefully, get_hfd)


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


def run(width, step, filt, make_plots):
    # make sure hardware is ready
    prepare_for_images()

    expT = 30

    xslice = slice(3300, 5100)
    yslice = slice(1400, 4100)
    kwargs = {'xslice': xslice, 'yslice': yslice,
              'filter_width': 4, 'threshold': 15}

    orig_focus = get_current_focus()
    deltas = np.arange(-width, +width+1, step)
    print('Steps ({:.0f}): '.format(len(deltas)), deltas)
    pos_master_list = {
        tel: np.arange(orig_focus[tel]-width, orig_focus[tel]+width+1, step)
        for tel in params.TEL_DICT
    }

    pos_master_list = pd.DataFrame(pos_master_list)

    # from here any exception or attempt to close should move to old focus
    close_signal_handler = RestoreFocus(orig_focus)
    series_list = []

    print("Starting focus run")

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

    # restore the origional focus
    print('############')
    print('Restoring original focus')
    set_new_focus(orig_focus)

    # write out data
    print('############')
    print('Writing out data to file')
    path = os.path.join(params.CONFIG_PATH, 'focus_data')
    df = pd.concat(series_list)
    ofname = 'focusdata_{}.csv'.format(Time.now().isot)
    df.to_csv(os.path.join(path, ofname))

    if make_plots == 'y':
        print('Plotting results')
        plot_results(df)

    print("Done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('width', nargs='?',  default=2000)
    parser.add_argument('step', nargs='?',  default=200)
    parser.add_argument('filt', nargs='?',  default='L')
    parser.add_argument('plot', nargs='?',  default='y')
    args = parser.parse_args()

    if args.filt not in params.FILTER_LIST:
        raise ValueError('filter not one of {!r}'.format(params.FILTER_LIST))

    run(int(args.width), int(args.step), args.filt, args.plot)
