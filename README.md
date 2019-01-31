# G-TeCS

**G-TeCS** (*gee-teks*) is the **Te**lescope **C**ontrol **S**ystem for the **G**OTO observatory.

Note this module is Python3 only and has been developed for Linux, otherwise use at your own risk.

## Requirements

G-TeCS requires a lot of Python modules, which should be included during installation. Notably the pilot is built around `asyncio`, so can't be run on any version before Python 3.5.

To work fully G-TeCS also requires other GOTO modules to be installed:

- [ObsDB](https://github.com/GOTO-OBS/goto-obsdb)
- [GOTO-tile](https://github.com/GOTO-OBS/goto-tile)
- [GOTO-alert](https://github.com/GOTO-OBS/goto-alert)
- [FLI-API](https://github.com/GOTO-OBS/fli-api)

## Installation

Once you've downloaded or cloned the repository, in the base directory run:

    pip3 install . --user

You should then be able to import the module using `import gtecs` within Python.

Several scripts from the `scripts` folder should also be added to your path.

### Configuration

The module will look for a file named `.gtecs.conf` either in the user's home directory or any path specified by the `GTECS_CONF` environment variable. An example file is included in the base directory of this repository.

When installing G-TeCS, copy the included `.gtecs.conf` file to one of the above locations, and change the `FILE_PATH` parameter to specify where you want G-TeCS to save files. Once that has been done run the `setup_gtecs.py` script to create the expected directory structure at that location.

## Usage instructions

See the GitHub wiki: <https://github.com/GOTO-OBS/g-tecs/wiki>.
