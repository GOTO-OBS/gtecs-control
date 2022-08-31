"""Convenience functions for communicating with the scheduler."""

from . import params
from .daemons import daemon_proxy


def scheduler_proxy(asynchronous=False, timeout=30):
    """Get a proxy to the scheduler."""
    proxy = daemon_proxy('scheduler', params.SCHEDULER_HOST, params.SCHEDULER_PORT, timeout=timeout)
    if asynchronous:
        proxy._pyroAsync()  # functions will return Pyro.Future
    return proxy


def update_schedule(current_pointing, current_status, shielding=False,
                    request_pointing=True, asynchronous=False, force_update=False):
    """Update the observing database and get what to observe next from the scheduler."""
    with scheduler_proxy(asynchronous) as scheduler:
        new_pointing = scheduler.update_schedule(params.TELESCOPE_NUMBER,
                                                 current_pointing,
                                                 current_status,
                                                 horizon=1 if shielding else 0,
                                                 return_new=request_pointing,
                                                 force_update=force_update,
                                                 )
    return new_pointing


def get_pointing_info(pointing_id):
    """Get the info dict for the given Pointing from the scheduler."""
    with scheduler_proxy() as scheduler:
        pointing_info = scheduler.get_pointing_info(pointing_id)
    return pointing_info
