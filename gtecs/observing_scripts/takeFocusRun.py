#!/usr/bin/env python3
"""Script to take a series of images running through focus.

It assumes you're already on a reasonable patch of sky and that you're
already focused (see autoFocus script)
"""

import os
import sys
import traceback
from argparse import ArgumentParser, ArgumentTypeError

from astropy.time import Time

from gtecs import params
from gtecs.catalogs import focus_star
from gtecs.observing import (get_analysis_image, get_current_focus, get_focus_limit,
                             prepare_for_images, set_new_focus, slew_to_radec, wait_for_focuser,
                             wait_for_mount)
from gtecs.observing_scripts.autoFocus import RestoreFocus, get_hfds, set_focus_carefully

from matplotlib import pyplot as plt

import numpy as np

import pandas as pd


def plot_results(df, finish_time):
    """Plot the results of the focus run."""
    fig, axes = plt.subplots(nrows=len(params.UTS_WITH_FOCUSERS), ncols=2)

    for i, ut in enumerate(params.UTS_WITH_FOCUSERS):
        try:
            ut_data = df.loc[ut]

            # HFD plot
            ax_hfd = axes[i, 0]
            ax_hfd.errorbar(ut_data['pos'], ut_data['median'], yerr=ut_data['std'],
                            color='k', ecolor='k', fmt='.')
            ax_hfd.set_xlabel('Pos')
            ax_hfd.set_ylabel('HFD')

            # FWHM plot
            ax_fwhm = axes[i, 1]
            sn_mask = ut_data['fwhm'] / ut_data['fwhm_std'] > 2
            e = ut_data['fwhm_std'][sn_mask]
            pars = np.polyfit(ut_data['pos'][sn_mask], ut_data['fwhm'][sn_mask], w=1 / e, deg=2)
            best_focus = -pars[1] / 2 / pars[0]
            print('UT{} best focus @ {}'.format(ut, int(best_focus)))
            poly = np.poly1d(pars)

            ax_fwhm.errorbar(ut_data['pos'], ut_data['fwhm'], yerr=ut_data['fwhm_std'],
                             color='k', ecolor='k', fmt='.')
            ax_fwhm.plot(ut_data['pos'], poly(ut_data['pos']), 'r-')
            ax_fwhm.axvline(best_focus, color='r', ls='--')

            ax_fwhm.set_xlabel('Pos')
            ax_fwhm.set_ylabel('FWHM')

        except Exception:
            print('Error plotting UT{}'.format(ut))
            print(traceback.format_exc())

    # Save the plot
    path = os.path.join(params.FILE_PATH, 'focus_data')
    ofname = 'focusplot_{}.png'.format(finish_time)
    plt.savefig(ofname)
    print('Saved to {}'.format(os.path.join(path, ofname)))

    plt.show()


def calculate_positions(fraction, steps):
    """Calculate the positions for the focus run."""
    # Get the current focus positions, and the maximum limit (assuming minimum is 0)
    current = get_current_focus()
    limits = get_focus_limit()

    all_positions = {}
    for ut in limits:
        print('UT{}: current position={}'.format(ut, current[ut]))
        # Calculate the deltas
        width = int((limits[ut] * fraction) / 2)
        upper_deltas = np.arange(0, width + 1, width // steps)
        lower_deltas = upper_deltas[::-1] * -1
        deltas = np.append(lower_deltas[:-1], upper_deltas)

        # Calculate the positions
        positions = current[ut] + deltas

        # Check if any are beyond the limits
        if positions[0] < 0:
            n_bad = sum(positions < 0)
            print('  WARNING: {} position(s) below minimum (0)'.format(n_bad))
            positions[positions < 0] = [0] * n_bad
        if positions[-1] > limits[ut]:
            n_bad = sum(positions > limits[ut])
            print('  WARNING: {} position(s) above maximum ({})'.format(n_bad, limits[ut]))
            positions[positions > limits[ut]] = [limits[ut]] * n_bad
        print('  {} positions:'.format(len(positions)), positions)

        all_positions[ut] = positions

    return all_positions


def run(fraction, steps, num_exp, exptime, filt, no_slew, no_plot, no_confirm):
    """Run the focus run routine."""
    # Get the positions for the run
    print('~~~~~~')
    print('Calculating positions...')
    positions = calculate_positions(fraction, steps)

    # Confirm
    if not no_confirm:
        go = ''
        while go not in ['y', 'n']:
            go = input('Continue? [y/n]: ')
        if go == 'n':
            sys.exit()

    # make sure hardware is ready
    prepare_for_images()

    print('~~~~~~')
    print('Starting focus routine')

    # Slew to a focus star
    if not no_slew:
        star = focus_star(Time.now())
        print('~~~~~~')
        print('Slewing to target...', star)
        coordinate = star.coord_now()
        slew_to_radec(coordinate.ra.deg, coordinate.dec.deg)
        wait_for_mount(coordinate.ra.deg, coordinate.dec.deg, timeout=120)
        print('Reached target')
        print('~~~~~~')

    # Store the current focus
    orig_focus = get_current_focus()
    print('Initial focus: ', get_current_focus())
    print('~~~~~~')
    # from here any exception or attempt to close should move to old focus
    RestoreFocus(orig_focus)

    series_list = []
    pos_master_list = pd.DataFrame(positions)
    for runno, row in pos_master_list.iterrows():
        print('## RUN {} of {}'.format(runno + 1, len(pos_master_list)))
        print('Setting focus...')
        set_focus_carefully(row, orig_focus, 100)
        print('New focus: ', get_current_focus())
        print('~~~~~~')
        hfds = None
        fwhms = None
        for i in range(num_exp):
            print('Taking exposure {}/{}...'.format(i + 1, num_exp))
            image = get_analysis_image(exptime, filt,
                                       'Focus run', 'FOCUS', glance=False)
            data = get_hfds(image, xslice=slice(3300, 5100), yslice=slice(1400, 4100),
                            filter_width=4, threshold=15)
            if hfds is not None:
                hfds = hfds.append(data['median'])
                fwhms = fwhms.append(data['fwhm'])
            else:
                hfds = data['median']
                fwhms = data['fwhm']
            print('HFDs:', data['median'].to_dict())
            print('~~~~~~')

        # Take the smallest value of the set
        hfds = hfds.groupby(level=0)
        hfd_dict = hfds.min()
        print('Best HFDs:', hfd_dict.to_dict())
        fwhms = fwhms.groupby(level=0)
        fwhm_dict = fwhms.min()

        data = {'median': hfd_dict,
                'std': hfd_dict.std(),
                'fwhm': fwhm_dict,
                'fwhm_std': fwhm_dict.std(),
                'pos': pd.Series(get_current_focus()),
                }
        series_list.append(pd.DataFrame(data))

        print('~~~~~~')

    print('Exposures finished')
    finish_time = Time.now().isot

    # Restore the origional focus
    print('~~~~~~')
    print('Restoring original focus...')
    set_new_focus(orig_focus)
    wait_for_focuser(orig_focus, timeout=120)
    print('Restored focus: ', get_current_focus())

    # Write out data
    print('~~~~~~')
    print('Writing out data to file...')
    path = os.path.join(params.FILE_PATH, 'focus_data')
    df = pd.concat(series_list)
    ofname = 'focusdata_{}.csv'.format(finish_time)
    df.to_csv(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    # Make plots
    if not no_plot:
        print('~~~~~~')
        print('Plotting results...')
        plot_results(df, finish_time)

    print('Done')


if __name__ == '__main__':
    def restricted_float(x):
        """See https://stackoverflow.com/questions/12116685/."""
        try:
            x = float(x)
        except ValueError:
            raise ArgumentTypeError("invalid float value: '{}'".format(x))

        if x < 0.0 or x > 1.0:
            raise ArgumentTypeError("'{}' not in range [0, 1]".format(x))
        return x

    parser = ArgumentParser(description='Take a series of exposures at different focus positions.')
    parser.add_argument('fraction', type=restricted_float,
                        help=('fraction of the focuser range to run over '
                              '(range 0-1)'),
                        )
    parser.add_argument('steps', type=int,
                        help=('how many exposures to take either side of the current position '
                              '(e.g. steps=5 gives 11 in total: 5 + 1 in the centre + 5)'),
                        )
    parser.add_argument('-n', '--numexp', type=int, default=3,
                        help=('number of exposures to take at each position (default=3)')
                        )
    parser.add_argument('-t', '--exptime', type=float, default=30,
                        help=('exposure time to use (default=30s)')
                        )
    parser.add_argument('-f', '--filter', type=str, choices=params.FILTER_LIST, default='L',
                        help=('filter to use (default=L)')
                        )
    parser.add_argument('--no-slew', action='store_true',
                        help=('do not slew to a focus star (stay at current position)')
                        )
    parser.add_argument('--no-plot', action='store_true',
                        help=('do not display plot of results')
                        )
    parser.add_argument('--no-confirm', action='store_true',
                        help=('skip confirmation (needed if running automatically)')
                        )
    args = parser.parse_args()

    fraction = args.fraction
    steps = args.steps
    num_exp = args.numexp
    exptime = args.exptime
    filt = args.filter
    no_slew = args.no_slew
    no_plot = args.no_plot
    no_confirm = args.no_confirm

    run(fraction, steps, num_exp, exptime, filt, no_slew, no_plot, no_confirm)
