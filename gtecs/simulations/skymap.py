"""Skymap functions for simulations."""

import math

from gototile.skymap import SkyMap
from gototile.skymaptools import tile_skymap
from gototile.telescope import GOTON4

import numpy as np

import obsdb as db


def update_skymap_probabilities(session, survey):
    """When a GW tile is observed, update all the Survey tile probabilities.

    THIS SHOUD BE MOVED SOMEWHERE BETTER!
    """
    print('    Updating skymap probabilities: ', end='\t')
    try:
        completed_pointings = session.query(db.Pointing).filter(
            db.Pointing.survey == survey).filter(
            db.Pointing.status == 'completed').all()

        completed_tilenames = [p.grid_tile.name for p in completed_pointings]

        filepath = survey.event.skymap
        skymap = SkyMap(filepath)

        pointings = tile_skymap(skymap, [GOTON4()],
                                observed=[completed_tilenames])

        i = 0
        for tile in survey.survey_tiles:
            old_prob = float(tile.current_weight)
            index = np.where(pointings['fieldname'] == tile.survey_tile.name)[0][0]
            new_prob = float(pointings['prob'][index])

            if not math.isclose(old_prob, new_prob, abs_tol=0.0000001):
                i += 1
                if new_prob < 0.001:
                    new_prob = 0
                tile.current_weight = new_prob
        print(' updated {:.0f} tiles'.format(i))

        session.commit()
        return 0
    except Exception:
        print('ERROR')
        session.rollback()
        return 1
