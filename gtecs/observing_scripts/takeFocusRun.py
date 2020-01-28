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
from gtecs.observing_scripts.autoFocus import (RestoreFocus, find_best_focus, get_hfds,
                                               set_focus_carefully)

from matplotlib import pyplot as plt

import numpy as np

import pandas as pd

from scipy.optimize import curve_fit


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


def lin_func(x, m, c):
    """Fit HFDs."""
    return m * x + c


def parabola_func(x, a, b, c):
    """Fit FWHMs."""
    return a * x ** 2 + b * x + c


def fit_to_data(df):
    """Fit to HFD and FWHM data."""
    uts = list(set(list(df.index)))
    fit_df = pd.DataFrame(columns=['min_hfd', 'm1', 'm2', 'delta', 'best_hfd', 'best_fwhm'],
                          index=uts)
    hfd_coeffs = {ut: None for ut in uts}
    fwhm_coeffs = {ut: None for ut in uts}

    for ut in uts:
        # Get data arrays
        ut_data = df.loc[ut]
        pos = np.array(ut_data['pos'])
        hfd = np.array(ut_data['median'])
        hfd_std = np.array(ut_data['std'])
        fwhm = np.array(ut_data['fwhm'])
        fwhm_std = np.array(ut_data['fwhm_std'])

        # Need a nominal non-zero sigma, otherwise the curve fit fails
        hfd_std[hfd_std == 0] = 0.001
        fwhm_std[fwhm_std == 0] = 0.0001

        # HFD
        min_hfd, m1, m2, delta, best_hfd = None, None, None, None, None
        try:
            # Split into left and right
            min_i = np.where(hfd == min(hfd))[0][0]
            min_hfd = pos[min_i]
            mask_l = pos <= min_hfd
            mask_r = pos >= min_hfd

            # Raise error if not enough points on both sides
            if sum(mask_l) < 2 or sum(mask_r) < 2:
                raise ValueError('Can not fit HFD V-curve (n_l={}, n_r={})'.format(
                                 sum(mask_l), sum(mask_r)))

            # Fit straight line
            coeffs_l, _ = curve_fit(lin_func, pos[mask_l], hfd[mask_l], sigma=hfd_std[mask_l])
            coeffs_r, _ = curve_fit(lin_func, pos[mask_r], hfd[mask_r], sigma=hfd_std[mask_r])
            hfd_coeffs[ut] = (coeffs_l, coeffs_r)
            m1 = coeffs_l[0]
            m2 = coeffs_r[0]
            delta = (coeffs_r[1] / coeffs_r[0]) - (coeffs_l[1] / coeffs_l[0])

            # Find meeting point by picking a point on the line and using the autofocus function
            point = (min_hfd, lin_func(min_hfd, *coeffs_r))
            best_hfd = find_best_focus(m1, m2, delta, point[0], point[1])

        except Exception:
            print('UT{}: Error fitting to HFD data'.format(ut))
            print(traceback.format_exc())

        # FWHM
        best_fwhm = None
        try:
            # Fit parabola
            coeffs, _ = curve_fit(parabola_func, pos, fwhm, sigma=fwhm_std)
            fwhm_coeffs[ut] = coeffs

            # Find minimum
            best_fwhm = -coeffs[1] / 2 / coeffs[0]

        except Exception:
            print('UT{}: Error fitting to FWHM data'.format(ut))
            print(traceback.format_exc())

        # Add to dataframe
        fit_df.loc[ut] = pd.Series({'min_hfd': min_hfd, 'm1': m1, 'm2': m2, 'delta': delta,
                                    'best_hfd': best_hfd, 'best_fwhm': best_fwhm})

    return fit_df, hfd_coeffs, fwhm_coeffs


def plot_results(df, fit_df, hfd_coeffs, fwhm_coeffs, finish_time):
    """Plot the results of the focus run."""
    uts = list(set(list(df.index)))
    fig, axes = plt.subplots(nrows=len(uts), ncols=2, figsize=(8, 12), dpi=100)
    plt.subplots_adjust(hspace=0.7, wspace=0.1)

    fig.suptitle('Focus run results - {}'.format(finish_time), x=0.5, y=0.9)
    for i, ut in enumerate(uts):
        ut_data = df.loc[ut]
        fit_data = fit_df.loc[ut]

        # HFD plot
        try:
            ax = axes[i, 0]

            # Plot data
            mask_l = np.array(ut_data['pos']) < fit_data['min_hfd']
            mask_mid = np.array(ut_data['pos']) == fit_data['min_hfd']
            mask_r = np.array(ut_data['pos']) > fit_data['min_hfd']
            ax.errorbar(ut_data['pos'][mask_l], ut_data['median'][mask_l],
                        yerr=ut_data['std'][mask_l], color='tab:blue', fmt='.', ms=7)
            ax.errorbar(ut_data['pos'][mask_mid], ut_data['median'][mask_mid],
                        yerr=ut_data['std'][mask_mid], color='tab:green', fmt='.', ms=7)
            ax.errorbar(ut_data['pos'][mask_r], ut_data['median'][mask_r],
                        yerr=ut_data['std'][mask_r], color='tab:orange', fmt='.', ms=7)

            # Plot fit
            test_range = np.arange(min(ut_data['pos']), max(ut_data['pos']), 50)
            if hfd_coeffs[ut] is not None:
                ax.plot(test_range, lin_func(test_range, *hfd_coeffs[ut][0]),
                        color='tab:blue', ls='dashed', zorder=-1, alpha=0.5)
                ax.plot(test_range, lin_func(test_range, *hfd_coeffs[ut][1]),
                        color='tab:orange', ls='dashed', zorder=-1, alpha=0.5)
                ax.axvline(fit_data['best_hfd'], c='tab:green', ls='dotted', zorder=-1)
            else:
                ax.text(0.03, 0.15, 'Fit failed', transform=ax.transAxes,
                        bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9}, zorder=4)

            # Set labels
            ax.set_ylabel('HFD')
            if i == len(uts) - 1:
                ax.set_xlabel('Focus position')
            ax.text(0.05, 1.15, 'UT{}'.format(ut), fontweight='bold',
                    transform=ax.transAxes, zorder=9, ha='center', va='center')

            # Set limits
            ax.set_ylim(bottom=0)

        except Exception:
            print('Error making HFD plot', end='\t')
            print(traceback.format_exc())

        # FWHM plot
        try:
            ax = axes[i, 1]

            # Plot data
            ax.errorbar(ut_data['pos'], ut_data['fwhm'], yerr=ut_data['fwhm_std'],
                        color='tab:red', fmt='.', ms=7)

            # Plot fit
            if fwhm_coeffs[ut] is not None:
                ax.plot(test_range, parabola_func(test_range, *fwhm_coeffs[ut]),
                        color='tab:red', ls='dashed', zorder=-1, alpha=0.5)
                ax.axvline(fit_data['best_fwhm'], c='tab:red', ls='dotted', zorder=-1)
                if fit_data['best_hfd'] is not None:
                    ax.axvline(fit_data['best_hfd'], c='tab:green', ls='dotted', zorder=-1)
            else:
                ax.text(0.03, 0.15, 'Fit failed', transform=ax.transAxes,
                        bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9}, zorder=4)

            # Set labels
            ax.yaxis.tick_right()
            ax.yaxis.set_label_position('right')
            ax.set_ylabel('FWHM')
            if i == len(uts) - 1:
                ax.set_xlabel('Focus position')

            # Set limits
            ax.set_ylim(bottom=0)

        except Exception:
            print('Error making FWHM plot')
            print(traceback.format_exc())

    # Save the plot
    path = os.path.join(params.FILE_PATH, 'focus_data')
    ofname = 'focusplot_{}.png'.format(finish_time)
    plt.savefig(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    plt.show()


def run(fraction, steps, num_exp, exptime, filt, change_focus, no_slew, no_plot, no_confirm):
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
            image_data = get_analysis_image(exptime, filt,
                                            'Focus run', 'FOCUS', glance=False)
            foc_data = get_hfds(image_data, xslice=slice(3300, 5100), yslice=slice(1400, 4100),
                                filter_width=4, threshold=15)
            if hfds is not None:
                hfds = hfds.append(foc_data['median'])
                fwhms = fwhms.append(foc_data['fwhm'])
            else:
                hfds = foc_data['median']
                fwhms = foc_data['fwhm']
            print('HFDs:', foc_data['median'].to_dict())
            print('~~~~~~')

        # Take the smallest value of the set
        hfds = hfds.groupby(level=0)
        fwhms = fwhms.groupby(level=0)
        data = {'median': hfds.min(),
                'std': hfds.std().fillna(0.0),
                'fwhm': fwhms.min(),
                'fwhm_std': fwhms.std().fillna(0.0),
                'pos': pd.Series(get_current_focus()),
                }
        print('Best HFDs:', data['median'].to_dict())

        # Save in series list
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

    # Fit to data
    print('~~~~~~')
    print('Fitting to data...')
    fit_df, hfd_coeffs, fwhm_coeffs = fit_to_data(df)
    print(fit_df)
    ofname = 'focusfit_{}.csv'.format(finish_time)
    fit_df.to_csv(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    # Make plots
    if not no_plot:
        print('~~~~~~')
        print('Plotting results...')
        plot_results(df, fit_df, hfd_coeffs, fwhm_coeffs, finish_time)

    # Move to best position
    go = ''
    if change_focus:
        go = 'y'
    else:
        if not no_confirm:
            while go not in ['y', 'n']:
                go = input('Move to best focus? [y/n]: ')
    if go == 'y':
        print('Moving to best focus...')
        best_focus = fit_df['best_fwhm'].to_dict()
        best_focus = {ut: focus for ut, focus in best_focus.items() if not np.isnan(focus)}
        print('Best focus: ', best_focus)
        set_new_focus(best_focus)
        wait_for_focuser(best_focus, timeout=120)

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
    parser.add_argument('--change-focus', action='store_true',
                        help=('when the run is complete move to the best measured focus')
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
    change_focus = args.change_focus
    no_slew = args.no_slew
    no_plot = args.no_plot
    no_confirm = args.no_confirm

    run(fraction, steps, num_exp, exptime, filt, change_focus, no_slew, no_plot, no_confirm)
