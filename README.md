# G-TeCS control package

**G-TeCS** (*gee-teks*) is the GOTO Telescope Control System.

This package (`gtecs-control`) contains the core hardware control scripts used for operating the telescope both manually and autonomously.

Note this module is Python3 only and has been developed for Linux, otherwise use at your own risk.

## Requirements

This package requires several Python modules, which should be included during installation.

This package requires the following G-TeCS packages to function fully:

- [gtecs-common](https://github.com/GOTO-OBS/gtecs-common)
- [gtecs-obs](https://github.com/GOTO-OBS/gtecs-obs)
- [gtecs-alert](https://github.com/GOTO-OBS/gtecs-alert)

This package requires the following other packages created for GOTO:

- [FLI-API](https://github.com/GOTO-OBS/fli-api) (to control the FLI cameras)

## Installation

Once you've downloaded or cloned the repository, in the base directory run:

    pip3 install . --user

You should then be able to import the module from within Python.

Several scripts from the `scripts` folder should also be added to your path.

### Configuration

The module will look for a file named `.gtecs.conf` either in the user's home directory, the `gtecs` subdirectory, or a path specified by the `GTECS_CONF` environment variable. An example file is included in the base directory of this repository.

## Testing

TODO

## Usage

TODO
