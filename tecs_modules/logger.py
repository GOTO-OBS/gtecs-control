#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                               logger.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#   G-TeCS module containing standard format for creating log files    #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import logging
from logging import handlers
import time
import sys
import os
# TeCS modules
from . import params


def getFileHandler(name=None):
    log_dir = params.LOG_PATH
    if name is not None:
        fname = os.path.join(log_dir, name + '.log')
    else:
        fname = os.path.join(log_dir, 'master.log')
    file_handler = handlers.WatchedFileHandler(
        fname,
        delay=True  # don't create file until first write
    )
    # formatter for stdout logging; does not include name of log
    formatter = logging.Formatter(
        '%(asctime)s:%(levelname)s - %(message)s',
        datefmt='%Y/%m/%d %H:%M:%S'
    )
    formatter.converter = time.gmtime
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    return file_handler


def getStreamHandler():
    # formatter for stdout logging; includes name of log
    formatter = logging.Formatter(
        '%(asctime)s:%(name)s:%(levelname)s - %(message)s',
        datefmt='%Y/%m/%d %H:%M:%S'
    )
    formatter.converter = time.gmtime

    # add output to stdout
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    return console


def getLogger(name=None, file_logging=True, stdout_logging=True):
    """
    Function to provide standardised logging to all processes.

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
    file_logging : bool
        whether to log to file or not
    stdout_logging : bool
        whether to log to stdout or not

    Returns
    --------
    log : `logging.Logger`
        a Logger class to use for Logging.

    Example
    --------
    The function can be used as follows::

        log = getLogger('pilot')
        if pilot.running:
            log.info('Pilot started successfully')
        else:
            log.error('Pilot not started!')
        # log exceptions to file and stdout
        try:
            print "Hi There"  # breaks in python 3
        except:
            log.exception('')
    """
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)

    # if the handlers are not empty this has been called
    # before and we shouldn't add more handlers
    if log.handlers != []:
        return log

    # add a stdout handler
    if stdout_logging:
        log.addHandler(getStreamHandler())

    # add a file handler
    if file_logging:
        log.addHandler(getFileHandler(name))
    return log


def setLoggerOutput(logger, file_logging=True, stdout_logging=True):
    """
    Adds or removes handlers to a logger to print to file or stdout.

    Parameters
    ----------
    logger : `logging.Logger`
        the logger to add or remove handlers from
    file_logging : bool
        ensure logger logs to file
    stdout_logging : bool
        ensure logger logs to stdout
    """
    file_logger_truefalse = [isinstance(hl, logging.handlers.WatchedFileHandler)
                             for hl in logger.handlers]
    already_has_file_logger = any(file_logger_truefalse)
    stdout_logger_truefalse = [isinstance(hl, logging.StreamHandler)
                               for hl in logger.handlers]
    already_has_stdout_logger = any(stdout_logger_truefalse)

    if file_logging:
        if not already_has_file_logger:
            logger.addHandler(getFileHandler(logger.name))
    else:
        if already_has_file_logger:
            handler = logger.handlers[file_logger_truefalse.index(True)]
            logger.removeHandler(handler)

    if stdout_logging:
        if not already_has_stdout_logger:
            logger.addHandler(getStreamHandler())
    else:
        if already_has_stdout_logger:
            handler = logger.handlers[stdout_logger_truefalse.index(True)]
            logger.removeHandler(handler)
