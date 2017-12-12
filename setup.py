from setuptools import setup
import glob
from gtecs import __version__

setup(name='gtecs',
      version=__version__,
      description='G-TeCS control system for GOTO',
      url='http://github.com/GOTO/g-tecs',
      author='Martin Dyer',
      author_email='martin.dyer@sheffield.ac.uk',
      packages=['gtecs', 'gtecs.daemons', 'gtecs.controls', 'gtecs.catalogs',
                'gtecs.observing_scripts', 'gtecs.database'],
      package_data={'': ['data/*','html/*']},
      install_requires=['Pyro4', 'astropy', 'astroplan', 'configobj', 'sep'],
      scripts=glob.glob('scripts/*'),
      include_package_data=True,
      zip_safe=False)
