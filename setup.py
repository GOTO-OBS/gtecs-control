"""Setup script for the gtecs-control package."""
import glob

from setuptools import find_namespace_packages, setup

REQUIRES = ['Pyro4',
            'astropy>=5',
            'astroplan>=0.6',
            'numpy',
            'scipy',
            'pandas',
            'matplotlib',
            'sep',
            'requests',
            'aiohttp',
            'pyserial',
            'opencv-python',
            'pytz'
            ]

setup(name='gtecs-control',
      version='0',
      description='G-TeCS functions for telescope hardware control',
      url='http://github.com/GOTO/g-tecs',
      author='Martin Dyer',
      author_email='martin.dyer@sheffield.ac.uk',
      install_requires=REQUIRES,
      packages=find_namespace_packages(include=['gtecs*']),
      package_data={'gtecs': ['control/data/*']},
      scripts=glob.glob('scripts/*'),
      zip_safe=False,
      )
