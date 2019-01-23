"""Setup script for the gtecs package."""
import glob

from gtecs import __version__

from setuptools import setup

setup(name='gtecs',
      version=__version__,
      description='G-TeCS control system for GOTO',
      url='http://github.com/GOTO/g-tecs',
      author='Martin Dyer',
      author_email='martin.dyer@sheffield.ac.uk',
      packages=['gtecs', 'gtecs.daemons', 'gtecs.hardware', 'gtecs.catalogs',
                'gtecs.observing_scripts'],
      package_data={'': ['data/*']},
      install_requires=['Pyro4', 'astropy', 'astroplan', 'configobj', 'sep',
                        'pid'],
      scripts=glob.glob('scripts/*'),
      include_package_data=True,
      zip_safe=False)
