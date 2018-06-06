#!/usr/bin/env python
"""
Daemon to control the exposure queue
"""

import os
import sys
import time
import datetime
from math import *
import Pyro4
import threading
from collections import MutableSequence

from gtecs import logger
from gtecs import misc
from gtecs import params
from gtecs.controls.exq_control import Exposure, ExposureQueue
from gtecs.daemons import HardwareDaemon

DAEMON_ID = 'exq'
DAEMON_HOST = params.DAEMONS[DAEMON_ID]['HOST']
DAEMON_PORT = params.DAEMONS[DAEMON_ID]['PORT']


class ExqDaemon(HardwareDaemon):
    """Exposure queue hardware daemon class"""

    def __init__(self):
        ### initiate daemon
        self.daemon_id = DAEMON_ID
        HardwareDaemon.__init__(self, self.daemon_id)

        ### exposure queue variables
        self.info = None

        self.exp_queue = ExposureQueue()
        self.current_exposure = None

        self.working = 0
        self.paused = 1 # start paused

        self.dependency_error = 0
        self.dependency_check_time = 0

        ### start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()


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
                if not misc.dependencies_are_alive(self.daemon_id):
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
                self.current_exposure = self.exp_queue.pop(0)
                self.logfile.info('Taking exposure')
                self.working = 1

                # set the filter, if needed
                if self._need_to_change_filter(filt):
                    try:
                        self._set_filter(filt)
                    except:
                        self.logfile.error('set_filter command failed')
                        self.logfile.debug('', exc_info=True)
                    # sleep briefly, to make sure the filter wheel has stopped
                    time.sleep(0.5)
                else:
                    self.logfile.info('No need to move filter wheel')

                # take the image
                try:
                    self._take_image(cam)
                except:
                    self.logfile.error('take_image command failed')
                    self.logfile.debug('', exc_info=True)

                # done!
                self.working = 0

            elif self.queue_len == 0 or self.paused:
                # either we are paused, or nothing in the queue
                time.sleep(1.0)

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return


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
        if self.working and self.current_exposure != None:
            info['current_tel_list'] = self.current_exposure.tel_list
            info['current_exptime'] = self.current_exposure.exptime
            info['current_filter'] = self.current_exposure.filt
            info['current_binning'] = self.current_exposure.binning
            info['current_frametype'] = self.current_exposure.frametype
            info['current_target'] = self.current_exposure.target
            info['current_imgtype'] = self.current_exposure.imgtype

        info['uptime'] = time.time() - self.start_time
        info['ping'] = time.time() - self.time_check
        now = datetime.datetime.utcnow()
        info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")

        # Return the updated info dict
        return info


    def get_info_simple(self):
        """Return plain status dict, or None"""
        try:
            info = self.get_info()
        except:
            return None
        return info


    def add(self, tel_list, exptime,
            filt=None, binning=1, frametype='normal',
            target='NA', imgtype='SCIENCE'):
        """Add an exposure to the queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt and filt.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list %s' %str(params.FILTER_LIST))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError("Frame type must be in {}".format(params.FRAMETYPE_LIST))

        # Call the command
        exposure = Exposure(tel_list, exptime,
                            filt.upper() if filt else None,
                            binning, frametype,
                            target.replace(';', ''),
                            imgtype.replace(';', ''))
        self.exp_queue.append(exposure)
        self.logfile.info('Added {:.0f}s {} exposure, now {:.0f} in queue'.format(
                exptime, filt.upper() if filt else 'X', len(self.exp_queue)))

        # Format return string
        s = 'Added {:.0f}s {} exposure,'.format(exptime, filt.upper() if filt else 'X')
        s += ' now {} items in queue'.format(len(self.exp_queue))
        if self.paused:
            s += ' [paused]'
        return s


    def add_multi(self, Nexp, tel_list, exptime,
                  filt=None, binning=1, frametype='normal',
                  target='NA', imgtype='SCIENCE',
                  expID = 0):
        """Add multiple exposures to the queue as a set"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt and filt.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list %s' %str(params.FILTER_LIST))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError("Frame type must be in {}".format(params.FRAMETYPE_LIST))

        # Call the command
        for i in range(Nexp):
            set_pos = i+1
            set_total = Nexp
            exposure = Exposure(tel_list, exptime,
                                filt.upper() if filt else None,
                                binning, frametype,
                                target.replace(';', ''),
                                imgtype.replace(';', ''),
                                set_pos, set_total, expID)
            self.exp_queue.append(exposure)
            self.logfile.info('Added {:.0f}s {} exposure, now {:.0f} in queue'.format(
                    exptime, filt.upper() if filt else 'X', len(self.exp_queue)))

        # Format return string
        s = 'Added {}x {:.0f}s {} exposure(s),'.format(Nexp, exptime,
                                                      filt.upper() if filt else 'X')
        s += ' now {} items in queue'.format(len(self.exp_queue))
        if self.paused:
            s += ' [paused]'
        return s


    def clear(self):
        """Empty the exposure queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Call the command
        num_in_queue = len(self.exp_queue)
        self.exp_queue.clear()

        self.logfile.info('Cleared {} items from queue'.format(num_in_queue))
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

        self.logfile.info('Queue paused')
        return 'Queue paused'


    def resume(self):
        """Unpause the queue"""
        # Check restrictions
        if self.dependency_error:
            raise misc.DaemonDependencyError('Dependencies are not running')

        # Set values
        self.paused = 0

        self.logfile.info('Queue resumed')
        return 'Queue resumed'


    # Internal functions
    def _need_to_change_filter(self, filt):
        new_filt = self.current_exposure.filt
        if new_filt is None:
            # filter doesn't matter, e.g. dark
            return False

        tel_list = self.current_exposure.tel_list
        filt._pyroReconnect()
        filt_info = filt.get_info()
        if all([params.FILTER_LIST[filt_info['current_filter_num'+str(tel)]] == new_filt for tel in tel_list]):
            return False
        else:
            return True

    def _set_filter(self, filt):
        new_filt = self.current_exposure.filt
        tel_list = self.current_exposure.tel_list
        self.logfile.info('Setting filter to {} on {!r}'.format(new_filt, tel_list))
        try:
            filt._pyroReconnect()
            filt.set_filter(new_filt, tel_list)
        except:
            self.logfile.error('No response from filter wheel daemon')
            self.logfile.debug('', exc_info=True)

        time.sleep(1)
        filt_info_dict = filt.get_info()
        filt_status = {tel: filt_info_dict['status%d' % tel] for tel in params.TEL_DICT}
        while('Moving' in filt_status.values()):
            try:
                filt_info_dict = filt.get_info()
            except Pyro4.errors.TimeoutError:
                pass
            filt_status = {tel: filt_info_dict['status%d' % tel] for tel in params.TEL_DICT}
            time.sleep(0.005)
            # keep ping alive
            self.time_check = time.time()
        self.logfile.info('Filter wheel move complete')


    def _take_image(self, cam):
        exptime = self.current_exposure.exptime
        binning = self.current_exposure.binning
        frametype = self.current_exposure.frametype
        tel_list = self.current_exposure.tel_list
        self.logfile.info('Taking exposure ({:.0f}s, {:.0f}x{:.0f}, {}) on {!r}'.format(
                                exptime, binning, binning, frametype, tel_list))
        try:
            cam._pyroReconnect()
            cam.take_exposure(self.current_exposure)
        except:
            self.logfile.error('No response from camera daemon')
            self.logfile.debug('', exc_info=True)

        time.sleep(1)
        cam_info_dict = cam.get_info()
        cam_status = {tel: cam_info_dict['status%d' % tel] for tel in params.TEL_DICT}
        while('Exposing' in cam_status.values() or 'Reading' in cam_status.values()):
            try:
                cam_info_dict = cam.get_info()
            except Pyro4.errors.TimeoutError:
                pass
            cam_status = {tel: cam_info_dict['status%d' % tel] for tel in params.TEL_DICT}
            time.sleep(0.05)
            # keep ping alive
            self.time_check = time.time()
        self.logfile.info('Camera exposure complete')


if __name__ == "__main__":
    # Check the daemon isn't already running
    if not misc.there_can_only_be_one(DAEMON_ID):
        sys.exit()

    # Create the daemon object
    daemon = ExqDaemon()

    # Start the daemon
    with Pyro4.Daemon(host=DAEMON_HOST, port=DAEMON_PORT) as pyro_daemon:
        uri = pyro_daemon.register(daemon, objectId=DAEMON_ID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=daemon.status_function)

    # Loop has closed
    daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)
