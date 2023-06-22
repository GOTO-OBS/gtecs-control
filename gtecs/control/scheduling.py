"""Convenience functions for communicating with the scheduler."""

import json

import aiohttp

import requests

from . import params
from .daemons import daemon_proxy


def get_pointing_info(*args, **kwargs):
    """Get the info dict for the given Pointing from the scheduler."""
    if params.SCHEDULER_CHECK_METHOD == 'pyro':
        return get_pointing_info_pyro(*args, **kwargs)
    elif params.SCHEDULER_CHECK_METHOD == 'server':
        return get_pointing_info_server(*args, **kwargs)
    else:
        raise ValueError(f'Unknown scheduler check method: {params.SCHEDULER_CHECK_METHOD}')


def scheduler_proxy(asynchronous=False, timeout=30):
    """Get a proxy to the scheduler."""
    proxy = daemon_proxy('scheduler', params.SCHEDULER_HOST, params.SCHEDULER_PORT, timeout=timeout)
    if asynchronous:
        proxy._pyroAsync()  # functions will return Pyro.Future
    return proxy


def update_schedule_pyro(current_pointing=None, current_status=None,
                         shielding=False, request_pointing=True, force_update=False,
                         asynchronous=False):
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


def get_pointing_info_pyro(pointing_id):
    """Get the info dict for the given Pointing from the scheduler."""
    with scheduler_proxy() as scheduler:
        pointing_info = scheduler.get_pointing_info(pointing_id)
    return pointing_info


def update_schedule_server(current_pointing=None, current_status=None,
                           shielding=False, request_pointing=True, force_update=False):
    """Update the observing database and get what to observe next from the scheduler."""
    url = f'http://{params.SCHEDULER_HOST}:{params.SCHEDULER_PORT}/scheduler/'
    url += f'update_schedule/{params.TELESCOPE_NUMBER}'
    query_params = {
        'api_key': params.SCHEDULER_API_KEY,
        'current_pointing_id': current_pointing if current_pointing is not None else 'None',
        'current_status': current_status if current_status is not None else 'None',
        'horizon': 1 if shielding else 0,
        'return_new': 1 if request_pointing else 0,
        'force_update': 1 if force_update else 0,
    }
    print(url)
    with requests.Session() as session:
        response = session.get(url, params=query_params)
        response.raise_for_status()  # Raise any HTTP errors
        return json.loads(response.text)


async def update_schedule_server_async(current_pointing=None, current_status=None,
                                       shielding=False, request_pointing=True, force_update=False):
    """Update the observing database and get what to observe next from the scheduler."""
    url = f'http://{params.SCHEDULER_HOST}:{params.SCHEDULER_PORT}/scheduler'
    url += f'update_schedule/{params.TELESCOPE_NUMBER}'
    query_params = {
        'api_key': params.SCHEDULER_API_KEY,
        'current_pointing_id': current_pointing if current_pointing is not None else 'None',
        'current_status': current_status if current_status is not None else 'None',
        'horizon': 1 if shielding else 0,
        'return_new': 1 if request_pointing else 0,
        'force_update': 1 if force_update else 0,
    }
    print(url)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=query_params) as response:
            response.raise_for_status()  # Raise any HTTP errors
            reply = await response.text()
            return json.loads(reply)


def get_pointing_info_server(pointing_id):
    """Get the info dict for the given Pointing from the scheduler."""
    url = f'http://{params.SCHEDULER_HOST}:{params.SCHEDULER_PORT}/scheduler'
    url += f'pointing_info/{pointing_id}'
    query_params = {
            'api_key': params.SCHEDULER_API_KEY,
        }
    with requests.Session() as session:
        response = session.get(url, params=query_params)
        response.raise_for_status()  # Raise any HTTP errors
        return json.loads(response.text)
