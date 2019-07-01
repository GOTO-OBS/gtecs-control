"""Simulation parameters."""

from astropy import units as u

# Telescope slew rate
SLEWRATE = 5 * u.degree / u.s

# Camera read-out time
READOUT_TIME = 10 * u.s
