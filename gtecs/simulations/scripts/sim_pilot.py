#!/usr/bin/env python
"""A script simulate a night's run with the pilot."""

import argparse
import warnings

from gtecs.simulations.pilot import run


warnings.simplefilter("ignore", DeprecationWarning)


if __name__ == "__main__":

    usage = 'python sim_pilot.py date'

    parser = argparse.ArgumentParser(description='Run the fake pilot for a night',
                                     usage=usage)
    parser.add_argument('date',
                        nargs='?',
                        default=None,
                        help='night starting date to simulate, default to tonight')
    args = parser.parse_args()

    run(args.date)
