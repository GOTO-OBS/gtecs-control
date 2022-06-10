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

from gtecs.common.system import NeatCloser
from gtecs.control import params
from gtecs.control.catalogs import focus_star
from gtecs.control.focusing import get_best_focus_position, measure_focus
from gtecs.control.observing import (get_analysis_image, get_focuser_limits, get_focuser_positions,
                                     prepare_for_images, set_focuser_positions,
                                     slew_to_altaz, slew_to_radec)

from matplotlib import pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.patches import Rectangle

from mpl_toolkits.axes_grid1.inset_locator import inset_axes

import numpy as np

import pandas as pd

from scipy.interpolate import griddata
from scipy.optimize import curve_fit


DEFAULT_NFV = 4


class RestoreFocusCloser(NeatCloser):
    """Restore the original focus positions if anything goes wrong."""

    def __init__(self, positions):
        super().__init__(taskname='Script')
        self.positions = positions

    def tidy_up(self):
        """Restore the original focus."""
        print('Interrupt caught: Restoring original focus positions...')
        set_focuser_positions(self.positions)


def calculate_positions(range_frac, steps, scale_factors=None):
    """Calculate the positions for the focus run."""
    # Get the current focus positions, and the maximum limit (assuming minimum is 0)
    current = get_focuser_positions()
    limits = get_focuser_limits()
    if scale_factors is None:
        scale_factors = {ut: 1 for ut in current}
    else:
        scale_factors = {ut: scale_factors[ut] if ut in scale_factors else 1 for ut in current}

    all_positions = {}
    for ut in current:
        print('UT{}: current position={}/{}'.format(ut, current[ut], limits[ut]))

        # Calculate the deltas
        width = int((limits[ut] * range_frac * scale_factors[ut]) / 2)
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


def estimate_time(steps, num_exp, exp_time, binning, corners=False):
    """Estimate how long it will take to complete the run."""
    READOUT_TIME_PER_EXPOSURE = 30 / (binning ** 2)
    ANALYSIS_TIME_PER_EXPOSURE = 15 if not corners else 30  # it takes longer with more regions
    MOVING_TIME_PER_STEP = 20
    FAR_MOVING_TIME = 40  # Time to move out and back from the extreme ends of the run

    time_per_exposure = exp_time + READOUT_TIME_PER_EXPOSURE + ANALYSIS_TIME_PER_EXPOSURE
    time_per_step = MOVING_TIME_PER_STEP + (time_per_exposure * num_exp)
    total_steps = steps * 2 + 1
    total_time = FAR_MOVING_TIME + time_per_step * total_steps + FAR_MOVING_TIME

    return total_time


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
            try:
                ax.text(0.98, 0.915, params.UT_DICT[ut]['OTA']['SERIAL'], fontweight='bold',
                        bbox={'fc': 'w', 'lw': 0, 'alpha': 0.9},
                        transform=ax.transAxes, ha='right', zorder=2)
            except KeyError:
                pass

        except Exception:
            print('UT{}: Error making HFD plot'.format(ut))
            print(traceback.format_exc())

    # Save the plot
    if save_plot:
        path = os.path.join(params.FILE_PATH, 'focus_data')
        filename = 'focusplot_{}.png'.format(finish_time)
        plt.savefig(os.path.join(path, filename))
        print('Saved to {}'.format(os.path.join(path, filename)))

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
            filename = 'focusplot_{}_UT{}.png'.format(finish_time, ut)
            plt.savefig(os.path.join(path, filename))
            print('Saved to {}'.format(os.path.join(path, filename)))

        plt.show()


def run(steps, range_frac=0.035, num_exp=2, exptime=2, filt='L', binning=1,
        measure_corners=False, go_to_best=False, zenith=False,
        no_slew=False, no_analysis=False, no_plot=False, no_confirm=False):
    """Run the focus run routine."""
    # Get the positions for the run
    print('~~~~~~')
    print('Calculating positions...')
    scale_factors = {ut: params.AUTOFOCUS_PARAMS[ut]['FOCRUN_SCALE']
                     for ut in params.AUTOFOCUS_PARAMS}
    positions = calculate_positions(range_frac, steps, scale_factors)

    total_time = estimate_time(steps, num_exp, exptime, binning, measure_corners)
    print('ESTIMATED TIME TO COMPLETE RUN: {:.1f} min'.format(total_time / 60))

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
        print('~~~~~~')
        if zenith:
            print('Slewing to zenith...')
            target_name = 'Focus run'
            slew_to_altaz(89.9, 0, timeout=120)
        else:
            star = focus_star(Time.now())
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
        if not no_analysis:
            print('Taking {} measurements at new focus position...'.format(num_exp))
            foc_data = measure_focus(num_exp, exptime, filt, binning, target_name, regions=regions)
            if not isinstance(foc_data, pd.DataFrame):
                # Concat region list
                foc_data = pd.concat(foc_data)
            all_data.append(foc_data)
        else:
            for i in range(num_exp):
                print('Taking exposure {}/{}...'.format(i + 1, num_exp))
                # This will take and save the images, we don't care about the data here
                image_headers = get_analysis_image(
                    exptime, filt, binning, target_name, 'FOCUS',
                    glance=False, uts=params.UTS_WITH_FOCUSERS, get_headers=True)
                print('Exposure {} complete'.format(image_headers[1]['RUN-ID']))

    print('~~~~~~')
    print('Exposures finished')
    finish_time = Time.now().isot

    # Restore the original focus
    print('~~~~~~')
    print('Restoring original focuser positions...')
    set_focuser_positions(initial_positions, timeout=120)
    print('Restored focus: ', get_focuser_positions())

    if no_analysis:
        print('~~~~~~')
        print('Skipping analysis...')
        # Nothing else to do
        print('Done')
        sys.exit()

    # Write out data
    print('~~~~~~')
    print('Writing out data to file...')
    path = os.path.join(params.FILE_PATH, 'focus_data')
    filename = 'focusdata_{}.csv'.format(finish_time)
    df = pd.concat(all_data)
    df.to_csv(os.path.join(path, filename))
    print('Saved to {}'.format(os.path.join(path, filename)))

    # Fit to data
    print('~~~~~~')
    print('Fitting to data...')
    nfvs = {ut: params.AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE']
            if ut in params.AUTOFOCUS_PARAMS else DEFAULT_NFV
            for ut in sorted(set(df.index))
            }
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

    filename = 'focusfit_{}.csv'.format(finish_time)
    fit_df.to_csv(os.path.join(path, filename))
    print('Saved to {}'.format(os.path.join(path, filename)))

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
    # Mandatory arguments
    parser.add_argument('steps',
                        type=int, default=5,
                        help=('how many exposures to take either side of the current position '
                              '(eg steps=5 gives 11 in total: 5 + 1 in the centre + 5)'),
                        )
    # Optional arguments
    parser.add_argument('-r', '--range',
                        type=restricted_float, default=0.035,
                        help=('fraction of the focuser range to run over'
                              ' (range 0-1, default=%(default)f)'),
                        )
    parser.add_argument('-n', '--numexp',
                        type=int, default=2,
                        help=('number of exposures to take at each position'
                              ' (default=%(default)d)'),
                        )
    parser.add_argument('-t', '--exptime',
                        type=float, default=5,
                        help=('exposure time, in seconds'
                              ' (default=%(default)d)')
                        )
    parser.add_argument('-f', '--filter',
                        type=str, choices=params.FILTER_LIST, default='L',
                        help=('filter to use'
                              ' (default=%(default)d)')
                        )
    parser.add_argument('-b', '--binning',
                        type=int, default=1,
                        help=('image binning factor'
                              ' (default=%(default)d)')
                        )
    # Flags
    parser.add_argument('--corners',
                        action='store_true',
                        help=('measure focus position in the corners as well as the centre')
                        )
    parser.add_argument('--go-to-best',
                        action='store_true',
                        help=('when the run is complete move to the best focus position')
                        )
    parser.add_argument('--zenith',
                        action='store_true',
                        help=('slew to zenith instead of a focus star')
                        )
    parser.add_argument('--no-slew',
                        action='store_true',
                        help=('do not slew (stay at current position)')
                        )
    parser.add_argument('--no-analysis',
                        action='store_true',
                        help=('do not analyse the image HFDs, just take them and quit')
                        )
    parser.add_argument('--no-plot',
                        action='store_true',
                        help=('do not display plot of results')
                        )
    parser.add_argument('--no-confirm',
                        action='store_true',
                        help=('skip confirmation (needed if running automatically)')
                        )

    args = parser.parse_args()
    steps = args.steps
    range_frac = args.range
    num_exp = args.numexp
    exptime = args.exptime
    filt = args.filter
    binning = args.binning
    measure_corners = args.corners
    go_to_best = args.go_to_best
    zenith = args.zenith
    no_slew = args.no_slew
    no_analysis = args.no_analysis
    no_plot = args.no_plot
    no_confirm = args.no_confirm

    # If something goes wrong we need to restore the original focus
    initial_positions = get_focuser_positions()
    try:
        RestoreFocusCloser(initial_positions)
        run(steps, range_frac, num_exp, exptime, filt, binning,
            measure_corners, go_to_best, zenith, no_slew, no_analysis, no_plot, no_confirm)
    except Exception:
        print('Error caught: Restoring original focus positions...')
        set_focuser_positions(initial_positions)
        raise
