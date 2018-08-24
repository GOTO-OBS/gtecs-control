#!/usr/bin/env python
"""Test code: a simple version of the pilot check_hardware routine to test the monitors."""

import sys
import time

from gtecs import monitors


def check_hardware(hardware):
    """Continuously monitor hardware and try to fix any issues."""
    print('hardware check routine initialised')

    sleep_time = 10
    while True:
        print('~############################~')
        error_count = 0
        print('running hardware checks')
        log_str = 'hardware check results: '
        for monitor in hardware.values():
            num_errs, errors = monitor.check()
            error_count += num_errs
            if num_errs > 0:
                msg = '{} reports {} error{}: '.format(monitor.__class__.__name__,
                                                       num_errs,
                                                       's' if num_errs > 1 else '')
                msg += ', '.join(errors)
                print(log_str + msg)
                monitor.recover()  # Will log recovery commands
                print('~~~~~')

        if error_count > 0:
            sleep_time = 10  # check more frequently till fixed
        else:
            sleep_time = 10  # was 60 in pilot
            print(log_str + 'AOK')

        # save error count so we dont restart whilst broken
        error_count = error_count

        print('~############################~')
        time.sleep(sleep_time)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        mnt_mode = 'parked'
        dome_mode = 'closed'
    elif len(sys.argv) == 2:
        mnt_mode = sys.argv[1]
        if mnt_mode not in ['tracking', 'parked']:
            raise ValueError('Mount mode must be tracking or parked')
        dome_mode = 'closed'
    elif len(sys.argv) == 3:
        mnt_mode = sys.argv[1]
        if mnt_mode not in ['tracking', 'parked']:
            raise ValueError('Mount mode must be tracking or parked')
        dome_mode = sys.argv[2]
        if dome_mode not in ['closed', 'open']:
            raise ValueError('Dome mode must be closed or open')

    # hardware to keep track of and fix if necessary
    hardware = {'dome': monitors.DomeMonitor(),
                'mnt': monitors.MntMonitor(),
                'power': monitors.PowerMonitor(),
                'cam': monitors.CamMonitor(),
                'filt': monitors.FiltMonitor(),
                'foc': monitors.FocMonitor(),
                'exq': monitors.ExqMonitor(),
                'conditions': monitors.ConditionsMonitor(),
                'scheduler': monitors.SchedulerMonitor(),
                }
    hardware['mnt'].mode = mnt_mode
    hardware['dome'].mode = dome_mode

    check_hardware(hardware)
