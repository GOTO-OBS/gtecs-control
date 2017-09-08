#!/usr/bin/env python

########################################################################
#                            exq_daemon.py                             #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#               G-TeCS daemon to control exposure queue                #
#                    Martin Dyer, Sheffield, 2015-16                   #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
from math import *
import time, datetime
import Pyro4
import threading
import os, sys
from collections import MutableSequence
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.controls.exq_control import ExposureSpec, ExposureQueue
from gtecs.tecs_modules.daemons import HardwareDaemon

########################################################################
# Exposure queue daemon class

class ExqDaemon(HardwareDaemon):
    """Exposure queue hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'exq')

        ### exposure queue variables
        self.info = {}
        self.flist = params.FILTER_LIST
        self.tel_dict = params.TEL_DICT
        self.exp_queue = ExposureQueue()
        self.exp_spec = None
        self.current_filter = None
        self.abort = 0
        self.working = 0
        self.paused = 1 # start paused

        self.dependency_error = 0
        self.dependency_check_time = 0

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def _control_thread(self):
        self.logfile.info('Daemon control thread started')

        # connect to daemons
        CAM_DAEMON_ADDRESS = params.DAEMONS['cam']['ADDRESS']
        cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
        cam._pyroTimeout = params.PROXY_TIMEOUT

        FILT_DAEMON_ADDRESS = params.DAEMONS['filt']['ADDRESS']
        filt = Pyro4.Proxy(FILT_DAEMON_ADDRESS)
        filt._pyroTimeout = params.PROXY_TIMEOUT

        while(self.running):
            self.time_check = time.time()

            ### check dependencies
            if (self.time_check - self.dependency_check_time) > 2:
                if not misc.dependencies_are_alive('exq'):
                    if not self.dependency_error:
                        self.logfile.error('Dependencies are not responding')
                        self.dependency_error = 1
                        # pause the queue
                        self.paused = 1
                else:
                    if self.dependency_error:
                        self.logfile.info('Dependencies responding again')
                        self.dependency_error = 0
                        # unpause the queue
                        self.paused = 0
                self.dependency_check_time = time.time()

            if self.dependency_error:
                time.sleep(5)
                continue

            ### exposure queue processes

            # check the queue, take off the first entry (if not paused)
            self.queue_len = len(self.exp_queue)
            if (self.queue_len > 0) and not self.paused and not self.working:
                # OK - time to add a new exposure
                self.exp_spec = self.exp_queue.pop(0)
                self.logfile.info('Taking exposure')
                self.working = 1
                # we need to set filter and take image
                try:
                    self._set_filter(filt)
                except:
                    self.logfile.error('set_filter command failed')
                    self.logfile.debug('', exc_info=True)
                try:
                    self._take_image(cam)
                except:
                    self.logfile.error('take_image command failed')
                    self.logfile.debug('', exc_info=True)
                self.working = 0

            elif self.queue_len == 0 or self.paused:
                # either we are paused, or nothing in the queue
                time.sleep(1.0)

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Exposure queue functions
    def get_info(self):
        """Return exposure queue status info"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Exq info is outside the loop
        info = {}
        if self.paused:
            info['status'] = 'Paused'
        elif self.working:
            info['status'] = 'Working'
        else:
            info['status'] = 'Ready'
        info['queue_length'] = self.queue_len
        if self.working and self.exp_spec != None:
            info['current_tel_list'] = self.exp_spec.tel_list
            info['current_exptime'] = self.exp_spec.exptime
            info['current_filter'] = self.exp_spec.filt
            info['current_binning'] = self.exp_spec.binning
            info['current_frametype'] = self.exp_spec.frametype
            info['current_target'] = self.exp_spec.target
            info['current_imgtype'] = self.exp_spec.imgtype

        info['uptime'] = time.time() - self.start_time
        info['ping'] = time.time() - self.time_check
        now = datetime.datetime.utcnow()
        info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

        # Return the updated info dict
        return info


    def add(self, tel_list, exptime, filt,
            binning=1, frametype='normal', target='NA', imgtype='SCIENCE'):
        """Add an exposure to the queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in self.tel_dict:
                raise ValueError('Unit telescope ID not in list {}'.format(list(self.tel_dict)))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt.upper() not in self.flist:
            raise ValueError('Filter not in list %s' %str(self.flist))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in ['normal', 'dark']:
            raise ValueError("Frame type must be 'normal' or 'dark'")

        # Call the command
        exposure = ExposureSpec(tel_list, exptime, filt.upper(),
                                binning, frametype,
                                target.replace(';', ''),
                                imgtype.replace(';', ''))
        self.exp_queue.append(exposure)

        # Format return string
        s = 'Added exposure, now %i items in queue' %len(self.exp_queue)
        if self.paused:
            s += ' [paused]'
        return s


    def add_multi(self, Nexp, tel_list, exptime, filt,
                  binning=1, frametype='normal', target='NA', imgtype='SCIENCE',
                  expID = 0):
        """Add multiple exposures to the queue as a set"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in self.tel_dict:
                raise ValueError('Unit telescope ID not in list {}'.format(list(self.tel_dict)))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt.upper() not in self.flist:
            raise ValueError('Filter not in list %s' %str(self.flist))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in ['normal', 'dark']:
            raise ValueError("Frame type must be 'normal' or 'dark'")

        # Call the command
        for i in range(Nexp):
            set_pos = i+1
            set_total = Nexp
            exposure = ExposureSpec(tel_list, exptime, filt.upper(),
                                    binning, frametype,
                                    target.replace(';', ''),
                                    imgtype.replace(';', ''),
                                    set_pos, set_total, expID)
            self.exp_queue.append(exposure)

        # Format return string
        s = 'Added %i exposure(s), now %i items in queue' %(Nexp, len(self.exp_queue))
        if self.paused:
            s += ' [paused]'
        return s


    def clear(self):
        """Empty the exposure queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Call the command
        self.exp_queue.clear()

        return 'Queue cleared'


    def get(self):
        """Return info on exposures in the queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Call the command
        queue_info = self.exp_queue.get()

        return queue_info


    def get_simple(self):
        """Return simple info on exposures in the queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Call the command
        queue_info_simple = self.exp_queue.get_simple()

        return queue_info_simple


    def pause(self):
        """Pause the queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set values
        self.paused = 1

        return 'Queue paused'


    def resume(self):
        """Unpause the queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set values
        self.paused = 0

        return 'Queue resumed'

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Internal functions
    def _set_filter(self, filt):
        new_filt = self.exp_spec.filt
        tel_list = self.exp_spec.tel_list
        try:
            filt._pyroReconnect()
            filt.set_filter(new_filt, tel_list)
            self.current_filter = new_filt
        except:
            self.logfile.error('No response from filter wheel daemon')
            self.logfile.debug('', exc_info=True)

        time.sleep(1)
        filt_info_dict = filt.get_info()
        filt_status = {tel: filt_info_dict['status%d' % tel] for tel in self.tel_dict}
        while('Moving' in filt_status.values()):
            try:
                filt_info_dict = filt.get_info()
            except Pyro4.errors.TimeoutError:
                pass
            filt_status = {tel: filt_info_dict['status%d' % tel] for tel in self.tel_dict}
            time.sleep(0.005)
            # keep ping alive
            self.time_check = time.time()

    def _take_image(self, cam):
        binning = self.exp_spec.binning
        exptime = self.exp_spec.exptime
        tel_list = self.exp_spec.tel_list
        try:
            cam._pyroReconnect()
            cam.set_binning(binning, tel_list)
            cam.set_spec(self.exp_spec.target, self.exp_spec.imgtype,
                         self.exp_spec.set_pos, self.exp_spec.set_total,
                         self.exp_spec.expID)
            time.sleep(0.1)
            if self.exp_spec.frametype == 'normal':
                cam.take_image(exptime, tel_list)
            elif self.exp_spec.frametype == 'dark':
                cam.take_dark(exptime, tel_list)
        except:
            self.logfile.error('No response from camera daemon')
            self.logfile.debug('', exc_info=True)

        time.sleep(1)
        cam_info_dict = cam.get_info()
        cam_status = {tel: cam_info_dict['status%d' % tel] for tel in self.tel_dict}
        while('Exposing' in cam_status.values() or 'Reading' in cam_status.values()):
            try:
                cam_info_dict = cam.get_info()
            except Pyro4.errors.TimeoutError:
                pass
            cam_status = {tel: cam_info_dict['status%d' % tel] for tel in self.tel_dict}
            time.sleep(0.05)
            # keep ping alive
            self.time_check = time.time()

########################################################################

def start():
    '''
    Create Pyro server, register the daemon and enter request loop
    '''
    host = params.DAEMONS['exq']['HOST']
    port = params.DAEMONS['exq']['PORT']

    # Check the daemon isn't already running
    if not misc.there_can_only_be_one('exq'):
        sys.exit()

    # Start the daemon
    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        exq_daemon = ExqDaemon()
        uri = pyro_daemon.register(exq_daemon, objectId='exq')
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        exq_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=exq_daemon.status_function)

    # Loop has closed
    exq_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
