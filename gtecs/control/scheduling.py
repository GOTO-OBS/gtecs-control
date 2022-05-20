"""Convenience functions for communicating with the scheduler."""

from . import params
from .daemons import daemon_proxy


def check_schedule(shielding=False, asynchronous=False, force_update=False):
    """Check the scheduler for the highest priority Pointing to observe."""
    with daemon_proxy('scheduler', params.SCHEDULER_HOST, params.SCHEDULER_PORT) as scheduler:
        if asynchronous:
            scheduler._pyroAsync()  # result will be a Pyro.Future
        horizon = 1 if shielding else 0
        pointing_info = scheduler.check_queue(params.TELESCOPE_NUMBER, horizon, force_update)
    return pointing_info


def get_pointing_info(pointing_id):
    """Get the info dict for the given Pointing from the scheduler."""
    with daemon_proxy('scheduler', params.SCHEDULER_HOST, params.SCHEDULER_PORT) as scheduler:
        pointing_info = scheduler.get_pointing_info(pointing_id)
    return pointing_info


def mark_pointing(pointing_id, status):
    """Update a Pointing's status through the scheduler."""
    with daemon_proxy('scheduler', params.SCHEDULER_HOST, params.SCHEDULER_PORT) as scheduler:
        if status == 'running':
            scheduler.mark_pointing_running(pointing_id, params.TELESCOPE_NUMBER)
        elif status == 'completed':
            scheduler.mark_pointing_completed(pointing_id)
        elif status == 'interrupted':
            scheduler.mark_pointing_interrupted(pointing_id)
        else:
            raise ValueError('Invalid status: {}'.format(status))
