from __future__ import print_function
from setuptools import setup
import glob

setup(name='gtecs',
      version='0.1',
      description='G-TECS control system for GOTO',
      url='http://github.com/GOTO/g-tecs',
      author='Martin Dyer',
      author_email='martin.dyer@sheffield.ac.uk',
      packages=['gtecs', 'gtecs.tecs_modules', 'gtecs.catalogs',
                'gtecs.daemons', 'gtecs.controls', 'gtecs.database'],
      package_data={'': ['data/*']},
      install_requires=['Pyro4', 'astropy', 'astroplan', 'configobj'],
      scripts=glob.glob('scripts/*'),
      include_package_data=True,
      zip_safe=False)
