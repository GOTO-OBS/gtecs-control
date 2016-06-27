#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                               logger.py                              #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#   G-TeCS module containing standard format for creating log files    #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import time
# TeCS modules
from . import misc
from . import params

class Logfile:
    def __init__(self, logname, filemode=1):  # filemode 1 for file, 0 for screen
        self.filemode = 1
        ut = time.gmtime()
        timestring = misc.adz(ut[0]) + '_' + misc.adz(ut[1]) + '_' + misc.adz(ut[2])
        self.filename = params.LOG_PATH + timestring + '_' + logname + '_log.txt'

    def log(self, string, emph=0):
        if emph > 0:
            strng += '     (!)'
        if self.filemode:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime())
            f = open(self.filename,'a')
            f.write(timestamp + '  ' + string + '\n')
            f.close()
        else:
            print(strng)
