"""Conditions functions for miscellaneous (non-weather) flags."""

import os
import subprocess

from .. import params
from ..hardware.power import APCUPS, APCUPS_USB, FakeUPS


def get_ups():
    """Get battery percent remaining and current status from GOTO UPSs."""
    percents = []
    statuses = []
    for unit_name in params.POWER_UNITS:
        unit_class = params.POWER_UNITS[unit_name]['CLASS']
        if 'UPS' not in unit_class:
            continue
        else:
            try:
                unit_ip = params.POWER_UNITS[unit_name]['IP']
                if unit_class == 'APCUPS':
                    ups = APCUPS(unit_ip)
                elif unit_class == 'APCUPS_USB':
                    unit_port = int(params.POWER_UNITS[unit_name]['PORT'])
                    ups = APCUPS_USB(unit_ip, unit_port)
                elif unit_class == 'FakeUPS':
                    ups = FakeUPS(unit_ip)
                else:
                    raise ValueError('Unrecognised power class: "{}"'.format(unit_class))

                remaining = ups.percent_remaining()
                percents.append(remaining)

                # Check status too
                status = ups.status()
                if status != 'Normal':
                    normal = False
                else:
                    normal = True
                statuses.append(normal)
            except Exception:
                percents.append(-999)
                statuses.append(-999)
    return percents, statuses


def check_ping(url, count=3, timeout=10):
    """Ping a url, and check it responds."""
    try:
        ping_command = 'ping -c {} {}'.format(count, url)
        out = subprocess.check_output(ping_command.split(),
                                      stderr=subprocess.STDOUT,
                                      timeout=timeout)
        if 'ttl=' in str(out):
            return True
        else:
            return False
    except Exception:
        return False


def get_diskspace_remaining(path):
    """Get the percentage diskspace remaining from a given path."""
    statvfs = os.statvfs(path)

    available = statvfs.f_bsize * statvfs.f_bavail / 1024
    total = statvfs.f_bsize * statvfs.f_blocks / 1024

    return available / total
