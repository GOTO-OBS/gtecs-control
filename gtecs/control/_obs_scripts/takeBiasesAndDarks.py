#!/usr/bin/env python3
"""Script to take bias and dark frames."""

from argparse import ArgumentParser

from gtecs.control import params
from gtecs.control.daemons import daemon_proxy
from gtecs.control.observing import prepare_for_images, wait_for_exposure_queue


def run(num_exp=5, extras=False):
    """Take biases and darks at start of night.

    Parameters
    ----------
    num_exp : int
        number of each type of frame to take

    extras : bool, optional
        if True, take extra dark frames
        default is False

    """
    print('Taking bias and dark frames.')

    # make sure hardware is ready
    prepare_for_images(open_covers=False)

    # TODO: Get set of exposure times from the database?
    #       We'd need a camera/exposure database...

    uts = params.UTS_WITH_CAMERAS
    with daemon_proxy('exq') as daemon:
        print(f'Taking {num_exp} bias exposures')
        daemon.add(uts, exptime=0.0, nexp=num_exp, frametype='dark', imgtype='BIAS')
        # TODO: this should be a param list (or args), match badConditionsTasks
        for exptime in [45, 60, 90, 120]:
            print(f'Taking {num_exp} {exptime:.0f}s dark exposures')
            daemon.add(uts, exptime=exptime, nexp=num_exp, frametype='dark', imgtype='DARK')
        if extras:
            print('Taking 2 extra 600s dark exposures')
            daemon.add(uts, exptime=600, nexp=2, frametype='dark', imgtype='DARK')
        daemon.resume()

    # estimate a deliberately pessimistic timeout
    readout = 10
    total_time = (1 + readout +
                  60 + readout +
                  90 + readout +
                  120 + readout) * num_exp
    if extras:
        total_time += (600 + readout) * 2
    total_time *= 1.5
    wait_for_exposure_queue(total_time)

    print('Biases and darks done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take bias and dark frames.')
    # Optional arguments
    parser.add_argument('numexp',
                        type=int,
                        nargs='?', default=5,
                        help=('number of frames to take for each exposure time'
                              ' (default=%(default)d)'),
                        )
    # Flags
    parser.add_argument('-x', '--take-extras',
                        action='store_true',
                        help=('take two extra long dark frames to test for hot pixels'),
                        )

    args = parser.parse_args()
    num_exp = args.numexp
    extras = args.take_extras

    run(num_exp, extras)
