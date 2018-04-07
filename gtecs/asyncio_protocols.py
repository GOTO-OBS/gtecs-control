"""
We communicate with external processes by defining
protocols for them. A protocol for an external
process handles communication with the process.

The results of the process are stored in a `~asyncio.Future`
and it is the protocol's job to parse the output
of a process and store the result in the `~asyncio.Future`.

This file is where we store the different
protocols necessary for communicating with
all external tasks that will be launched.
"""

import asyncio
import abc
from . import logger


class GTECSJobProtocol(asyncio.SubprocessProtocol):
    """
    A protocol class to handle communication
    between the external process launched by (e.g)
    the Pilot, and the pilot itself.

    Make concrete versions of this abstract class
    by implementing `_parseResults`, which parses the
    processess output and stores the result in the `done`
    Future.
    """
    __metaclass__ = abc.ABCMeta
    FD_NAMES = ['stdin', 'stdout', 'stderr']

    def __init__(self, jobName, done, logName=None, debug=False):
        """
        Create the protocol.

        Parameters
        -----------
        jobName : str
            A name for this job. Will be prepended to output.
        done : `~asyncio.Future`
            A Future object to store the result.
        logName : str
            Name of logger, root logger used if none
        debug : boolean
            Default: False. Enable debug output.
        """
        self.jobName = jobName
        self.done = done
        self.debug = debug
        self.buffer = bytearray()
        self.log = logger.getLogger(logName)
        super().__init__()

    def connection_made(self, transport):
        """
        Called when a new process is started.

        The transport argument is used to control
        the process.
        """
        logstr = 'process {} started'.format(transport.get_pid())
        self.log.debug('{}: {}'.format(self.jobName, logstr))
        self.transport = transport

    def pipe_data_received(self, fd, data):
        """
        Called when data written to stdout or stderr.

        Here we just print this to the screen, but eventually
        it should get logged the same way pilot output does.
        """
        logstr = 'read {} bytes from {}'.format(len(data), self.FD_NAMES[fd])
        self.log.debug('{}: {}'.format(self.jobName, logstr))

        if fd == 1:
            # data written to stdout
            # we should really write to the appropriate log here
            lines_of_output = data.decode().strip().split('\n')
            for line in lines_of_output:
                self.log.info('{}: {}'.format(self.jobName, line.strip()))
            # store in buffer for processing when we finish
            self.buffer.extend(data)

    def process_exited(self):
        logstr ='process {} exited'.format(self.transport.get_pid())
        self.log.debug('{}: {}'.format(self.jobName, logstr))

        return_code = self.transport.get_returncode()
        logstr = 'return code {}'.format(return_code)
        self.log.debug('{}: {}'.format(self.jobName, logstr))
        if not return_code:
            cmd_output = bytes(self.buffer).decode()
            results = self._parse_results(cmd_output)
        else:
            results = []
        self.done.set_result((return_code, results))

    @abc.abstractmethod
    def _parse_results(cmd_output):
        """Parse the stdout buffer and store results"""
        return


class SimpleProtocol(GTECSJobProtocol):
    """
    A simple protocol which does no parsing of the output.

    This protocol can be used to run any process where we just
    want to log the output but don't need to do anything with
    the results.
    """
    def _parse_results(self, cmd_output):
        return True
