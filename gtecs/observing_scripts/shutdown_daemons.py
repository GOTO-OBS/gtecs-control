"""
Script to run to shutdown the daemons.
"""
from __future__ import absolute_import
from __future__ import print_function

from gtecs.tecs_modules.misc import execute_command as cmd


def run():
    # start the daemons
    cmd('lilith shutdown')


if __name__ == "__main__":
    run()
