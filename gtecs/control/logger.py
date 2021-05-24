"""Standard format for creating log files."""

import logging
import os
import sys
import time
from logging import handlers

from . import params


def get_file_handler(name=None):
    """Get the file handler."""
    if name is not None:
        logfile = name + '.log'
    else:
        logfile = 'master.log'
    fname = os.path.join(params.LOG_PATH, logfile)
    file_handler = handlers.WatchedFileHandler(fname, delay=True)

    # formatter for stdout logging; does not include name of log
    formatter = logging.Formatter(
        '%(asctime)s:%(levelname)s - %(message)s',
        datefmt='%Y/%m/%d %H:%M:%S'
    )
    formatter.converter = time.gmtime
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    return file_handler


def get_stream_handler():
    """Get the stream handler."""
    # formatter for stdout logging; includes name of log
    formatter = logging.Formatter(
        '%(asctime)s.%(msecs)03d:%(name)s:%(levelname)s - %(message)s',
        datefmt='%Y/%m/%d %H:%M:%S'
    )
    formatter.converter = time.gmtime

    # add output to stdout
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    return console


def get_logger(name=None, log_stdout=False, log_to_file=True, log_to_stdout=True):
    """Provide standardised logging to all processes.

    Each logger can write to stdout and a file name 'name.log'
    in the appropriate log directory. If no name is provided,
    the logger will write to a file called master.log

    By default all levels from DEBUG up are written to the logfile,
    and all levels from INFO up are written to stdout.

    This function will not rename logfiles based on the date. The idea is
    to use the UNIX utility logrotate to rotate the log files daily.

    Use of this function should help reduce the myriad ways logging was
    done under the pt5m software. The hope is also that it can tidy up
    the way in which some scripts write some info to stdout and some to
    a logfile, and the stdout info is then written to another logfile.

    Parameters
    ----------
    name : str
        the name of the logger, which is also used for the name of the logfile
    log_stdout : bool
        whether to log all stdout, not just log commands
    log_to_file : bool
        whether to log to file or not
    log_to_stdout : bool
        whether to log to stdout or not

    Returns
    --------
    log : `logging.Logger`
        a Logger class to use for Logging.

    Example
    --------
    The function can be used as follows::

        log = get_logger('pilot')
        if pilot.running:
            log.info('Pilot started successfully')
        else:
            log.error('Pilot not started!')
        # log exceptions to file and stdout
        try:
            print "Hi There"  # breaks in python 3
        except Exception:
            log.exception('')

    """
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)

    # if the handlers are not empty this has been called
    # before and we shouldn't add more handlers
    if log.handlers != []:
        return log

    # add a stdout handler
    if log_to_stdout:
        log.addHandler(get_stream_handler())

    # add a file handler
    if log_to_file:
        log.addHandler(get_file_handler(name))

    # redirect system stdout
    if log_stdout:
        sys.stdout = StreamToLogger(log, logging.INFO)
        sys.stderr = StreamToLogger(log, logging.ERROR)

    return log


class StreamToLogger(object):
    """Fake file-like stream object that redirects writes to a logger instance."""

    def __init__(self, logger, log_level=logging.INFO):
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ''

    def write(self, buf):
        """Write to the stream."""
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, line.rstrip())

    def flush(self):
        """Flush the stream."""
        pass


def set_logger_output(logger, log_to_file=True, log_to_stdout=True):
    """Add or remove handlers to a logger to print to file or stdout.

    Parameters
    ----------
    logger : `logging.Logger`
        the logger to add or remove handlers from
    log_to_file : bool
        ensure logger logs to file
    log_to_stdout : bool
        ensure logger logs to stdout

    """
    file_logger_truefalse = [isinstance(hl, logging.handlers.WatchedFileHandler)
                             for hl in logger.handlers]
    already_has_file_logger = any(file_logger_truefalse)
    stdout_logger_truefalse = [isinstance(hl, logging.StreamHandler)
                               for hl in logger.handlers]
    already_has_stdout_logger = any(stdout_logger_truefalse)

    if log_to_file:
        if not already_has_file_logger:
            logger.addHandler(get_file_handler(logger.name))
    else:
        if already_has_file_logger:
            handler = logger.handlers[file_logger_truefalse.index(True)]
            logger.removeHandler(handler)

    if log_to_stdout:
        if not already_has_stdout_logger:
            logger.addHandler(get_stream_handler())
    else:
        if already_has_stdout_logger:
            handler = logger.handlers[stdout_logger_truefalse.index(True)]
            logger.removeHandler(handler)
