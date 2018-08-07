"""Asyncio protocols for use with the pilot.

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

import abc
import asyncio

from . import logger


class GTECSJobProtocol(asyncio.SubprocessProtocol, metaclass=abc.ABCMeta):
    """A protocol class to handle communication between the external process and the pilot itself.

    Make concrete versions of this abstract class
    by implementing `_parseResults`, which parses the
    processess output and stores the result in the `done`
    Future.
    """

    FD_NAMES = ['stdin', 'stdout', 'stderr']

    def __init__(self, job_name, done, log_name=None, debug=False):
        """Create the protocol.

        Parameters
        -----------
        job_name : str
            A name for this job. Will be prepended to output.
        done : `~asyncio.Future`
            A Future object to store the result.
        log_name : str
            Name of logger, root logger used if none
        debug : boolean
            Default: False. Enable debug output.

        """
        self.job_name = job_name
        self.done = done
        self.debug = debug
        self.buffer = bytearray()
        self.log = logger.get_logger(log_name)
        super().__init__()

    def connection_made(self, transport):
        """Run when a new process is started.

        The transport argument is used to control
        the process.
        """
        logstr = 'process {} started'.format(transport.get_pid())
        self.log.debug('{}: {}'.format(self.job_name, logstr))
        self.transport = transport

    def pipe_data_received(self, fd, data, log_bytes=False):
        """Run when data written to stdout or stderr.

        Here we just print this to the screen, but eventually
        it should get logged the same way pilot output does.
        """
        logstr = 'read {} bytes from {}'.format(len(data), self.FD_NAMES[fd])
        if log_bytes:
            self.log.debug('{}: {}'.format(self.job_name, logstr))

        if fd == 1:
            # data written to stdout
            # we should really write to the appropriate log here
            lines_of_output = data.decode().strip().split('\n')
            for line in lines_of_output:
                self.log.info('{}: {}'.format(self.job_name, line.strip()))
            # store in buffer for processing when we finish
            self.buffer.extend(data)
        elif fd == 2:
            # data written to stderr
            lines_of_output = data.decode().strip().split('\n')
            for line in lines_of_output:
                self.log.error('{}: {}'.format(self.job_name, line.strip()))
            # store in buffer for processing when we finish
            self.buffer.extend(data)

    def process_exited(self):
        """Run when a process exits."""
        logstr = 'process {} exited'.format(self.transport.get_pid())
        self.log.debug('{}: {}'.format(self.job_name, logstr))

        return_code = self.transport.get_returncode()
        logstr = 'return code {}'.format(return_code)
        self.log.debug('{}: {}'.format(self.job_name, logstr))
        if not return_code:
            cmd_output = bytes(self.buffer).decode()
            results = self._parse_results(cmd_output)
        else:
            results = []
        self.done.set_result((return_code, results))

    @abc.abstractmethod
    def _parse_results(self, cmd_output):
        """Parse the stdout buffer and store results."""
        return


class SimpleProtocol(GTECSJobProtocol):
    """A simple protocol which does no parsing of the output.

    This protocol can be used to run any process where we just
    want to log the output but don't need to do anything with
    the results.
    """

    def _parse_results(self, cmd_output):
        return True
