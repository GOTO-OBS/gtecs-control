#!/usr/bin/env python
"""A script to setup directory structure for G-TeCS files."""

import glob
import os
import shutil
import sys
import traceback

from gtecs import params

import pkg_resources


print('~~~~~~~~~~~~~~~~~~~~~~')
print('Setting up G-TeCS')
print('~~~~~~~~~~~~~~~~~~~~~~')

# Check for configuration file
if params.CONFIG_FILE_PATH is None:
    print('ERROR: No .gtecs.conf file found')
    sys.exit(1)
print('Using config file {}/.gtecs.conf'.format(params.CONFIG_FILE_PATH))
print('')

# Check file path is set
if params.FILE_PATH in ['/path/goes/here/', 'path_not_set', None]:
    print('ERROR: FILE_PATH not set')
    print('       You need to edit .gtecs.conf')
    sys.exit(1)
print('FILE_PATH is set to: "{}"'.format(params.FILE_PATH))
print('')

# Create directories
direcs = [params.FILE_PATH,
          params.LOG_PATH,
          params.PID_PATH,
          params.IMAGE_PATH,
          ]
try:
    for direc in direcs:
        if not os.path.exists(direc):
            os.mkdir(direc)
        print('Created ', direc)
except Exception:
    print('ERROR: Failed to create directories')
    print('       Try creating {} yourself then re-running this script'.format(params.FILE_PATH))
    traceback.print_exc()
    sys.exit(1)
print('')

# Find package data files
data_dir = pkg_resources.resource_filename('gtecs', 'data')
files = glob.glob(os.path.join(data_dir, '*[!.fit][!html]'))

# Copy files to the new directory
try:
    for data_file in files:
        shutil.copy(data_file, os.path.join(params.FILE_PATH, data_file.split('/')[-1]))
        print('Created ', data_file)
except Exception:
    print('ERROR: Failed to copy data files')
    traceback.print_exc()
    sys.exit(1)
print('')

print('Setup complete!')
