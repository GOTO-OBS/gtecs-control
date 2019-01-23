"""Setup script for the gtecs package."""
import glob

from gtecs import __version__

from setuptools import setup

PACKAGES = ['gtecs',
            'gtecs.daemons',
            'gtecs.hardware',
            'gtecs.catalogs',
            'gtecs.observing_scripts',
            ]

REQUIRES = ['Pyro4',
            'astropy',
            'astroplan',
            'configobj',
            'sep',
            'pid',
            'slackclient',
            'pyudev',
            'ephem',
            'pyserial',
            'opencv-python',
            ]

setup(name='gtecs',
      version=__version__,
      description='G-TeCS control system for GOTO',
      url='http://github.com/GOTO/g-tecs',
      author='Martin Dyer',
      author_email='martin.dyer@sheffield.ac.uk',
      install_requires=REQUIRES,
      packages=PACKAGES,
      package_data={'': ['data/*']},
      include_package_data=True,
      scripts=glob.glob('scripts/*'),
      zip_safe=False,
      )
