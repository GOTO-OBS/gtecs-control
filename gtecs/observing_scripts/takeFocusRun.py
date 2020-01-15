#!/usr/bin/env python3
"""Script to take a series of images running through focus.

takeFocusRun [width=2000] [step=200] [plot (y/n)]

It assumes you're already on a reasonable patch of sky and that you're
already focused (see autoFocus script)
"""

import argparse
import os

from astropy.time import Time

from gtecs import params
from gtecs.catalogs import focus_star
from gtecs.observing import (get_analysis_image, get_current_focus, prepare_for_images,
                             set_new_focus, slew_to_radec, wait_for_mount)
from gtecs.observing_scripts.autoFocus import RestoreFocus, get_hfds, set_focus_carefully

from matplotlib import pyplot as plt

import numpy as np

import pandas as pd


def plot_results(df):
    """Plot the results of the focus run."""
    fig, axes = plt.subplots(nrows=len(params.UTS_WITH_FOCUSERS), ncols=2)
    kwargs = dict(
        color='k',
        ecolor='k',
        fmt='.'
    )
    for i, ut in enumerate(params.UTS_WITH_FOCUSERS):
        ax_hfd = axes[i, 0]
        ax_fwhm = axes[i, 1]
        df_ut = df.loc[ut]

        x = df_ut['pos']
        yfw = df_ut['fwhm']
        yhfd = df_ut['median']

        sn_mask = yfw / df_ut['fwhm_std'] > 2
        e = df_ut['fwhm_std'][sn_mask]
        pars = np.polyfit(x[sn_mask], yfw[sn_mask], w=1 / e, deg=2)
        best_focus = -pars[1] / 2 / pars[0]
        print('UT{} best focus @ {}'.format(ut, int(best_focus)))
        poly = np.poly1d(pars)

        ax_hfd.errorbar(x, yhfd, yerr=df_ut['std'], **kwargs)
        ax_fwhm.errorbar(x, yfw, yerr=df_ut['fwhm_std'], **kwargs)
        ax_fwhm.axvline(best_focus, color='r', ls='--')
        ax_fwhm.plot(x, poly(x), 'r-')
        ax_fwhm.set_xlabel('Pos')
        ax_hfd.set_xlabel('Pos')
        ax_hfd.set_ylabel('HFD')
        ax_fwhm.set_ylabel('FWHM')
    plt.show()


def run(width, step, make_plots):
    """Run the focus run routine."""
    # make sure hardware is ready
    prepare_for_images()

    # Slew to a focus star
    print('~~~~~~')
    print('Starting focus routine')
    star = focus_star(Time.now())
    print('Slewing to target', star)
    coordinate = star.coord_now()
    slew_to_radec(coordinate.ra.deg, coordinate.dec.deg)
    wait_for_mount(coordinate.ra.deg, coordinate.dec.deg, timeout=120)
    print('Reached target')

    xslice = slice(3300, 5100)
    yslice = slice(1400, 4100)
    kwargs = {'xslice': xslice, 'yslice': yslice,
              'filter_width': 4, 'threshold': 15}

    orig_focus = get_current_focus()

    if not params.FOCUSRUN_DELTAS:
        # Create deltas from the given width and steps
        deltas = np.arange(-width, +width + 1, step)
    else:
        # We've been given overwrite deltas
        deltas = np.array(params.FOCUSRUN_DELTAS)
    print('Steps ({:.0f}): '.format(len(deltas)), deltas)

    pos_master_list = {ut: orig_focus[ut] + deltas for ut in params.UTS_WITH_FOCUSERS}
    pos_master_list = pd.DataFrame(pos_master_list)
    print('Run positions for each UT:')
    print(pos_master_list)

    # from here any exception or attempt to close should move to old focus
    RestoreFocus(orig_focus)
    series_list = []

    print("Starting focus run")

    for runno, row in pos_master_list.iterrows():

        print('############')
        print('## RUN {} of {}'.format(runno + 1, len(pos_master_list)))
        set_focus_carefully(row, orig_focus, 100)
        print('Focus: {!r}'.format(get_current_focus()))
        print('Taking 3 frames')
        hfds = None
        fwhms = None
        for i in range(3):
            image = get_analysis_image(params.FOCUSRUN_EXPTIME, params.FOCUSRUN_FILTER,
                                       'Focus run', 'FOCUS', glance=False)
            data = get_hfds(image, **kwargs)
            if hfds is not None:
                hfds = hfds.append(data['median'])
                fwhms = fwhms.append(data['fwhm'])
            else:
                hfds = data['median']
                fwhms = data['fwhm']
            print('Measurement {:.0f}/3\n Half-flux-diameters:\n{!r}'.format(i + 1, data['median']))

        # Take the smallest value of the 3
        hfds = hfds.groupby(level=0)
        hfd_dict = hfds.min()
        print('Best measurement:\n Half-flux-diameters:\n{!r}'.format(hfd_dict))
        fwhms = fwhms.groupby(level=0)
        fwhm_dict = fwhms.min()

        data = {'median': hfd_dict,
                'std': hfd_dict.std(),
                'fwhm': fwhm_dict,
                'fwhm_std': fwhm_dict.std(),
                'pos': pd.Series(get_current_focus()),
                }

        series_list.append(pd.DataFrame(data))
    print('Exposures finished')

    # restore the origional focus
    print('############')
    print('Restoring original focus')
    set_new_focus(orig_focus)

    # write out data
    print('############')
    print('Writing out data to file')
    path = os.path.join(params.FILE_PATH, 'focus_data')
    df = pd.concat(series_list)
    ofname = 'focusdata_{}.csv'.format(Time.now().isot)
    df.to_csv(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    if make_plots == 'y':
        print('Plotting results')
        plot_results(df)

    print("Done")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('width', nargs='?', default=2000)
    parser.add_argument('step', nargs='?', default=200)
    parser.add_argument('plot', nargs='?', default='y')
    args = parser.parse_args()

    run(int(args.width), int(args.step), args.plot)
