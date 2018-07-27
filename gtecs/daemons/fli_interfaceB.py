#!/usr/bin/env python
"""
Clone FLI interface to allow testing on a single host
"""

import sys
import pid
import time
import Pyro4

from gtecs import misc
from gtecs import params
from gtecs.daemons import run

# Directly import a copy of the real interface daemon
from fli_interface import FLIDaemon


if __name__ == "__main__":
    try:
        with pid.PidFile(misc.find_interface_ID(params.LOCAL_HOST), piddir=params.CONFIG_PATH):
            run(FLIDaemon)
    except pid.PidFileError:
        raise misc.MultipleDaemonError('Daemon already running')
