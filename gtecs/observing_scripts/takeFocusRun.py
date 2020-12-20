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
from gtecs.focusing import get_best_focus_position, measure_focus
from gtecs.misc import NeatCloser
from gtecs.observing import (get_focuser_limits, get_focuser_positions, prepare_for_images,
                             set_focuser_positions, slew_to_radec)

from matplotlib import pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.patches import Rectangle

from mpl_toolkits.axes_grid1.inset_locator import inset_axes

import numpy as np

import pandas as pd

from scipy.interpolate import griddata
from scipy.optimize import curve_fit


DEFAULT_NFV = 4


class RestoreFocus(NeatCloser):
    """Restore the origional focus positions if anything goes wrong."""

    def __init__(self, positions):
        super(RestoreFocus, self).__init__('Script')
        self.positions = positions

    def tidy_up(self):
        """Restore the original focus."""
        print('Interrupt caught: Restoring original focus positions...')
        set_focuser_positions(self.positions)


def calculate_positions(fraction, steps):
    """Calculate the positions for the focus run."""
    # Get the current focus positions, and the maximum limit (assuming minimum is 0)
    current = get_focuser_positions()
    limits = get_focuser_limits()

    all_positions = {}
    for ut in limits:
        print('UT{}: current position={}/{}'.format(ut, current[ut], limits[ut]))
        # Fudge for RASAs having wider ranges
        if params.UT_DICT[ut]['FOCUSER']['CLASS'] == 'RASA':
            ut_fraction = fraction * 2.5
        else:
            ut_fraction = fraction

        # Calculate the deltas
        width = int((limits[ut] * ut_fraction) / 2)
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


def fit_to_data(df, nfvs=None):
    """Fit to a series of HFD measurements."""
    uts = list(set(list(df.index)))
    fit_df = pd.DataFrame(columns=['pivot_pos', 'n_l', 'n_r',
                                   'm_l', 'm_r', 'c_l', 'c_r',
                                   'delta_x', 'cross_pos'],
                          index=uts)
    if nfvs is None:
        nfvs = {ut: DEFAULT_NFV for ut in uts}

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

            # Mask to exclude any points below the near-focus value
            # (the NFV is supposed to mark where the V-curve is no-longer linear)
            # or above double the NFV
            if ut not in nfvs:
                nfvs[ut] = DEFAULT_NFV
            mask = (hfd > nfvs[ut]) & (hfd < 2 * nfvs[ut])

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
            else:
                print('UT{}: Can not fit to left side of V-curve (n_l={})'.format(ut, n_l))
                m_l, c_l = None, None

            if n_r > 1:
                coeffs_r, _ = curve_fit(lin_func, pos[mask_r], hfd[mask_r], sigma=hfd_std[mask_r])
                m_r, c_r = coeffs_r
            else:
                print('UT{}: Can not fit to right side of V-curve (n_r={})'.format(ut, n_r))
                m_r, c_r = None, None

            # Find crossing point
            if m_l is not None and m_r is not None:
                delta_x = (c_r / m_r) - (c_l / m_l)

                # Find meeting point by picking a point on the line and using the autofocus function
                point = (pivot_pos, lin_func(pivot_pos, m_r, c_r))
                cross_pos = int(get_best_focus_position(point[0], point[1], m_l, m_r, delta_x))
            else:
                delta_x, cross_pos = None, None

            # Add to dataframe
            fit_df.loc[ut] = pd.Series({'pivot_pos': pivot_pos,
                                        'n_l': n_l,
                                        'n_r': n_r,
                                        'm_l': m_l,
                                        'm_r': m_r,
                                        'c_l': c_l,
                                        'c_r': c_r,
                                        'delta_x': delta_x,
                                        'cross_pos': cross_pos})

        except Exception:
            print('UT{}: Error fitting to HFD data'.format(ut))
            print(traceback.format_exc())

    return fit_df


def plot_results(df, fit_df, nfvs=None, finish_time=None, save_plot=True):
    """Plot the results of the focus run."""
    uts = list(set(list(df.index)))
    if finish_time is None:
        finish_time = Time.now().isot
    if nfvs is None:
        nfvs = {ut: DEFAULT_NFV for ut in uts}

    fig, axes = plt.subplots(nrows=math.ceil(len(uts) / 4), ncols=4, figsize=(16, 6), dpi=100)
    plt.subplots_adjust(hspace=0.15, wspace=0.2)
    axes = axes.flatten()

    fig.suptitle('Focus run results - {}'.format(finish_time), x=0.5, y=0.92)
    fig.patch.set_facecolor('w')

    for i, ut in enumerate(uts):
        ut_data = df.loc[ut]
        fit_data = fit_df.loc[ut]

        try:
            ax = axes[i]

            # Plot data
            mask = (np.array(ut_data['hfd']) > nfvs[ut]) & (np.array(ut_data['hfd']) < 2 * nfvs[ut])
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
            ax.axhline(nfvs[ut], c='tab:red', ls='dotted', lw=1, zorder=-1)
            ax.axhline(2 * nfvs[ut], c='tab:red', ls='dotted', lw=1, zorder=-1)

            # Set limits (lock the x-limit before we add the fit lines)
            x_lim = ax.get_xlim()
            ax.set_xlim(*x_lim)
            ax.set_ylim(bottom=0, top=14)

            # Plot fits (if they worked)
            test_range = np.arange(min(ut_data['pos']) * 0.9, max(ut_data['pos']) * 1.1, 50)
            if fit_data['m_l'] is not None and fit_data['c_l'] is not None:
                ax.plot(test_range, lin_func(test_range, fit_data['m_l'], fit_data['c_l']),
                        color='tab:blue', ls='dashed', zorder=-1, alpha=0.5)
            if fit_data['m_r'] is not None and fit_data['c_r'] is not None:
                ax.plot(test_range, lin_func(test_range, fit_data['m_r'], fit_data['c_r']),
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
            if i >= len(axes) - 4:
                ax.set_xlabel('Focus position')
            ax.text(0.02, 0.915, 'UT{}'.format(ut), fontweight='bold',
                    bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9},
                    transform=ax.transAxes, ha='left', zorder=2)
            ax.text(0.98, 0.915, params.UT_DICT[ut]['OTA']['SERIAL'], fontweight='bold',
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


def plot_corners(df, fit_df, region_slices, nfvs=None, finish_time=None, save_plot=True):
    """Plot the results of the focus run with measure_corners=True."""
    uts = list(set(list(df.index)))
    if finish_time is None:
        finish_time = Time.now().isot
    if nfvs is None:
        nfvs = {ut: DEFAULT_NFV for ut in uts}

    region_name = {0: 'centre',
                   1: 'lower-left',
                   2: 'lower-right',
                   3: 'upper-left',
                   4: 'upper-right',
                   }

    region_colour = {0: 'tab:blue',
                     1: 'tab:orange',
                     2: 'tab:green',
                     3: 'tab:red',
                     4: 'tab:purple',
                     }

    region_to_subplot = {0: 1,
                         1: 3,
                         2: 5,
                         3: 0,
                         4: 2,
                         }

    for ut in uts:
        ut_data = df.loc[ut]

        fig, axes = plt.subplots(2, 3, figsize=(12, 6), dpi=100)
        plt.subplots_adjust(hspace=0.2, wspace=0.15)

        fig.suptitle('Focus run results - {} - UT{}'.format(finish_time, ut), x=0.5, y=0.92)
        fig.patch.set_facecolor('w')
        axes = axes.flatten()

        # Region V-curve plots
        for i in range(5):
            try:
                region_data = ut_data[ut_data['region'] == i]
                fit_data = fit_df[fit_df['region'] == i].loc[ut]

                ax = axes[region_to_subplot[i]]
                colour = region_colour[i]

                # Plot data
                mask = ((np.array(region_data['hfd']) > nfvs[ut]) &
                        (np.array(region_data['hfd']) < 2 * nfvs[ut]))
                mask_l = mask & (np.array(region_data['pos']) < fit_data['pivot_pos'])
                mask_m = np.invert(mask) | (np.array(region_data['pos']) == fit_data['pivot_pos'])
                mask_r = mask & (np.array(region_data['pos']) > fit_data['pivot_pos'])
                ax.errorbar(region_data['pos'][mask_l], region_data['hfd'][mask_l],
                            yerr=region_data['hfd_std'][mask_l],
                            color=colour, fmt='.', ms=7, zorder=1)
                ax.errorbar(region_data['pos'][mask_m], region_data['hfd'][mask_m],
                            yerr=region_data['hfd_std'][mask_m],
                            color='0.7', fmt='.', ms=7, zorder=1)
                ax.errorbar(region_data['pos'][mask_r], region_data['hfd'][mask_r],
                            yerr=region_data['hfd_std'][mask_r],
                            color=colour, fmt='.', ms=7, zorder=1)
                # ax.axvline(fit_data['pivot_pos'], c='0.7', ls='dotted', zorder=-1)
                ax.axhline(nfvs[ut], c='0.8', ls='dashed', lw=1, zorder=-1)
                ax.axhline(2 * nfvs[ut], c='0.8', ls='dashed', lw=1, zorder=-1)

                # Set limits (lock the x-limit before we add the fit lines)
                x_lim = ax.get_xlim()
                ax.set_xlim(*x_lim)
                ax.set_ylim(bottom=0, top=14)

                # Plot fits (if they worked)
                test_range = np.arange(min(region_data['pos']) * 0.9,
                                       max(region_data['pos']) * 1.1,
                                       50)
                if fit_data['m_l'] is not None and fit_data['c_l'] is not None:
                    ax.plot(test_range, lin_func(test_range, fit_data['m_l'], fit_data['c_l']),
                            color=colour, ls='dashed', zorder=-1, alpha=0.5)
                if fit_data['m_r'] is not None and fit_data['c_r'] is not None:
                    ax.plot(test_range, lin_func(test_range, fit_data['m_r'], fit_data['c_r']),
                            color=colour, ls='dashed', zorder=-1, alpha=0.5)
                if not np.isnan(fit_data['cross_pos']):
                    ax.axvline(fit_data['cross_pos'], c=colour, ls='dashed', zorder=-1)
                else:
                    txt = 'Fit failed ($n_L={:.0f}$, $n_R={:.0f}$)'.format(
                        fit_data['n_l'], fit_data['n_r'])
                    ax.text(0.02, 0.8, txt, fontweight='normal', c='tab:red',
                            transform=ax.transAxes, ha='left', zorder=2.1,
                            bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9})

                # Plot crossing point for other subplots
                for j in region_to_subplot.values():
                    if j == region_to_subplot[i]:
                        continue
                    if not np.isnan(fit_data['cross_pos']):
                        axes[j].axvline(fit_data['cross_pos'], c=colour, ls='dotted', alpha=0.5,
                                        zorder=-2)

                # Set labels
                if region_to_subplot[i] % 3 == 0:
                    ax.set_ylabel('HFD')
                if region_to_subplot[i] >= len(axes) - 3:
                    ax.set_xlabel('Focus position')
                ax.text(0.5, 0.98, region_name[i], fontweight='bold', color=colour,
                        bbox={'fc': 'w', 'lw': 0, 'alpha': 0.5},
                        transform=ax.transAxes, ha='center', va='top', zorder=2)

            except Exception:
                print('UT{}: Error making region {} plot'.format(ut, i))
                print(traceback.format_exc())

        # Tilt plot
        try:
            ax = axes[4]

            # Plot points
            region_x = [xs.start + (xs.stop - xs.start) / 2 for xs, ys in region_slices]
            region_y = [ys.start + (ys.stop - ys.start) / 2 for xs, ys in region_slices]
            cross_pos = np.array([fit_df[fit_df['region'] == i].loc[ut]['cross_pos']
                                  for i in range(5)])
            cross_pos_relative = cross_pos - cross_pos[0]
            ax.scatter(region_x, region_y, c=cross_pos_relative)

            # Plot region patches
            patches = [Rectangle((xs.start, ys.start), xs.stop - xs.start, ys.stop - ys.start)
                       for xs, ys in region_slices]
            pc = PatchCollection(patches)
            pc.set_edgecolor([region_colour[i] for i in range(5)])
            pc.set_facecolor('none')
            pc.set_linewidth(1.5)
            pc.set_linestyle('dotted')
            ax.add_collection(pc)

            # Plot contour
            points_x = [8304 / 2, 0, 8304, 0, 8304]
            points_y = [6220 / 2, 0, 0, 6220, 6220]
            grid_x, grid_y = np.meshgrid(np.linspace(0, 8304, 20), np.linspace(0, 6220, 20))
            cross_pos_fit = griddata((points_x, points_y), cross_pos_relative, (grid_x, grid_y),
                                     method='cubic')
            pcm = ax.contourf(grid_x, grid_y, cross_pos_fit, zorder=-2, alpha=0.3, levels=6)

            # Plot colorbar
            axi = inset_axes(ax, width='100%', height='7%', loc='center',
                             bbox_to_anchor=(0, -0.57, 1, 1), bbox_transform=ax.transAxes,)
            cb = fig.colorbar(pcm, cax=axi, orientation='horizontal', pad=0.3)
            cb.ax.tick_params(labelsize=7)

            # Set limits & labels
            ax.set_xlim(0, 8304)
            ax.set_ylim(0, 6220)
            ax.tick_params(left=False, labelleft=False, bottom=False, labelbottom=False)

        except Exception:
            print('UT{}: Error making tilt plot'.format(ut))
            print(traceback.format_exc())

        # Save the plot
        if save_plot:
            path = os.path.join(params.FILE_PATH, 'focus_data')
            ofname = 'focusplot_{}_UT{}.png'.format(finish_time, ut)
            plt.savefig(os.path.join(path, ofname))
            print('Saved to {}'.format(os.path.join(path, ofname)))

        plt.show()


def run(fraction, steps, num_exp=3, exptime=30, filt='L', nfvs=None,
        measure_corners=False, go_to_best=False, no_slew=False, no_plot=False, no_confirm=False):
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

    # Store the starting focus
    print('~~~~~~')
    initial_positions = get_focuser_positions()
    print('Initial positions:', initial_positions)

    # Define measurement regions
    if measure_corners:
        regions = [(slice(2500, 6000), slice(1500, 4500)),  # centre (default)
                   (slice(200, 2500), slice(100, 1500)),    # bottom-left
                   (slice(6000, 8000), slice(100, 1500)),   # bottom-right
                   (slice(200, 2500), slice(4500, 6000)),   # top-left
                   (slice(6000, 8000), slice(4500, 6000)),  # top-right
                   ]
    else:
        regions = None

    # Measure the HFDs at each position calculated earlier
    all_data = []
    for i, new_positions in positions.iterrows():
        print('~~~~~~')
        print('## RUN {} of {}'.format(i + 1, len(positions)))
        print('Moving focusers...')
        set_focuser_positions(new_positions.to_dict(), timeout=120)
        print('New positions:', get_focuser_positions())
        print('Taking {} measurements at new focus position...'.format(num_exp))
        foc_data = measure_focus(num_exp, exptime, filt, target_name, regions=regions)
        if not isinstance(foc_data, pd.DataFrame):
            # Concat region list
            foc_data = pd.concat(foc_data)
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
    if nfvs is None:
        nfvs = {ut: DEFAULT_NFV for ut in sorted(set(df.index))}
    print('Fit results:')
    if not measure_corners:
        fit_df = fit_to_data(df, nfvs)
        print(fit_df)
    else:
        fit_df = []
        for i in range(len(regions)):
            region_df = df[df['region'] == i]
            region_fit_df = fit_to_data(region_df, nfvs)
            print('region {}:'.format(i))
            print(region_fit_df)
            region_fit_df.insert(0, 'region', i)
            fit_df.append(region_fit_df)
        fit_df = pd.concat(fit_df)

    ofname = 'focusfit_{}.csv'.format(finish_time)
    fit_df.to_csv(os.path.join(path, ofname))
    print('Saved to {}'.format(os.path.join(path, ofname)))

    # Make plots
    if not no_plot:
        print('~~~~~~')
        print('Plotting results...')
        if not measure_corners:
            plot_results(df, fit_df, nfvs, finish_time)
        else:
            # Still make both plots
            plot_results(df[df['region'] == 0], fit_df[fit_df['region'] == 0], nfvs, finish_time)
            plot_corners(df, fit_df, regions, nfvs, finish_time)

    # Get best positions
    if not measure_corners:
        best_focus = fit_df['cross_pos'].to_dict()
    else:
        # for now take the best position in the central region (region 0)
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
    parser.add_argument('--corners', action='store_true',
                        help=('measure focus position in the corners as well as the centre')
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
    measure_corners = args.corners
    go_to_best = args.go_to_best
    no_slew = args.no_slew
    no_plot = args.no_plot
    no_confirm = args.no_confirm

    # Get the near-focus values for each UT
    nfvs = {ut: params.AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE'] for ut in params.AUTOFOCUS_PARAMS}
    for ut in params.UTS_WITH_FOCUSERS:
        if ut not in nfvs:
            nfvs[ut] = DEFAULT_NFV

    # If something goes wrong we need to restore the origional focus
    initial_positions = get_focuser_positions()
    try:
        RestoreFocus(initial_positions)
        run(fraction, steps, num_exp, exptime, filt, nfvs,
            measure_corners, go_to_best, no_slew, no_plot, no_confirm)
    except Exception:
        print('Error caught: Restoring original focus positions...')
        set_focuser_positions(initial_positions)
        raise
