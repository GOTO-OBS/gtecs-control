"""Catalogs containing targets needed for the observing scripts."""

from .flats import best_flat, antisun_flat
from .flats import exposure_sequence, sky_brightness, extrapolate_from_filters
from .gliese import focus_star
from .landolt import standard_star

__all__ = [best_flat, antisun_flat, exposure_sequence, sky_brightness, extrapolate_from_filters,
           focus_star, standard_star]
