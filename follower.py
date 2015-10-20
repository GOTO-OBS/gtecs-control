#!/usr/bin/env python

########################################################################
#                              follower.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#     G-TeCS script to provide regularly updated status infomation     #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
import time
import Pyro4
# TeCS modules
import X_params as params
import X_misc as misc


def get_info():
    MNT_DAEMON_ADDRESS=params.DAEMONS['mnt']['ADDRESS']
    mnt=Pyro4.Proxy(MNT_DAEMON_ADDRESS)
    #try:
    mnt.get_info()
    time.sleep(0.1) # Wait for it to update
    info = mnt.report_to_UI('info')
    
    if info['targ_ra'] != None and info['targ_dec'] != None:
        targ_dist = misc.ang_sep(info['tel_ra'],info['tel_dec'],info['targ_ra'],info['targ_dec'])
    else:
        targ_dist = None
    
    print '#### MOUNT INFO ####'
    if info['status'] != 'Slewing':
        print 'Status: %s' %info['status']
    else:
        print 'Status: %s (%.2f)' %(info['status'],targ_dist)
    print '~~~~~~~'
    print 'Telescope RA:  %.4f' %info['tel_ra']
    print 'Telescope Dec: %.4f' %info['tel_dec']
    if info['targ_ra'] != None:
        print 'Target RA:     %.4f' %info['targ_ra']
    else:
        print 'Target RA:     NONE'
    if info['targ_dec'] != None:
        print 'Target Dec:    %.4f' %info['targ_dec']
    else:
        print 'Target Dec:    NONE'
    if targ_dist != None:
        print 'Target dist:   %.4f' %targ_dist
    else:
        print 'Target dist:   N/A'
    print 'Telescope Alt: %.2f' %info['tel_alt']
    print 'Telescope Az:  %.2f' %info['tel_az']
    print 'Step size: %.2f arcsec' %info['step']
    print '~~~~~~~'
    print 'LST: %.2f' %info['lst']
    print 'Hour Angle: %.2f' %info['ha']
    print 'Tel Time: %s' %info['teltime']
    #print 'UTC: %s' %info['ut']
    print '~~~~~~~'
    print 'Uptime: %.1fs' %info['uptime']
    print 'Ping: %.3fs' %info['ping']
    #print 'Site Long: %.2f' %info['long']
    #print 'Site Lat: %.2f' %info['lat']
    #print 'Site Eliv: %.2f' %info['eliv']
    print '####################'
    #except:
        #print 'No response from daemon'
    
if __name__ == '__main__':
    
    while 1:
        get_info()
        time.sleep(1.)
