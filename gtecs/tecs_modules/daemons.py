#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                              daemons.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#      G-TeCS module containing generic daemon classes & functions     #
#                     Martin Dyer, Sheffield, 2017                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import time
import Pyro4

# TeCS modules
from . import logger
from . import params
from . import misc

########################################################################
# Super classes

class HardwareDaemon(object):
    """
    Generic hardware daemon class
    """

    def __init__(self, daemon_ID):
        self.daemon_ID = daemon_ID
        self.running = True
        self.start_time = time.time()
        self.time_check = time.time()

        # set up logfile
        self.logfile = logger.getLogger(self.daemon_ID,
                                        file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
        self.logfile.info('Daemon created')

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Common daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS[self.daemon_ID]['PINGLIFE']:
            return 'ERROR: Last control thread time check was '\
                   '%.1f seconds ago' %dt_control
        else:
            return 'ping'

    def prod(self):
        return

    def status_function(self):
        return self.running

    def shutdown(self):
        self.running = False


class InterfaceDaemon(object):
    """
    Generic interface daemon class
    """

    def __init__(self, interface_ID):
        self.interface_ID = interface_ID
        self.running = True
        self.start_time = time.time()

        # set up logfile
        self.logfile = logger.getLogger(self.interface_ID,
                                        file_logging=params.FILE_LOGGING,
                                        stdout_logging=params.STDOUT_LOGGING)
        self.logfile.info('Daemon created')

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Common daemon functions
    def ping(self):
        return 'ping'

    def prod(self):
        return

    def status_function(self):
        return self.running

    def shutdown(self):
        self.running = False
