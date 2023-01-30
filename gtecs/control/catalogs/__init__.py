"""Catalogs containing targets needed for the observing scripts."""

from .flats import antisun_flat, best_flat, exposure_sequence, sky_brightness
from .gliese import focus_star
from .landolt import standard_star

__all__ = [best_flat, antisun_flat, exposure_sequence, sky_brightness,
           focus_star,
           standard_star,
           ]
