"""Simulation parameters."""

from astropy import units as u
from astropy.time import TimeDelta

# DeltaT
DELTA_T = TimeDelta(60 * u.s) * 7  # 15 * u.s)

# Telescope slew rate
SLEWRATE = 5 * u.degree / u.s

# Camera read-out time
READOUT_TIME = 10 * u.s

# Enable weather
ENABLE_WEATHER = False
