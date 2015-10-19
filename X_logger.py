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
import time
# TeCS modules
import X_params as params

def adz(num):
    num=`num`
    if(len(num)==1):
        num='0'+num
    return num

class Logfile:
    def __init__(self,logname,filemode=1):  # filemode is 1 for file, 0 for screen
        self.filemode=1
        ut=time.gmtime()
        self.fname=params.LOG_PATH+adz(ut[0])+'_'+adz(ut[1])+'_'+adz(ut[2])+'_'+logname+'_log.txt'

    def log(self,strng,emph=0):
        if emph > 0:
            strng += '     (!)'
        if self.filemode: #save in file
            timestamp=time.strftime('%Y-%m-%d %H:%M:%S',time.gmtime())
            f=open(self.fname,'a')
            f.write(timestamp+'  '+strng+'\n')
            f.close()
        else: #print to screen
            print strng
