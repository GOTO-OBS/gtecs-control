#!/usr/bin/env python3
"""Script to take extra dark frames to test for hot pixels."""

from argparse import ArgumentParser

from gtecs.common.system import execute_command
from gtecs.control.observing import prepare_for_images, wait_for_exposure_queue


def run():
    """Take extra dark frames."""
    print('Taking extra test dark frames.')

    # make sure hardware is ready
    prepare_for_images(open_covers=False)

    execute_command('exq multdark 2 600 1')
    execute_command('exq resume')  # just in case

    # estimate a deliberately pessimistic timeout
    readout = 10
    total_time = (600 + readout +
                  600 + readout)
    total_time *= 1.5
    wait_for_exposure_queue(total_time)

    print('Extra darks done')


if __name__ == '__main__':
    parser = ArgumentParser(description='Take extra dark frames.')
    run()
