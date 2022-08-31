#!/usr/bin/env python3
"""Test code: a simple version of the pilot check_hardware routine to test the monitors."""

import time

from gtecs.control import monitors


def check_hardware(hardware):
    """Continuously monitor hardware and try to fix any issues."""
    print('hardware check routine initialised')

    sleep_time = 10
    while True:
        print('~############################~')
        start_time = time.time()
        error_count = 0
        print('running hardware checks...')
        for monitor in sorted(hardware.values(), key=lambda x: x.monitor_id):
            num_errs, errors = monitor.check()
            print('  {: >20}: {}'.format(monitor.monitor_id, monitor.hardware_status))
            error_count += num_errs
            if num_errs > 0:
                monitor.recover()  # Will log recovery commands

        print('------------------------------')
        if error_count > 0:
            sleep_time = 10  # check more frequently till fixed
            print('hardware check results: {:.0f} errors detected'.format(error_count))
        else:
            sleep_time = 10  # was 60 in pilot
            print('hardware check results: AOK')
        print('           checks took: {:.2f}s'.format(time.time() - start_time))
        print('~############################~')
        time.sleep(sleep_time)


if __name__ == '__main__':
    # hardware to keep track of and fix if necessary
    hardware = {'dome': monitors.DomeMonitor(),
                'mnt': monitors.MntMonitor(),
                'power': monitors.PowerMonitor(),
                'cam': monitors.CamMonitor(),
                'ota': monitors.OTAMonitor(),
                'filt': monitors.FiltMonitor(),
                'foc': monitors.FocMonitor(),
                'exq': monitors.ExqMonitor(),
                'conditions': monitors.ConditionsMonitor(),
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
