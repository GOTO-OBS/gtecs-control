from __future__ import print_function
from setuptools import setup
import glob
from gtecs.tecs_modules.params import GTECS_VERSION

setup(name='gtecs',
      version=GTECS_VERSION,
      description='G-TECS control system for GOTO',
      url='http://github.com/GOTO/g-tecs',
      author='Martin Dyer',
      author_email='martin.dyer@sheffield.ac.uk',
      packages=['gtecs', 'gtecs.tecs_modules', 'gtecs.catalogs',
                'gtecs.daemons', 'gtecs.controls', 'gtecs.observing_scripts',
                'gtecs.database'],
      package_data={'': ['data/*','html/*']},
      install_requires=['Pyro4', 'astropy', 'astroplan', 'configobj', 'sep'],
      scripts=glob.glob('scripts/*'),
      include_package_data=True,
      zip_safe=False)
