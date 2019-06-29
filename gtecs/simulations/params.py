"""Simulation parameters."""

from astropy import units as u

# Telescope slew rate
SLEWRATE = 5 * u.degree / u.s

# Camera read-out time
READOUT_TIME = 10 * u.s

# Pilot params
TIMESTEP = 60 * u.s
ENABLE_WEATHER = False
SLEEP_TIME = 0
WRITE_QUEUE = False
WRITE_HTML = False
