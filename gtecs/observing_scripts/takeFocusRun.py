#!/usr/bin/env python3
"""Script to take a series of images running through focuser positions.

It assumes you're already on a reasonable patch of sky and that you're
already focused (see autoFocus script).
"""

import math
import os
import sys
import traceback
from argparse import ArgumentParser, ArgumentTypeError

from astropy.time import Time

from gtecs import params
from gtecs.catalogs import focus_star
from gtecs.observing import (get_focuser_limits, get_focuser_positions, prepare_for_images,
                             set_focuser_positions, slew_to_radec)
from gtecs.observing_scripts.autoFocus import (RestoreFocus, get_best_focus_position, measure_focus)

from matplotlib import pyplot as plt

import numpy as np

import pandas as pd

from scipy.optimize import curve_fit


def calculate_positions(fraction, steps):
    """Calculate the positions for the focus run."""
    # Get the current focus positions, and the maximum limit (assuming minimum is 0)
    current = get_focuser_positions()
    limits = get_focuser_limits()

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
            print('  WARNING: {} position(s) is above maximum ({})'.format(n_bad, limits[ut]))
            positions[positions > limits[ut]] = [limits[ut]] * n_bad
        print('  {} positions:'.format(len(positions)), positions)

        all_positions[ut] = positions

    return pd.DataFrame(all_positions)


def lin_func(x, m, c):
    """Fit HFDs."""
    return m * x + c


def fit_to_data(df):
    """Fit to a series of HFD measurements."""
    uts = list(set(list(df.index)))
    fit_df = pd.DataFrame(columns=['pivot_pos', 'm_l', 'm_r', 'delta_x', 'cross_pos'],
                          index=uts)
    fit_coeffs = {ut: None for ut in uts}

    for ut in uts:
        # Get data arrays
        ut_data = df.loc[ut]
        pos = np.array(ut_data['pos'])
        hfd = np.array(ut_data['hfd'])
        hfd_std = np.array(ut_data['hfd_std'])

        # Mask any failed measurements (e.g. not enough objects)
        mask = np.invert(np.isnan(hfd))
        pos = pos[mask]
        hfd = hfd[mask]
        hfd_std = hfd_std[mask]

        # Need a nominal non-zero sigma, otherwise the curve fit fails
        hfd_std[hfd_std == 0] = 0.001

        # HFD
        pivot_pos, m_l, m_r, delta_x, cross_pos = None, None, None, None, None
        try:
            # Split into left and right
            min_i = np.where(hfd == min(hfd))[0][0]
            pivot_pos = pos[min_i]
            mask_l = pos <= pivot_pos
            mask_r = pos >= pivot_pos

            # Raise error if not enough points on both sides
            if sum(mask_l) < 2 or sum(mask_r) < 2:
                raise ValueError('Can not fit HFD V-curve (n_l={}, n_r={})'.format(
                                 sum(mask_l), sum(mask_r)))

            # Fit straight line
            coeffs_l, _ = curve_fit(lin_func, pos[mask_l], hfd[mask_l], sigma=hfd_std[mask_l])
            coeffs_r, _ = curve_fit(lin_func, pos[mask_r], hfd[mask_r], sigma=hfd_std[mask_r])
            fit_coeffs[ut] = (coeffs_l, coeffs_r)
            m_l, c_l = coeffs_l
            m_r, c_r = coeffs_r
            delta_x = (c_r / m_r) - (c_l / m_l)

            # Find meeting point by picking a point on the line and using the autofocus function
            point = (pivot_pos, lin_func(pivot_pos, *coeffs_r))
            cross_pos = get_best_focus_position(m_l, m_r, delta_x, point[0], point[1])

        except Exception:
            print('UT{}: Error fitting to HFD data'.format(ut))
            print(traceback.format_exc())

        # Add to dataframe
        fit_df.loc[ut] = pd.Series({'pivot_pos': pivot_pos,
                                    'm_l': m_l,
                                    'm_r': m_r,
                                    'delta_x': delta_x,
                                    'cross_pos': cross_pos})

    return fit_df, fit_coeffs


def plot_results(df, fit_df, fit_coeffs, finish_time):
    """Plot the results of the focus run."""
    uts = list(set(list(df.index)))
    fig, axes = plt.subplots(nrows=math.ceil(len(uts) / 4), ncols=4, figsize=(16, 6), dpi=150)
    plt.subplots_adjust(hspace=0.15, wspace=0.2)

    fig.suptitle('Focus run results - {}'.format(finish_time), x=0.5, y=0.92)
    for i, ut in enumerate(uts):
        ut_data = df.loc[ut]
        fit_data = fit_df.loc[ut]

        # HFD plot
        try:
            ax = axes.flatten()[i]

            # Plot data
            mask_l = np.array(ut_data['pos']) < fit_data['pivot_pos']
            mask_mid = np.array(ut_data['pos']) == fit_data['pivot_pos']
            mask_r = np.array(ut_data['pos']) > fit_data['pivot_pos']
            ax.errorbar(ut_data['pos'][mask_l], ut_data['hfd'][mask_l],
                        yerr=ut_data['hfd_std'][mask_l], color='tab:blue', fmt='.', ms=7)
            ax.errorbar(ut_data['pos'][mask_mid], ut_data['hfd'][mask_mid],
                        yerr=ut_data['hfd_std'][mask_mid], color='tab:green', fmt='.', ms=7)
            ax.errorbar(ut_data['pos'][mask_r], ut_data['hfd'][mask_r],
                        yerr=ut_data['hfd_std'][mask_r], color='tab:orange', fmt='.', ms=7)

            # Plot fit
            test_range = np.arange(min(ut_data['pos']), max(ut_data['pos']), 50)
            if fit_coeffs[ut] is not None:
                ax.plot(test_range, lin_func(test_range, *fit_coeffs[ut][0]),
                        color='tab:blue', ls='dashed', zorder=-1, alpha=0.5)
                ax.plot(test_range, lin_func(test_range, *fit_coeffs[ut][1]),
                        color='tab:orange', ls='dashed', zorder=-1, alpha=0.5)
                ax.axvline(fit_data['cross_pos'], c='tab:green', ls='dotted', zorder=-1)
            else:
                ax.text(0.03, 0.15, 'Fit failed', transform=ax.transAxes,
                        bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9}, zorder=4)

            # Set labels
            if i % 4 == 0:
                ax.set_ylabel('HFD')
            if i >= len(axes.flatten()) - 4:
                ax.set_xlabel('Focus position')
            ax.text(0.07, 0.95, 'UT{}'.format(ut), fontweight='bold',
                    transform=ax.transAxes, zorder=9, ha='center', va='center')

            # Set limits
            ax.set_ylim(bottom=0)

        except Exception:
            print('Error making HFD plot', end='\t')
            print(traceback.format_exc())

    # Save the plot
    path = os.path.join(params.FILE_PATH, 'focus_data')
    ofname = 'focusplot_{}.png'.format(finish_time)
    plt.savefig(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    plt.show()


def run(fraction, steps, num_exp=3, exptime=30, filt='L',
        go_to_best=False, no_slew=False, no_plot=False, no_confirm=False):
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
        print('Slewing to target {}...'.format(star))
        target_name = star.name
        coordinate = star.coord_now()
        slew_to_radec(coordinate.ra.deg, coordinate.dec.deg, timeout=120)
        print('Reached target')
    else:
        target_name = 'Focus run'

    # Store the current focus
    print('~~~~~~')
    initial_positions = get_focuser_positions()
    print('Initial positions:', initial_positions)

    # Measure the HFDs at each position calculated earlier
    all_data = []
    for i, new_positions in positions.iterrows():
        print('~~~~~~')
        print('## RUN {} of {}'.format(i + 1, len(positions)))
        print('Moving focusers...')
        set_focuser_positions(new_positions.to_dict(), timeout=120)
        print('New positions:', get_focuser_positions())
        print('Taking {} measurements at new focus position...'.format(num_exp))
        foc_data = measure_focus(num_exp, exptime, filt, target_name)
        hfds = foc_data['hfd']
        print('Best HFDs:', hfds.to_dict())

        # Save data in list
        all_data.append(foc_data)
    df = pd.concat(all_data)

    print('~~~~~~')
    print('Exposures finished')
    finish_time = Time.now().isot

    # Restore the origional focus
    print('~~~~~~')
    print('Restoring original focuser positions...')
    set_focuser_positions(initial_positions, timeout=120)
    print('Restored focus: ', get_focuser_positions())

    # Write out data
    print('~~~~~~')
    print('Writing out data to file...')
    path = os.path.join(params.FILE_PATH, 'focus_data')
    ofname = 'focusdata_{}.csv'.format(finish_time)
    df.to_csv(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    # Fit to data
    print('~~~~~~')
    print('Fitting to data...')
    fit_df, fit_coeffs = fit_to_data(df)
    print(fit_df)
    ofname = 'focusfit_{}.csv'.format(finish_time)
    fit_df.to_csv(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    # Make plots
    if not no_plot:
        print('~~~~~~')
        print('Plotting results...')
        plot_results(df, fit_df, fit_coeffs, finish_time)

    # Move to best position?
    best_focus = fit_df['cross_pos'].to_dict()
    best_focus = {ut: int(focus) for ut, focus in best_focus.items() if not np.isnan(focus)}
    print('Current focus: ', get_focuser_positions())
    print('Best focus: ', best_focus)
    go = ''
    if go_to_best:
        go = 'y'
    else:
        if not no_confirm:
            while go not in ['y', 'n']:
                go = input('Move to best focus? [y/n]: ')
    if go == 'y':
        print('Moving to best focus...')
        set_focuser_positions(best_focus, timeout=120)

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
    parser.add_argument('--go-to-best', action='store_true',
                        help=('when the run is complete move to the best focus position')
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
    go_to_best = args.go_to_best
    no_slew = args.no_slew
    no_plot = args.no_plot
    no_confirm = args.no_confirm

    # If something goes wrong we need to restore the origional focus
    try:
        initial_positions = get_focuser_positions()
        RestoreFocus(initial_positions)
        run(fraction, steps, num_exp, exptime, filt, go_to_best, no_slew, no_plot, no_confirm)
    except Exception:
        print('Error caught: Restoring original focus positions...')
        set_focuser_positions(initial_positions)
        raise
