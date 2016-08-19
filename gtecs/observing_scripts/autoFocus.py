"""
Script to autofocus the telescopes.

Image quality is measured via the half-flux-diameter (HFD).

Half flux diameter vs focus position should be linear relationship,
with opposite slopes either side of the best focus. This function should
be fairly stable, so once you know which side of best focus you are
on, and the current HFD, you can in principle move straight to focus.

The routine searches for a target HFD known as the near focus value,
and hops to the best focus from there.
"""
from __future__ import absolute_import
from __future__ import print_function
import argparse
import time
import sys
import pydoc

import numpy as np

from astropy import units as u
from astropy.time import Time
from astropy.io import fits
from astropy.stats.sigma_clipping import sigma_clipped_stats

from gtecs.tecs_modules.misc import execute_command as cmd, neatCloser
from gtecs.catalogs import flats
from gtecs.tecs_modules.observing import (wait_for_exposure_queue,
                                          last_written_image, goto,
                                          get_current_focus, set_new_focus,
                                          wait_for_focuser,
                                          wait_for_telescope)
from gtecs.tecs_modules import params
import gtecs.tecs_modules.astronomy as ast
from gtecs.tecs_modules.time_date import nightStarting


def take_frame(expT, current_filter, name):
    cmd('exq image {} {} 1 {} FOCUS'.format(
        expT, current_filter, name
    ))
    time.sleep(0.1)
    wait_for_exposure_queue()
    time.sleep(0.1)
    fnames = last_written_image()
    return fnames


class RestoreFocus(neatCloser):
    def __init__(self, focusVals):
        super(RestoreFocus, self).__init__('autofocus')
        self.focusVals = focusVals

    def tidyUp(self):
        print('Restoring original focus')
        set_new_focus(self.focusVals)


def set_focus_carefully(new_focus_values, orig_focus):
    """
    Move to focus, but restore old values if we fail
    """
    set_new_focus(new_focus_values)
    try:
        wait_for_focuser(10)
    except:
        set_new_focus(orig_focus)
        raise


def measure_focus_carefully(expT, filt, name, orig_focus):
    try:
        fnames = take_frame(expT, filt, name)
        return get_hfd(fnames)
    except:
        set_new_focus(orig_focus)
        raise


def find_best_focus(m1, m2, delta, xval, yval):
    """
    Given two lines with gradients m1, m2 whose intercepts differ by delta.

    Now find the point where the lines cross, given a location xval, yval on
    the line with gradient m2.
    """
    c2 = yval-m2*xval
    c1 = m1*(-delta + c2/m2)
    meeting_point = ((c1-c2)/(m2-m1))
    return meeting_point


def estimate_focus(targetHFD, currentHFD, currentPos, slope):
    return currentPos + (targetHFD-currentHFD)/slope


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter
        )
    parser.add_argument('nearfocusvalue', type=float)
    parser.add_argument('filter')
    args = parser.parse_args()
    nfv = args.nearfocusvalue
    filt = args.filter
    if filt not in params.FILTER_LIST:
        raise ValueError('filter not one of {!r}'.format(params.FILTER_LIST))
    if np.fabs(nfv - 20) > 5:
        raise ValueError('near near focus value should be between 15 and 25')

    bigstep = 20
    smallstep = 5
    expT = 2

    print('Starting focus routine')
 #star = gliese.focus_star(Time.now())
    star = flats.best_flat(Time.now())
    print('Slewing to star', star)
    name = star.name

    coordinate = star.coord
    goto(coordinate.ra.deg, coordinate.dec.deg)
    time.sleep(1)
    wait_for_telescope(240)  # 240s timeout

    # get the parameters of the focus curves. Should be arrays, one entry per OTA
    m2 = np.array(params.FOCUS_SLOPE_ABOVE)
    m1 = np.array(params.FOCUS_SLOPE_BELOW)
    delta = np.array(params.FOCUS_INTERCEPT_DIFFERENCE)

    # TODO: remove the fudge below which is there for testing
    def get_hfd(fnames):
        focus_to_aim_for = np.array([1002, 1005, 1001, 1000])
        hfd_at_focus = np.array([2, 2, 2, 2])
        jitter = np.random.normal(size=4, loc=0, scale=0.1)
        return jitter + np.fabs((get_current_focus() - focus_to_aim_for)*m2) + hfd_at_focus

    # start where we are now.
    fnames = take_frame(expT, filt, star.name)
    hfd_values = get_hfd(fnames)
    orig_focus = get_current_focus()
    print('Previous focus: {!r}'.format(orig_focus))
    print('Half-flux-diameters: {!r}'.format(hfd_values))

    # from here any exception or attempt to close should move to old focus
    close_signal_handler = RestoreFocus(orig_focus)

    # move to +ve side of best focus position
    print('Move focus OUT')
    set_focus_carefully(orig_focus + bigstep, orig_focus)

    # hfd should have increased substantially
    # if it hasn't focus measurement is not reliable (dominated by CRs or BPs)?
    old_hfd = hfd_values
    hfd_values = measure_focus_carefully(expT, filt, name, orig_focus)
    print('Focus: {!r}'.format(get_current_focus()))
    print('Half-flux-diameters: {!r}'.format(hfd_values))
    if np.any(hfd_values/old_hfd < 2):
        print(hfd_values, old_hfd)
        set_new_focus(orig_focus)
        raise Exception('image quality estimate not changing with focus position')

    # check we are actually on the +ve side of best focus by moving towards best focus
    set_focus_carefully(get_current_focus() - smallstep, orig_focus)

    # check the IQ has got better
    old_hfd = hfd_values
    hfd_values = measure_focus_carefully(expT, filt, name, orig_focus)
    print('Focus: {!r}'.format(get_current_focus()))
    print('Half-flux-diameters: {!r}'.format(hfd_values))
    if np.any(old_hfd < hfd_values):
        set_new_focus(orig_focus)
        raise Exception('cannot be sure we are on the correct side of best focus')

    # while we are > than twice the near focus value, keep halving the hfd_values
    while np.any(hfd_values > nfv):
        print('stepping towards near focus')
        new_focus_values = estimate_focus(0.5*hfd_values, hfd_values,
                                          get_current_focus(), m2)
        set_focus_carefully(new_focus_values, orig_focus)
        hfd_values = measure_focus_carefully(expT, filt, name, orig_focus)
        print('Focus: {!r}'.format(get_current_focus()))
        print('Half-flux-diameters: {!r}'.format(hfd_values))

    # close enough. Make the step to the focus position that should give HFV
    print('Starting near focus measurements')
    near_focus_pos = estimate_focus(nfv, hfd_values,
                                    get_current_focus(), m2)
    set_focus_carefully(near_focus_pos, orig_focus)
    print('Focus: {!r}'.format(near_focus_pos))
    # measure NFV five times and take average
    hfd_measurements = []
    for i in range(5):
        hfd_values = measure_focus_carefully(expT, filt, name, orig_focus)
        hfd_measurements.append(hfd_values)
        print('Half-flux-diameters: {!r}'.format(hfd_values))
    hfd_measurements = np.asarray(hfd_measurements)
    hfd_values = hfd_measurements.mean(axis=0)
    hfd_stddev = hfd_measurements.std(axis=0)

    # find best focus
    hfd_samples = np.random.normal(size=(1e4, len(hfd_values)),
                                   loc=hfd_values, scale=hfd_stddev)
    best_focus = find_best_focus(m1, m2, delta, near_focus_pos, hfd_samples)
    best_focus_mean = best_focus.mean(axis=0)
    best_focus_std = best_focus.std(axis=0)
    print("Best focus at {!r}".format(best_focus_mean))
    print('With uncertainty {!r}'.format(best_focus_std))

    set_focus_carefully(best_focus_mean, orig_focus)
    best_focus_values = measure_focus_carefully(expT, filt, name, orig_focus)
    print('HFD at best focus = {!r}'.format(best_focus_values))

    print("Done")
