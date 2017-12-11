"""
Time/date functions
"""

import datetime


def nightStarting():
    """
    Return the date at the start of the current astronomical night in format Y-M-D.
    """
    now = datetime.datetime.utcnow()
    if now.hour < 12: now = now - datetime.timedelta(days=1)
    return now.strftime("%Y-%m-%d")
