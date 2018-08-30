#!/usr/bin/env python
"""Test code: a simple version of the pilot check_hardware routine to test the monitors."""

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
        for monitor in sorted(hardware.values(), key=lambda x: x.__class__.__name__):
            num_errs, errors = monitor.check()
            print('  {: >20}: {}'.format(monitor.__class__.__name__, monitor.hardware_status))
            error_count += num_errs
            if num_errs > 0:
                msg = '{} reports {} error{}: '.format(monitor.__class__.__name__,
                                                       num_errs,
                                                       's' if num_errs > 1 else '')
                msg += ', '.join(errors)
                print(log_str + msg)
                monitor.recover()  # Will log recovery commands

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

    # Set modes
    while True:
        mode = input('Mount mode ({}: '.format(hardware['mnt'].available_modes))
        try:
            hardware['mnt'].mode = mode
            break
        except Exception:
            print('   Invalid mode, must be in {}'.format(hardware['mnt'].available_modes))
            continue

    while True:
        mode = input('Dome mode ({}: '.format(hardware['dome'].available_modes))
        try:
            hardware['dome'].mode = mode
            break
        except Exception:
            print('   Invalid mode, must be in {}'.format(hardware['dome'].available_modes))
            continue

    while True:
        mode = input('Camera mode ({}: '.format(hardware['cam'].available_modes))
        try:
            hardware['cam'].mode = mode
            break
        except Exception:
            print('   Invalid mode, must be in {}'.format(hardware['cam'].available_modes))
            continue

    check_hardware(hardware)
