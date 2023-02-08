#!/usr/bin/env python3
"""Script to run during the night while the dome is closed."""

import time
from argparse import ArgumentParser

from gtecs.control import params
from gtecs.control.daemons import daemon_proxy
from gtecs.control.observing import (mirror_covers_are_closed, mount_is_parked, prepare_for_images,
                                     slew_to_altaz, wait_for_exposure_queue)


def run(nexp=3):
    """Tasks to occupy the telescope while the dome is closed.

    Parameters
    ----------
    nexp : int
        number of each type of bias and dark frame to take

    """
    print('Running bad conditions tasks')

    # make sure hardware is ready
    prepare_for_images(open_covers=True)
    time.sleep(2)

    # close the covers again for darks (we open to stop them sticking)
    print('Closing mirror covers')
    with daemon_proxy('ota') as daemon:
        daemon.close_covers()
    start_time = time.time()
    while not mirror_covers_are_closed():
        time.sleep(0.5)
        if (time.time() - start_time) > 60:
            raise TimeoutError('Mirror covers timed out')

    # move the mount around
    if mount_is_parked():
        print('Unparking mount')
        with daemon_proxy('mnt') as daemon:
            daemon.unpark()
        start_time = time.time()
        while mount_is_parked():
            time.sleep(1)
            if (time.time() - start_time) > 60:
                raise TimeoutError('Mount unparking timed out')
    for az in [0, 90, 180, 270, 0]:
        slew_to_altaz(50, az, timeout=120)
        time.sleep(2)
    print('Mount tests complete')

    # park again
    print('Parking mount')
    with daemon_proxy('mnt') as daemon:
        daemon.park()
    start_time = time.time()
    while not mount_is_parked():
        time.sleep(1)
        if (time.time() - start_time) > 60:
            raise TimeoutError('Mount parking timed out')

    # take extra biases and darks
    uts = params.UTS_WITH_CAMERAS
    with daemon_proxy('exq') as daemon:
        print(f'Taking {nexp} bias exposures')
        daemon.add(uts, exptime=0.0, nexp=nexp, frametype='dark', imgtype='BIAS')
        # TODO: this should be a param list (or args), match takeBiasesAndDarks
        for exptime in [60, 90, 120, 600]:
            print(f'Taking {nexp} {exptime:.0f}s dark exposures')
            daemon.add(uts, exptime=exptime, nexp=nexp, frametype='dark', imgtype='DARK')
        daemon.resume()

    # estimate a deliberately pessimistic timeout
    readout = 10
    total_time = (1 + readout +
                  60 + readout +
                  90 + readout +
                  120 + readout +
                  600 + readout
                  ) * nexp
    total_time *= 1.5
    wait_for_exposure_queue(total_time)
    print('Biases and darks complete')

    print('Bad conditions tasks done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Tasks to run during the night while the dome is closed.')
    # Optional arguments
    parser.add_argument('nexp',
                        type=int, nargs='?', default=3,
                        help=('number of bias and dark sets to take'
                              ' (default=%(default)d)'),
                        )

    args = parser.parse_args()

    run(args.nexp)
