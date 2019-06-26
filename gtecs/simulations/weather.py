"""Class to simulate bad weather for simulations."""

from astropy import units as u

import george

import numpy as np

from scipy import stats


class Weather(object):
    """Simulate bad weather using Gaussian processes.

    Args:
    -----
    start_time : astropy.time.Time
        start of night (sunset)

    stop_time : astropy.time.Time
        end of night (sunset)

    timescale : float, optional
        typical timescale of weather event (hours)
        default is 1h

    frac_bad : float, optional
        average fraction of night lost to bad weather (0-1)
        default is 0.1

    """

    def __init__(self, start_time, stop_time, timescale=1, frac_bad=0.1):
        self.start_time = start_time
        self.stop_time = stop_time
        self.kernel = george.kernels.Matern32Kernel(timescale)
        self.gp = george.GP(self.kernel)

        # evaluate guassian process on grid of hours between stop and start
        time_range = stop_time - start_time
        x = np.linspace(0, time_range.to(u.hour), 100)
        e = 0.0001 * np.ones_like(x)
        # evaluate kernel of GP
        self.gp.compute(x, e)

        # now draw a sample from the GP to represent tonight's weather
        self.weather_graph = self.gp.sample(x)

        # GP follows Gaussian statistics with sigma=1.
        # for a given fraction of bad time, we can work out the amplitude
        # to use as a threshold for the GP using the percent point function
        # (the inverse of the cumulative distribution function
        # if weather_graph is below this threshold weather is bad!
        self.threshold = stats.norm.ppf(frac_bad)

    def is_bad(self, curr_time):
        """Return if the weather is bad at the given time."""
        x = (curr_time - self.start_time).to(u.hour)
        val, uncer = self.gp.predict(self.weather_graph, x)
        if val < self.threshold:
            return True
        else:
            return False
