"""
Script to run to resume observing.
"""
from __future__ import absolute_import
from __future__ import print_function

from gtecs.tecs_modules.misc import execute_command as cmd


def run():
    print('Starting observations')
    cmd('dome open')
    cmd('mnt unpark')
    cmd('exq resume')

if __name__ == "__main__":
    run()
