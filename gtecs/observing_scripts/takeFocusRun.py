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


def fit_to_data(df, hfd_limits=None):
    """Fit to a series of HFD measurements."""
    uts = list(set(list(df.index)))
    fit_df = pd.DataFrame(columns=['pivot_pos', 'n_l', 'n_r', 'm_l', 'm_r', 'delta_x', 'cross_pos'],
                          index=uts)
    fit_coeffs = {ut: [None, None] for ut in uts}
    if hfd_limits is None:
        hfd_limits = {ut: (4, 12) for ut in uts}

    for ut in uts:
        try:
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

            # Mask to only include points within the given limits
            # (the lower limit should be the NFV, where the V-curve is no longer linear)
            min_hfd, max_hfd = hfd_limits[ut]
            mask = (hfd > min_hfd) & (hfd < max_hfd)

            # Split into left and right
            min_i = np.where(hfd == np.nanmin(hfd))[0][0]
            pivot_pos = pos[min_i]
            mask_l = mask & (pos < pivot_pos)
            mask_r = mask & (pos > pivot_pos)
            n_l = sum(mask_l)
            n_r = sum(mask_r)

            # Fit straight lines
            if n_l > 1:
                coeffs_l, _ = curve_fit(lin_func, pos[mask_l], hfd[mask_l], sigma=hfd_std[mask_l])
                m_l, c_l = coeffs_l
                fit_coeffs[ut][0] = (m_l, c_l)
            else:
                print('UT{}: Can not fit to left side of V-curve (n_l={})'.format(ut, n_l))
                m_l, c_l = None, None

            if n_r > 1:
                coeffs_r, _ = curve_fit(lin_func, pos[mask_r], hfd[mask_r], sigma=hfd_std[mask_r])
                m_r, c_r = coeffs_r
                fit_coeffs[ut][1] = (m_r, c_r)
            else:
                print('UT{}: Can not fit to right side of V-curve (n_r={})'.format(ut, n_r))
                m_r, c_r = None, None

            # Find crossing point
            if m_l is not None and m_r is not None:
                delta_x = (c_r / m_r) - (c_l / m_l)

                # Find meeting point by picking a point on the line and using the autofocus function
                point = (pivot_pos, lin_func(pivot_pos, m_r, c_r))
                cross_pos = int(get_best_focus_position(m_l, m_r, delta_x, point[0], point[1]))
            else:
                delta_x, cross_pos = None, None

            # Add to dataframe
            fit_df.loc[ut] = pd.Series({'pivot_pos': pivot_pos,
                                        'n_l': n_l,
                                        'n_r': n_r,
                                        'm_l': m_l,
                                        'm_r': m_r,
                                        'delta_x': delta_x,
                                        'cross_pos': cross_pos})

        except Exception:
            print('UT{}: Error fitting to HFD data'.format(ut))
            print(traceback.format_exc())

    return fit_df, fit_coeffs


def plot_results(df, fit_df, fit_coeffs, hfd_limits=None, finish_time=None, save_plot=True):
    """Plot the results of the focus run."""
    uts = list(set(list(df.index)))
    if hfd_limits is None:
        hfd_limits = {ut: (4, 12) for ut in uts}
    if finish_time is None:
        finish_time = Time.now()

    fig, axes = plt.subplots(nrows=math.ceil(len(uts) / 4), ncols=4, figsize=(16, 6), dpi=100)
    plt.subplots_adjust(hspace=0.15, wspace=0.2)

    fig.suptitle('Focus run results - {}'.format(finish_time), x=0.5, y=0.92)
    for i, ut in enumerate(uts):
        ut_data = df.loc[ut]
        fit_data = fit_df.loc[ut]

        # HFD plot
        try:
            ax = axes.flatten()[i]

            # Plot data
            min_hfd, max_hfd = hfd_limits[ut]
            mask = (np.array(ut_data['hfd']) > min_hfd) & (np.array(ut_data['hfd']) < max_hfd)
            mask_l = mask & (np.array(ut_data['pos']) < fit_data['pivot_pos'])
            mask_m = np.invert(mask) | (np.array(ut_data['pos']) == fit_data['pivot_pos'])
            mask_r = mask & (np.array(ut_data['pos']) > fit_data['pivot_pos'])
            ax.errorbar(ut_data['pos'][mask_l], ut_data['hfd'][mask_l],
                        yerr=ut_data['hfd_std'][mask_l],
                        color='tab:blue', fmt='.', ms=7, zorder=1)
            ax.errorbar(ut_data['pos'][mask_m], ut_data['hfd'][mask_m],
                        yerr=ut_data['hfd_std'][mask_m],
                        color='0.7', fmt='.', ms=7, zorder=1)
            ax.errorbar(ut_data['pos'][mask_r], ut_data['hfd'][mask_r],
                        yerr=ut_data['hfd_std'][mask_r],
                        color='tab:orange', fmt='.', ms=7, zorder=1)
            ax.axvline(fit_data['pivot_pos'], c='0.7', ls='dotted', zorder=-1)
            ax.axhline(min_hfd, c='tab:red', ls='dotted', lw=1, zorder=-1)
            ax.axhline(max_hfd, c='tab:red', ls='dotted', lw=1, zorder=-1)

            # Set limits (lock the x-limit before we add the fit lines)
            x_lim = ax.get_xlim()
            ax.set_xlim(*x_lim)
            ax.set_ylim(bottom=0, top=14)

            # Plot fits (if they worked)
            test_range = np.arange(min(ut_data['pos']) * 0.9, max(ut_data['pos']) * 1.1, 50)
            if fit_coeffs[ut][0] is not None:
                ax.plot(test_range, lin_func(test_range, *fit_coeffs[ut][0]),
                        color='tab:blue', ls='dashed', zorder=-1, alpha=0.5)
            if fit_coeffs[ut][1] is not None:
                ax.plot(test_range, lin_func(test_range, *fit_coeffs[ut][1]),
                        color='tab:orange', ls='dashed', zorder=-1, alpha=0.5)
            if not np.isnan(fit_data['cross_pos']):
                ax.axvline(fit_data['cross_pos'], c='tab:green', ls='dotted', zorder=-1)
            else:
                txt = 'Fit failed ($n_L={:.0f}$, $n_R={:.0f}$)'.format(
                    fit_data['n_l'], fit_data['n_r'])
                ax.text(0.02, 0.8, txt, fontweight='normal', c='tab:red',
                        transform=ax.transAxes, ha='left', zorder=2.1,
                        bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9})

            # Set labels
            if i % 4 == 0:
                ax.set_ylabel('HFD')
            if i >= len(axes.flatten()) - 4:
                ax.set_xlabel('Focus position')
            ax.text(0.02, 0.915, 'UT{}'.format(ut), fontweight='bold',
                    bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9},
                    transform=ax.transAxes, ha='left', zorder=2)
            ax.text(0.98, 0.915, params.UT_DICT[ut]['SERIAL'], fontweight='bold',
                    bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9},
                    transform=ax.transAxes, ha='right', zorder=2)

        except Exception:
            print('UT{}: Error making HFD plot'.format(ut))
            print(traceback.format_exc())

    # Save the plot
    if save_plot:
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
        if num_exp > 1:
            print('Best HFDs:', hfds.round(1).to_dict())

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
    hfd_limits = {ut: (4, 12) for ut in set(df.index)}
    fit_df, fit_coeffs = fit_to_data(df, hfd_limits)
    print('Fit results:')
    print(fit_df)
    ofname = 'focusfit_{}.csv'.format(finish_time)
    fit_df.to_csv(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    # Make plots
    if not no_plot:
        print('~~~~~~')
        print('Plotting results...')
        plot_results(df, fit_df, fit_coeffs, hfd_limits, finish_time)

    # Get best positions
    best_focus = fit_df['cross_pos'].to_dict()
    best_focus = {ut: int(focus) for ut, focus in best_focus.items() if not np.isnan(focus)}
    for ut in fit_df.index:
        if ut not in best_focus:
            # Use the minimum point if the fit failed
            pivot_pos = fit_df['pivot_pos'].to_dict()[ut]
            if not np.isnan(pivot_pos):
                best_focus[ut] = int(pivot_pos)
    best_focus = {ut: best_focus[ut] for ut in sorted(best_focus.keys())}

    # Move to best positions?
    print('Current focus: ', get_focuser_positions())
    print('Best focus:    ', best_focus)
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
    initial_positions = get_focuser_positions()
    try:
        RestoreFocus(initial_positions)
        run(fraction, steps, num_exp, exptime, filt, go_to_best, no_slew, no_plot, no_confirm)
    except Exception:
        print('Error caught: Restoring original focus positions...')
        set_focuser_positions(initial_positions)
        raise
