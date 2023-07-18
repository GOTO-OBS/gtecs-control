"""Focusing utilities."""

import time

from gtecs.common.system import NeatCloser

import numpy as np

import pandas as pd

from . import params
from .analysis import get_focus_region, measure_image_hfd
from .daemons import daemon_proxy
from .observing import get_analysis_image, get_image_headers


class RestoreFocusCloser(NeatCloser):
    """Restore the original focus positions if anything goes wrong."""

    def __init__(self, positions):
        super().__init__('Script')
        self.positions = positions

    def tidy_up(self):
        """Restore the original focus."""
        print('Interrupt caught: Restoring original focus positions...')
        set_focuser_positions(self.positions)


def get_focus_params():
    """Create a dataframe with all the autofocus parameters from params."""
    all_uts = sorted(params.AUTOFOCUS_PARAMS.keys())
    foc_params = {'big_step': {ut: params.AUTOFOCUS_PARAMS[ut]['BIG_STEP'] for ut in all_uts},
                  'small_step': {ut: params.AUTOFOCUS_PARAMS[ut]['SMALL_STEP'] for ut in all_uts},
                  'nfv': {ut: params.AUTOFOCUS_PARAMS[ut]['NEAR_FOCUS_VALUE'] for ut in all_uts},
                  'm_l': {ut: params.AUTOFOCUS_PARAMS[ut]['SLOPE_LEFT'] for ut in all_uts},
                  'm_r': {ut: params.AUTOFOCUS_PARAMS[ut]['SLOPE_RIGHT'] for ut in all_uts},
                  'delta_x': {ut: params.AUTOFOCUS_PARAMS[ut]['DELTA_X'] for ut in all_uts},
                  }
    foc_params = pd.DataFrame(foc_params)
    return foc_params


def get_hfd_position(target_hfd, current_position, current_hfd, gradient):
    """Estimate the focuser position producing the target HFD.

    Uses the gradient of the right-hand wing of the V-curve, given a known position on that line
    it's just a simple linear fit.

    """
    return current_position + (target_hfd - current_hfd) / gradient


def get_best_focus_position(x_r, y_r, m_l, m_r, delta_x):
    """Find the best focus position by fitting to the V-curve.

    This method is based on:
        Fast auto-focus method and software for ccd-based telescopes
        L Weber and S Brady (2001) Minor Planet Amateur/Professional Workshop
        https://www.ccdware.com/Files/ITS%20Paper.pdf

    We have two straight lines (l and r) which follow y=mx+c, where m is the gradient and c is the
    y-intercept. We want to find the meeting point between the two lines, specifically the
    x-position (x_b) as that corresponds to the best focus position.

    The point when the two lines meet (x_b, y_b) satisfies both equations, i.e.
        (1) y_b = m_l * x_b + c_l = m_r * x_b + c_r
    which when rearranged gives
        (2) x_b = (c_l - c_r) / (m_r - m_l)

    That's great, we have the gradients m_l and m_r as they remain constant. However the V-curve
    will move on different nights which means the intercepts c_l and c_r will change.

    Since we have found a point (x_r, y_r) on the right-hand side of the V-curve it must
    satisfy the standard equation y_r = m_r * x_r + c_r. Therefore we rearrange to find
        (3) c_r = y_r - m_r * x_r

    We could try and find another point on the left side of the curve, however we don't need to.
    We know the V-curve only moves along the x-axis, meaning the difference between the x-intercepts
    (delta_x) will remain constant. The x-intercept (k) is given when y=0, so 0=mk+c or k=c/m. So
        (4) delta_x = k_r - k_l = (c_r / m_r) - (c_l / m_l)
    which can be rearranged to
        (5) c_l = m_l * (c_r / m_r - delta_x)

    Finally we substitute (3) and (5) into (2) and that gives us x_b.

    """
    c_r = y_r - m_r * x_r
    c_l = m_l * (c_r / m_r - delta_x)
    x_b = ((c_l - c_r) / (m_r - m_l))
    return x_b


def get_best_focus_position_2(x_l, y_l, x_r, y_r, m_r):
    """Find the best focus position by fitting to the V-curve.

    This method is based on:
        Donut: Measuring Optical Aberrations from a Single Extrafocal Image
        A. Tokovinin and S. Heathcote (2006) PASP 118 1165
        https://iopscience.iop.org/article/10.1086/506972

    As in `get_best_focus_position()`, we have two lines l and r that meet at (x_b, y_b).
        (1) y_b = m_l * x_b + c_l = m_r * x_b + c_r
    which when rearranged gives
        (2) x_b = (c_l - c_r) / (m_r - m_l)

    This time we have two points on either side, (x_l, y_l) and (x_r, y_r). With the standard
    y=mx+c form we can rearrange to find the two constants
        (3) c_l = y_l - m_l * x_l
        (4) c_r = y_r - m_r * x_r
    Then we can sub these both into (2) to get
        (5) x_b = (y_l - m_l * x_l - y_r + m_r * x_r) / (m_r - m_l)

    Now if we know the two gradients we're done, but in this case we assume that the V-curve is
    symmetric, i.e. m_l = -m_r. Using that (5) simplifies to
        (6) x_b = (y_l + m_r * x_l - y_r + m_r * x_r) / (2 * m_r)
                = (y_l - y_r) / (2 * m_r) + (x_l + x_r) / 2
    """
    x_b = (y_l - y_r) / (2 * m_r) + (x_l + x_r) / 2
    return x_b


def get_focuser_positions(uts=None):
    """Find the current focuser positions."""
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    if uts is None:
        uts = info['uts']
    positions = {ut: info[ut]['current_pos'] for ut in uts}
    return positions


def set_focuser_positions(positions, wait=False, timeout=None):
    """Move each focuser to the requested position.

    Parameters
    ----------
    positions : float, dict
        position to move to, or a dictionary of unit telescope IDs and positions

    wait: bool, default=False
        wait for the focusers to complete their move
    timeout : float, default=None
        time in seconds after which to timeout, None to wait forever
        if `wait` is False and a non-None timeout is given, still wait for that time

    """
    if not isinstance(positions, dict):
        positions = {ut: positions for ut in params.UTS_WITH_FOCUSERS}

    with daemon_proxy('foc') as daemon:
        # We can't overwrite moves once they have started, so need to wait for them to be ready
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            ready = [info[ut]['status'] == 'Ready' for ut in positions]
            if all(ready):
                break

        print('Setting focusers:', positions)
        daemon.set_focusers(positions)

        if wait or timeout is not None:
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            if timeout is None:
                timeout = 30
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                # Note we say we're there when we're within 5 steps,
                # because the ASA auto-adjustment means we can't be exact.
                done = [abs(info[ut]['current_pos'] - int(positions[ut])) < 5 and
                        info[ut]['status'] == 'Ready'
                        for ut in positions]
                if done:
                    break
                if (time.time() - start_time) > timeout:
                    raise TimeoutError('Focuser timed out')


def move_focusers(offsets, wait=False, timeout=None):
    """Move each focuser by the given number of steps.

    Parameters
    ----------
    offsets : float, dict
        offsets in steps to move by, or a dictionary of unit telescope IDs and offsets

    wait: bool, default=False
        wait for the focusers to complete their move
    timeout : float, default=None
        time in seconds after which to timeout, None to wait forever
        if `wait` is False and a non-None timeout is given, still wait for that time

    """
    if not isinstance(offsets, dict):
        offsets = {ut: offsets for ut in params.UTS_WITH_FOCUSERS}

    # We can't overwrite moves once they have started, so need to wait for them to be ready
    with daemon_proxy('foc') as daemon:
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            ready = [info[ut]['status'] == 'Ready' for ut in offsets]
            if all(ready):
                break

        info = daemon.get_info(force_update=True)
        start_positions = {ut: info[ut]['current_pos'] for ut in info['uts']}
        finish_positions = {ut: start_positions[ut] + offsets[ut] for ut in offsets}

        print('Moving focusers:', offsets)
        daemon.move_focusers(offsets)

        if wait or timeout is not None:
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            if timeout is None:
                timeout = 30
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                # Note we say we're there when we're within 5 steps,
                # because the ASA auto-adjustment means we can't be exact.
                done = [abs(info[ut]['current_pos'] - int(finish_positions[ut])) < 5 and
                        info[ut]['status'] == 'Ready'
                        for ut in finish_positions]
                if done:
                    break
                if (time.time() - start_time) > timeout:
                    raise TimeoutError('Focuser timed out')


def wait_for_focusers(target_positions, timeout=None):
    """Wait until focuser has reached the target position.

    Parameters
    ----------
    target_positions : float, dict
        target position, or a dictionary of unit telescope IDs and positions

    timeout : float, default=None
        time in seconds after which to timeout, None to wait forever

    """
    if not isinstance(target_positions, dict):
        target_positions = {ut: target_positions for ut in params.UTS_WITH_FOCUSERS}

    start_time = time.time()
    reached_position = False
    timed_out = False
    while not reached_position and not timed_out:
        time.sleep(0.2)

        try:
            with daemon_proxy('foc') as daemon:
                info = daemon.get_info(force_update=True)

            # Note we say we're there when we're within 5 steps,
            # because the ASA auto-adjustment means we can't be exact.
            done = [abs(info[ut]['current_pos'] - int(target_positions[ut])) < 5 and
                    info[ut]['status'] == 'Ready'
                    for ut in target_positions]
            if all(done):
                reached_position = True
        except Exception:
            pass

        if timeout and time.time() - start_time > timeout:
            timed_out = True

    if timed_out:
        raise TimeoutError('Focuser timed out')


def measure_focus(num_exp=1, exptime=5, filt='L', binning=1, target_name='Focus test image',
                  uts=None, regions=None):
    """Take a set of images and measure the median half-flux diameters.

    Parameters
    ----------
    num_exp : int, default=1
        Number of exposures to take.
        If > 1 the smallest of the measured HFD values will be returned for each UT.
    exptime : float, default=5
        Image exposure time.
    filt : str, default='L'
       Filter to use for the exposures.
    binning : int, default=1
       Binning factor to use for the exposures.
    target_name : str, default='Focus test image'
        Name of the target being observed.
    uts : list of int, default=params.UTS_WITH_FOCUSERS (all UTs with focusers)
        UTs to measure focus for.
    regions : list of 2-tuple of slice, or list of list of same, or None, default=None
        If given, the image data will be cropped to the given region(s) before measuring.
        Note the region limits should be in BINNED pixels.

    Returns
    -------
    foc_data : `pandas.DataFrame` or list of `pandas.DataFrame`s
        A Pandas dataframe with an index of unit telescope ID.
        If multiple regions are given then the list will be len(regions)

    """
    if uts is None:
        uts = params.UTS_WITH_FOCUSERS
    else:
        uts = [ut for ut in uts if ut in params.UTS_WITH_FOCUSERS]
    if regions is None:
        regions = [None]

    # Get the current focuser positions and the temperature the last time they moved
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    current_positions = {ut: info[ut]['current_pos'] for ut in info['uts']}
    last_temps = {ut: info[ut]['last_move_temp'] for ut in info['uts']}

    all_uts = sorted(current_positions.keys())

    all_data = [{ut: [] for ut in all_uts} for _ in range(len(regions))]
    for i in range(num_exp):
        print('Taking exposure {}/{}...'.format(i + 1, num_exp))
        # Take a set of images
        image_data = get_analysis_image(exptime, filt, binning, target_name, 'FOCUS',
                                        glance=False, uts=uts)

        # Measure the median HFDs in each image
        for ut in all_uts:
            for j, region in enumerate(regions):
                if ut in image_data:
                    try:
                        # Extract median HFD and std values from the image data
                        # Note filter_width is 15, this deals much better with out-of-focus images
                        hfd, hfd_std = measure_image_hfd(image_data[ut],
                                                         region=region,
                                                         filter_width=15 // binning,
                                                         verbose=False)

                        # HFDs are in binned pixels, convert to unbinned
                        hfd *= binning
                        hfd_std *= binning

                        # Check for invalid values
                        if hfd_std <= 0.0:
                            raise ValueError

                    except Exception as err:
                        print('HFD measurement for UT{}{} errored: {}'.format(ut,
                              ' region {}'.format(j) if len(regions) > 1 else '', str(err)))
                        hfd = np.nan
                        hfd_std = np.nan
                else:
                    # We're ignoring this UT, but still add NaNs
                    hfd = np.nan
                    hfd_std = np.nan

                # Add to main arrays
                data_dict = {'UT': ut,
                             'pos': current_positions[ut],
                             # 'exposure': i,
                             'region': j,
                             'hfd': hfd,
                             'hfd_std': hfd_std,
                             'temp': last_temps[ut],
                             }
                all_data[j][ut].append(data_dict)

        # Delete the image data for good measure, to save memory
        del image_data

        if len(regions) == 1:
            print('HFDs:', {ut: np.round(all_data[0][ut][i]['hfd'], 1) for ut in uts})
        else:
            msg = 'HFDs:\n'
            for j, data in enumerate(all_data):
                msg += 'region {}: {}\n'.format(j, {ut: np.round(data[ut][i]['hfd'], 1)
                                                    for ut in uts})
            print(msg[:-1])

    all_dfs = []
    msg = 'Best HFDs:{}'.format('\n' if len(all_data) > 1 else ' ')
    for j, region_data in enumerate(all_data):
        # Make into dataframes
        region_dfs = {ut: pd.DataFrame(region_data[ut]) for ut in region_data}

        # Take the smallest of the HFD values measured as the best estimate for this position.
        # The reasoning is that we already average the HFD over many stars in each frame,
        # so across multiple frames we only sample external fluctuations, usually windshake,
        # which will always make the HFD worse, never better.
        # We also want to make sure we get the std associated with that image.
        best_dfs = [region_dfs[ut][region_dfs[ut]['hfd'] == region_dfs[ut]['hfd'].min()]
                    if not np.isnan(region_dfs[ut]['hfd'].min())  # will return NaN if are all NaNs
                    else region_dfs[ut].iloc[[0]]                 # just take the first row
                    for ut in region_dfs]

        # Make into a single dataframe
        df = pd.concat(best_dfs)
        df.set_index('UT', inplace=True)
        all_dfs.append(df)

        # Print best HFDs if more than one exp was taken
        if len(all_data) > 1:
            msg += 'region {}: '.format(j)
        msg += '{}\n'.format(df['hfd'].round(1).to_dict())

    if num_exp > 1:
        print(msg[:-1])

    if len(all_dfs) == 1:
        # backwards compatibility if there's only one region
        df = all_dfs[0]
        df.drop('region', axis=1, inplace=True)
        return df
    return all_dfs


def focus_temp_compensation(take_images=False, verbose=False):
    """Apply any needed temperature compensation to the focusers."""
    # Find the change in temperature since the last move
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    curr_temp = {ut: info[ut]['current_temp'] for ut in info['uts']}
    prev_temp = {ut: info[ut]['last_move_temp'] for ut in info['uts']}
    deltas = {ut: np.round(curr_temp[ut] - prev_temp[ut], 1)
              if (curr_temp[ut] is not None and prev_temp[ut] is not None) else 0
              for ut in params.AUTOFOCUS_PARAMS}
    if verbose:
        print('Checking focuser temperatures...')
        print('Current temp:', curr_temp)
        print('Previous temp:', prev_temp)
        print('Difference:', deltas)

    # Check if the change is greater than the minimum to refocus
    min_change = {ut: params.AUTOFOCUS_PARAMS[ut]['TEMP_MINCHANGE']
                  for ut in params.AUTOFOCUS_PARAMS}
    deltas = {ut: deltas[ut]
              if abs(deltas[ut]) > min_change[ut] else 0
              for ut in deltas}

    # Find the gradients (in steps/degree C)
    gradients = {ut: params.AUTOFOCUS_PARAMS[ut]['TEMP_GRADIENT']
                 for ut in params.AUTOFOCUS_PARAMS}

    # Calculate the focus offset
    offsets = {ut: int(deltas[ut] * gradients[ut]) for ut in params.AUTOFOCUS_PARAMS}
    if verbose:
        print('Offsets:', offsets)

    # Ignore any UTs which do not need changing
    offsets = {ut: offsets[ut] for ut in offsets if offsets[ut] != 0}

    if len(offsets) > 0:
        print('Applying temperature compensation to focusers')

        if not take_images:
            # Just move
            move_focusers(offsets, timeout=60)
        else:
            before_data = measure_focus(exptime=5)
            if verbose:
                print('Before data:\n', before_data.round(1))

            move_focusers(offsets, timeout=60)

            after_data = measure_focus(exptime=5)
            if verbose:
                print('After data:\n', after_data.round(1))

            diff = {ut: np.round(after_data['hfd'][ut] - before_data['hfd'][ut], 1)
                    for ut in after_data.index}
            print('Change in HFDs:', diff)


def refocus(uts=None, use_annulus_region=True, take_test_images=False, reset=False):
    """Quickly test and adjust the focus position if necessary."""
    if uts is None:
        uts = params.UTS_WITH_FOCUSERS
    uts = [ut for ut in uts if ut in params.UTS_WITH_FOCUSERS]

    # Default parameters  # TODO: these should be function args? At least the offset
    focus_offset = 200
    exptime = 5
    filt = 'L'
    binning = 2

    # Get the focus parameters defined in params
    foc_params = get_focus_params()

    # Define measurement region
    if use_annulus_region:
        # Measure sources in an annulus around the centre
        region = get_focus_region(binning)
    else:
        # Stick to the default central region
        region = (slice(2500 // binning, 6000 // binning),
                  slice(1500 // binning, 4500 // binning))
    regions = [region]  # measure_focus takes a list of regions

    # Store the initial positions and define the new ones on either side
    with daemon_proxy('foc') as daemon:
        info = daemon.get_info(force_update=True)
    initial_positions = {ut: info[ut]['current_pos'] for ut in uts}
    r_positions = {ut: initial_positions[ut] + focus_offset for ut in uts}
    l_positions = {ut: initial_positions[ut] - focus_offset for ut in uts}
    if take_test_images:
        print('Taking test image at initial position...')
        measure_focus(1, exptime, filt, binning, 'Refocus', uts, regions)

    #####################################################################################
    # # Move the focusers out to the right (+ve) side of the V-curve and measure HFDs
    # print('Measuring focus on the right...')
    # set_focuser_positions(r_positions, timeout=60)
    # r_data = measure_focus(num_exp, exptime, filt, binning, 'Refocus', uts, regions)

    # # Move the focusers out to the left (-ve) side of the V-curve and measure HFDs
    # print('Measuring focus on the left...')
    # set_focuser_positions(l_positions, timeout=60)
    # l_data = measure_focus(num_exp, exptime, filt, binning, 'Refocus', uts, regions)

    # # Calculate the new best focus positions
    # print('Calculating best focus positions...')
    # bf_positions = get_best_focus_position_2(l_data['pos'], l_data['hfd'],
    #                                          r_data['pos'], r_data['hfd'],
    #                                          foc_params['m_r'],
    #                                          )
    # bf_positions_dict = {ut: int(bf_positions.to_dict()[ut]) for ut in uts}

    # # We need to handle any UTs that failed to measure HFDs (e.g. clouds)
    # for ut in uts:
    #     if bf_positions_dict[ut] in [np.nan, None]:
    #         print('Warning: UT{} position is NaN, reverting to initial position')
    #         bf_positions_dict[ut] = initial_positions[ut]

    # print('Best focus positions:', bf_positions_dict)

    # # Move to the best focus position
    # print('Moving to best focus position...')
    # set_focuser_positions(bf_positions_dict, timeout=60)
    # if take_test_images:
    #     print('Taking test image at best focus position...')
    #     measure_focus(num_exp, exptime, filt, binning, 'Refocus', uts, regions)

    #####################################################################################
    # Step 0: Turn on HFD measurement
    with daemon_proxy('cam') as daemon:
        daemon.measure_image_hfds('on')

    # Step 1: Start to move out to the right (+ve steps), return immediately
    print('Moving to the right...')
    set_focuser_positions(r_positions, wait=False)

    # Step 2: Wait until the focusers have finished moving, return when stationary
    wait_for_focusers(r_positions, timeout=60)

    # Step 3: Start the right exposure, return when finished (but still saving)
    print('Taking right exposure...')
    _, r_i = get_analysis_image(exptime, filt, binning, 'Refocus', 'FOCUS', False, uts,
                                get_data=False, get_headers=False)

    # Step 4: Start to move out to the left (return immediately)
    print('Moving to the left...')
    set_focuser_positions(l_positions, wait=False)

    # Step 5: Read the right HFDs from the image headers
    r_headers = get_image_headers(r_i, timeout=30)
    r_hfds = {ut: r_headers[ut]['MEDHFD0'] for ut in r_headers}
    print('right hfds:', r_hfds)

    # Step 6: Wait until the focusers have finished moving, return when stationary
    wait_for_focusers(l_positions, timeout=60)

    # Step 7: Start the left exposure, return when finished (but still saving)
    print('Taking left exposure...')
    _, l_i = get_analysis_image(exptime, filt, binning, 'Refocus', 'FOCUS', False, uts,
                                get_data=False, get_headers=False)

    # Step 8: Start to move back to the centre
    print('Moving to the centre...')
    set_focuser_positions(initial_positions, wait=False)

    # Step 9: Read the left HFDs from the image headers
    l_headers = get_image_headers(l_i, timeout=30)
    l_hfds = {ut: l_headers[ut]['MEDHFD0'] for ut in l_headers}
    print('left hfds:', l_hfds)

    # Step 10: Calculate the new best position
    bf_positions = get_best_focus_position_2(
        np.array([l_positions[ut] for ut in l_positions]),
        np.array([l_hfds[ut] for ut in l_hfds]),
        np.array([r_positions[ut] for ut in r_positions]),
        np.array([r_hfds[ut] for ut in r_hfds]),
        np.array([foc_params['m_r'].to_dict()[ut] for ut in foc_params['m_r'].to_dict()]),
    )
    bf_positions = {ut: int(bf_positions[ut - 1]) for ut in uts}
    print('best positions:', bf_positions)

    # Step 11: Wait until the focusers have finished moving
    wait_for_focusers(initial_positions, timeout=60)

    # Step 12: Move to the best position
    print('Moving to best positions...')
    set_focuser_positions(bf_positions, wait=False)

    # Step 13: Wait until the focusers have finished moving (or return immediately?)
    wait_for_focusers(bf_positions, timeout=60)

    # Step 14: Turn off HFD measurement
    with daemon_proxy('cam') as daemon:
        daemon.measure_image_hfds('off')

    if reset:
        # Move back to the original position
        print('Moving back to initial position...')
        set_focuser_positions(initial_positions, timeout=60)
        if take_test_images:
            print('Taking test image at initial position...')
            measure_focus(1, exptime, filt, binning, 'Refocus', uts, regions)

    print('Refocusing complete')
