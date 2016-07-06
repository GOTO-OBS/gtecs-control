from __future__ import print_function
from setuptools import setup
import glob
import os
import shutil

# create CONFIG_DIR if necessary and put files there
from gtecs.tecs_modules import params
if not os.path.exists(params.CONFIG_PATH):
      os.mkdir(params.CONFIG_PATH)
      os.mkdir(os.path.join(params.CONFIG_PATH, 'logs'))
      os.mkdir(os.path.join(params.CONFIG_PATH, 'images'))
shutil.copy('gtecs/data/conditions_flags', os.path.join(params.CONFIG_PATH, 'conditions_flags'))
shutil.copy('gtecs/data/overrides_flags', os.path.join(params.CONFIG_PATH, 'overrides_flags'))
shutil.copy('gtecs/data/run_number', os.path.join(params.CONFIG_PATH, 'run_number'))

setup(name='gtecs',
      version='0.1',
      description='G-TECS control system for GOTO',
      url='http://github.com/GOTO/g-tecs',
      author='Martin Dyer',
      author_email='martin.dyer@sheffield.ac.uk',
      packages=['gtecs', 'gtecs.tecs_modules', 'gtecs.catalogs',
                'gtecs.daemons'],
      package_data={'': ['data/*.ini']},
      install_requires=['Pyro4', 'astropy', 'configobj'],
      scripts=glob.glob('scripts/*'),
      include_package_data=True,
      zip_safe=False)