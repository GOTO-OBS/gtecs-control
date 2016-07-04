#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                                time_date.py                          #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#  G-TeCS module containing time/date functions used by TeCS processes #
#                 Stuart Littlefair, Sheffield, 2015                   #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import datetime


def nightStarting():
    """
    Return the date at the start of the current astronomical night in format Y-M-D.
    """
    now = datetime.datetime.utcnow()
    if now.hour < 12: now = now - datetime.timedelta(days=1)
    return now.strftime("%Y-%m-%d")
