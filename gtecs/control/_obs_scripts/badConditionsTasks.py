#!/usr/bin/env python3
"""Script to run during the night while the dome is closed."""

import time
from argparse import ArgumentParser

from gtecs.control.daemons import daemon_proxy
from gtecs.control.observing import prepare_for_images, slew_to_altaz


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
        # TODO: blocking command with confirmation or timeout in daemon
        start_time = time.time()
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            if all([info[ut]['position'] == 'closed' for ut in info['uts_with_covers']]):
                break
            if (time.time() - start_time) > 60:
                raise TimeoutError('Mirror covers timed out')

    # move the mount around
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
        if info['status'] == 'Parked':
            print('Unparking mount')
            daemon.unpark()
            # TODO: blocking command with confirmation or timeout in daemon
            start_time = time.time()
            while True:
                time.sleep(0.5)
                info = daemon.get_info(force_update=True)
                if info['status'] != 'Parked':
                    break
                if (time.time() - start_time) > 60:
                    raise TimeoutError('Mount unparking timed out')
    print('Moving mount')
    for az in [0, 90, 180, 270, 0]:
        slew_to_altaz(50, az, timeout=120)
        time.sleep(2)
    print('Mount tests complete')

    # park again
    with daemon_proxy('mnt') as daemon:
        info = daemon.get_info(force_update=True)
        print('Parking mount')
        daemon.park()
        # TODO: blocking command with confirmation or timeout in daemon
        start_time = time.time()
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            if info['status'] in ['Parked', 'IN BLINKY MODE', 'MOTORS OFF']:
                break
            if (time.time() - start_time) > 60:
                raise TimeoutError('Mount parking timed out')

    # take extra biases and darks
    with daemon_proxy('exq') as daemon:
        print(f'Taking {nexp} bias exposures')
        daemon.add(exptime=0.0, nexp=nexp, frametype='dark', imgtype='BIAS')

        # TODO: this should be a param list (or args), match takeBiasesAndDarks
        for exptime in [60, 90, 120, 600]:
            print(f'Taking {nexp} {exptime:.0f}s dark exposures')
            daemon.add(exptime=exptime, nexp=nexp, frametype='dark', imgtype='DARK')

        # estimate a deliberately pessimistic timeout
        readout = 10
        total_time = (1 + readout +
                      60 + readout +
                      90 + readout +
                      120 + readout +
                      600 + readout
                      ) * nexp
        total_time *= 1.5

        # Resume the queue
        daemon.resume()
        # TODO: blocking command with confirmation or timeout in daemon
        start_time = time.time()
        while True:
            time.sleep(0.5)
            info = daemon.get_info(force_update=True)
            if (info['queue_length'] == 0 and
                    info['exposing'] is False and
                    info['status'] == 'Ready'):
                break
            if (time.time() - start_time) > total_time:
                raise TimeoutError('Exposure queue timed out')

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
