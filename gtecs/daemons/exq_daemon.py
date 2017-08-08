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
import ast
# TeCS modules
from gtecs.tecs_modules import logger
from gtecs.tecs_modules import misc
from gtecs.tecs_modules import params
from gtecs.tecs_modules.daemons import HardwareDaemon

########################################################################
# Exposure queue classes

class ExposureSpec:
    """
    Exposure specification class

    Contains 3 functions:
    - line_to_spec(str)
    - spec_to_line()
    - info()

    Exposures contain the folowing infomation:
    - tel_list    [lst] -- REQUIRED --
    - exptime     [int] -- REQUIRED --
    - filter      [str] -- REQUIRED --
    - binning     [int] <default = 1>
    - frame type  [str] <default = 'normal'>
    - target      [str] <default = 'NA'>
    - image type  [str] <default = 'SCIENCE'>
    - set_pos     [int] <default = 1>
    - set_total   [int] <default = 1>
    - expID       [int] <default = None>
    """
    def __init__(self, tel_list, exptime, filt,
                 binning=1, frametype='normal', target='NA', imgtype='SCIENCE',
                 set_pos = 1, set_total = 1, expID = None):
        self.creation_time = time.gmtime()
        self.tel_list = tel_list
        self.exptime = exptime
        self.filt = filt
        self.binning = binning
        self.frametype = frametype
        self.target = target
        self.imgtype = imgtype
        self.set_pos = set_pos
        self.set_total = set_total
        if expID:
            self.expID = expID
        else:
            self.expID = 0

    @classmethod
    def line_to_spec(cls, line):
        """Convert a line of data to exposure spec object"""
        # eg '[1, 2, 4];20;R;2;normal;NA;SCIENCE;1;3;126598'
        ls = line.split(';')
        tel_list = ast.literal_eval(ls[0])
        exptime = float(ls[1])
        filt = ls[2]
        binning = int(ls[3])
        frametype = ls[4]
        target = ls[5]
        imgtype = ls[6]
        set_pos = int(ls[7])
        set_total = int(ls[8])
        expID = int(ls[9])
        exp = cls(tel_list, exptime, filt,
                  binning, frametype, target, imgtype,
                  set_pos, set_total, expID)
        return exp

    def spec_to_line(self):
        """Convert exposure spec object to a line of data"""
        line = '%s;%.1f;%s;%i;%s;%s;%s;%i;%i;%i\n'\
           %(self.tel_list, self.exptime, self.filt,
             self.binning, self.frametype, self.target, self.imgtype,
             self.set_pos, self.set_total, self.expID)
        return line

    def info(self):
        """Return a readable string of summary infomation about the exposure"""
        s = 'EXPOSURE \n'
        s += '  '+time.strftime('%Y-%m-%d %H:%M:%S UT',self.creation_time)+'\n'
        s += '  Unit telescope(s): %s\n' %self.tel_list
        s += '  Exposure time: %is\n' %self.exptime
        s += '  Filter: %s\n' %self.filt
        s += '  Binning: %i\n' %self.binning
        s += '  Frame type: %s\n' %self.frametype
        s += '  Target: %s\n' %self.target
        s += '  Image type: %s\n' %self.imgtype
        s += '  Position in set: %i\n' %self.set_pos
        s += '  Total in set: %i\n' %self.set_total
        s += '  ExposureSet database ID (if any): %i\n' %self.expID
        return s

class Queue(MutableSequence):
    """
    Queue sequence to hold exposures

    Contains 4 functions:
    - write_to_file()
    - insert(index,value)
    - clear()
    - get()
    """
    def __init__(self):
        self.data = []
        self.queue_file = os.path.join(params.CONFIG_PATH, 'exposure_queue')

        if not os.path.exists(self.queue_file):
            f = open(self.queue_file,'w')
            f.write('#\n')
            f.close()

        with open(self.queue_file) as f:
            lines = f.read().splitlines()
            for line in lines:
                if not line.startswith('#'):
                    self.data.append(ExposureSpec.line_to_spec(line))

    def write_to_file(self):
        """Write the current queue to the queue file"""
        with open(self.queue_file,'w') as f:
            for exp in self.data:
                f.write(exp.spec_to_line())

    def __getitem__(self,index):
        return self.data[index]

    def __setitem__(self,index,value):
        self.data[index] = value
        self.write_to_file()

    def __delitem__(self,index):
        del self.data[index]
        self.write_to_file()

    def __len__(self):
        return len(self.data)

    def insert(self,index,value):
        """Add an item to the queue at a specified position"""
        self.data.insert(index,value)
        self.write_to_file()

    def clear(self):
        """Empty the current queue and queue file"""
        self.data = []
        self.write_to_file()

    def get(self):
        """Return info() for all exposures in the queue"""
        s ='%i items in queue:\n' %len(self.data)
        for i,x in enumerate(self.data):
            s += str(i+1) + ': ' + x.info()
        return s.rstrip()

    def get_simple(self):
        """Return string for all exposures in the queue"""
        s ='%i items in queue:\n' %len(self.data)
        for i,x in enumerate(self.data):
            s += str(i+1) + ': ' + x.spec_to_line()
        return s.rstrip()

########################################################################
# Exposure queue daemon class

class ExqDaemon(HardwareDaemon):
    """
    Exposure queue daemon class

    Contains 6 functions:
    - get_info()
    - add(exptime,filt,tel,binning,frametype,target,imgtype)
    - clear()
    - get()
    - pause()
    - resume()

    """

    def __init__(self):
        ### initiate daemon
        HardwareDaemon.__init__(self, 'exq')

        ### exposure queue variables
        self.info = {}
        self.flist = params.FILTER_LIST
        self.tel_dict = params.TEL_DICT
        self.exp_queue = Queue()
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
                self._set_filter(filt)
                self._take_image(cam)
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
        if self.dependency_error:
            return 'ERROR: Dependencies are not running'
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
        return info

    def add(self, tel_list, exptime, filt,
            binning=1, frametype='normal', target='NA', imgtype='SCIENCE'):
        """Add an exposure to the queue"""
        if self.dependency_error:
            return 'ERROR: Dependencies are not running'
        filt = filt.upper()
        target = target.replace(';', '')
        imgtype = imgtype.replace(';', '')

        # check if valid
        if filt.upper() not in self.flist:
            return 'ERROR: Filter not in list %s' %str(self.flist)

        exposure = ExposureSpec(tel_list, exptime, filt,
                                binning, frametype, target, imgtype)
        self.exp_queue.append(exposure)

        if self.paused:
            return 'Added exposure, now %i items in queue [paused]' %len(self.exp_queue)
        else:
            return 'Added exposure, now %i items in queue' %len(self.exp_queue)

    def add_multi(self, Nexp, tel_list, exptime, filt,
                  binning=1, frametype='normal', target='NA', imgtype='SCIENCE',
                  expID = 0):
        """Add multiple exposures to the queue as a set"""
        if self.dependency_error:
            return 'ERROR: Dependencies are not running'
        filt = filt.upper()
        target = target.replace(';', '')
        imgtype = imgtype.replace(';', '')

        # check if valid
        if filt.upper() not in self.flist:
            return 'ERROR: Filter not in list %s' %str(self.flist)

        s = ''
        for i in range(Nexp):
            set_pos = i+1
            set_total = Nexp
            exposure = ExposureSpec(tel_list, exptime, filt,
                                    binning, frametype, target, imgtype,
                                    set_pos, set_total, expID)
            self.exp_queue.append(exposure)

            if self.paused:
                s += 'Added exposure, now %i items in queue [paused]\n' %len(self.exp_queue)
            else:
                s += 'Added exposure, now %i items in queue\n' %len(self.exp_queue)
        return s[:-1]

    def clear(self):
        """Empty the exposure queue"""
        if self.dependency_error:
            return 'ERROR: Dependencies are not running'
        self.exp_queue.clear()
        return 'Queue cleared'

    def get(self):
        """Return info on exposures in the queue"""
        if self.dependency_error:
            return 'ERROR: Dependencies are not running'
        return self.exp_queue.get()

    def get_simple(self):
        """Return simple info on exposures in the queue"""
        if self.dependency_error:
            return 'ERROR: Dependencies are not running'
        return self.exp_queue.get_simple()

    def pause(self):
        """Pause the queue"""
        if self.dependency_error:
            return 'ERROR: Dependencies are not running'
        self.paused = 1
        return 'Queue paused'

    def resume(self):
        """Unpause the queue"""
        if self.dependency_error:
            return 'ERROR: Dependencies are not running'
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
