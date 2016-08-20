"""
Script to run to pause observing for a while.
"""
from __future__ import absolute_import
from __future__ import print_function

from gtecs.tecs_modules.misc import execute_command as cmd


def run():
    print('Going into standby')
    cmd('dome close')
    cmd('mnt park')
    cmd('exq pause')
    cmd('cam abort')

if __name__ == "__main__":
    run()
