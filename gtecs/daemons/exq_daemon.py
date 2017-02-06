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
    - run ID      [int] <automatically assigned>
    - tel_list    [lst] -- REQUIRED --
    - exptime     [int] -- REQUIRED --
    - filter      [str] -- REQUIRED --
    - bin factor  [int] <default = 1>
    - frame type  [str] <default = 'normal'>
    - target      [str] <default = 'N/A'>
    - image type  [str] <default = 'SCIENCE'>
    """
    def __init__(self,run_ID,tel_list,exptime,filt,bins=1,frametype='normal',target='N/A',imgtype='SCIENCE'):
        self.creation_time = time.gmtime()
        self.run_ID = run_ID
        self.tel_list = tel_list
        self.exptime = exptime
        self.filt = filt
        self.bins = bins
        self.frametype = frametype
        self.target = target
        self.imgtype = imgtype

    @classmethod
    def line_to_spec(cls,line):
        """Convert a line of data to exposure spec object"""
        # eg '00033;[1, 2, 4];20;R;2;normal;N/A;SCIENCE'
        ls = line.split(';')
        run_ID = int(ls[0])
        tel_list = ast.literal_eval(ls[1])
        exptime = float(ls[2])
        filt = ls[3]
        bins = int(ls[4])
        frametype = ls[5]
        target = ls[6]
        imgtype = ls[7]
        exp = cls(run_ID,tel_list,exptime,filt,bins,frametype,target,imgtype)
        return exp

    def spec_to_line(self):
        """Convert exposure spec object to a line of data"""
        line = '%05d;%s;%.1f;%s;%i;%s;%s;%s\n'\
           %(self.run_ID,self.tel_list,self.exptime,self.filt,self.bins,self.frametype,self.target,self.imgtype)
        return line

    def info(self):
        """Return a readable string of summary infomation about the exposure"""
        s = 'RUN NUMBER %05d\n' %self.run_ID
        s += '  '+time.strftime('%Y-%m-%d %H:%M:%S UT',self.creation_time)+'\n'
        s += '  Unit telescope(s): %s\n' %self.tel_list
        s += '  Exposure time: %is\n' %self.exptime
        s += '  Filter: %s\n' %self.filt
        s += '  Bins: %i\n' %self.bins
        s += '  Frame type: %s\n' %self.frametype
        s += '  Target: %s\n' %self.target
        s += '  Image type: %s\n' %self.imgtype
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
        for x in self.data:
            s += x.info()
        return s.rstrip()

    def get_simple(self):
        """Return string for all exposures in the queue"""
        s ='%i items in queue:\n' %len(self.data)
        for x in self.data:
            s += x.spec_to_line()
        return s.rstrip()

########################################################################
# Exposure queue daemon class

class ExqDaemon(HardwareDaemon):
    """
    Exposure queue daemon class

    Contains 6 functions:
    - get_info()
    - add(exptime,filt,tel,bins,frametype,target,imgtype)
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
        self.run_number_file = os.path.join(params.CONFIG_PATH, 'run_number')
        self.exp_queue = Queue()
        self.exp_spec = None
        self.current_ID = None
        self.current_filter = None
        self.abort = 0
        self.working = 0
        self.paused = 1 # start paused

        ### start control thread
        t = threading.Thread(target=self.exq_control)
        t.daemon = True
        t.start()

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary control thread
    def exq_control(self):
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

            ### exposure queue processes

            # check the queue, take off the first entry (if not paused)
            self.queue_len = len(self.exp_queue)
            if (self.queue_len > 0) and not self.paused and self.current_ID == None:
                if not self.working:
                    # OK - time to add a new exposure
                    self.exp_spec = self.exp_queue.pop(0)
                    self.current_ID = self.exp_spec.run_ID
                    self.logfile.info('Taking exposure %s', str(self.current_ID))
                    self.working = 1
                    # we need to set filter and take image
                    self._set_filter(filt)
                    self._take_image(cam)
                    self.working = 0
                    self.current_ID = None

            elif self.current_ID == None:
                # either we are paused, or nothing in the queue
                time.sleep(1.0)

            time.sleep(0.0001) # To save 100% CPU usage

        self.logfile.info('Daemon control thread stopped')
        return

    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Exposure queue functions
    def get_info(self):
        """Return exposure queue status info"""
        info = {}
        if self.paused:
            info['status'] = 'Paused'
        elif self.working:
            info['status'] = 'Working'
        else:
            info['status'] = 'Ready'
        info['queue_length'] = self.queue_len
        if self.working and self.exp_spec != None:
            info['current_run_ID'] = self.exp_spec.run_ID
            info['current_tel_list'] = self.exp_spec.tel_list
            info['current_exptime'] = self.exp_spec.exptime
            info['current_filter'] = self.exp_spec.filt
            info['current_bins'] = self.exp_spec.bins
            info['current_frametype'] = self.exp_spec.frametype
            info['current_target'] = self.exp_spec.target
            info['current_imgtype'] = self.exp_spec.imgtype
        info['uptime'] = time.time() - self.start_time
        info['ping'] = time.time() - self.time_check
        now = datetime.datetime.utcnow()
        info['timestamp'] = now.strftime("%Y-%m-%d %H:%M:%S")
        return info

    def add(self,tel_list,exptime,filt,bins=1,frametype='normal',target='N/A',imgtype='SCIENCE'):
        """Add an exposure to the queue"""
        # check if valid
        if filt.upper() not in self.flist:
            return 'ERROR: Filter not in list %s' %str(self.flist)

        # find and update run number
        with open(self.run_number_file,) as f:
            lines = f.readlines()
            new_run_ID = int(lines[0]) + 1
        with open(self.run_number_file,'w') as f:
            f.write(str(new_run_ID))

        self.exp_queue.append(ExposureSpec(new_run_ID,tel_list,exptime,filt.upper(),bins,frametype,target.replace(';',''),imgtype.replace(';','')))
        if(self.paused):
            return 'Added exposure, now %i items in queue [paused]' %len(self.exp_queue)
        else:
            return 'Added exposure, now %i items in queue' %len(self.exp_queue)

    def clear(self):
        """Empty the exposure queue"""
        self.exp_queue.clear()
        return 'Queue cleared'

    def get(self):
        """Return info on exposures in the queue"""
        return self.exp_queue.get()

    def get_simple(self):
        """Return simple info on exposures in the queue"""
        return self.exp_queue.get_simple()

    def pause(self):
        """Pause the queue"""
        self.paused = 1
        return 'Queue paused'

    def resume(self):
        """Unpause the queue"""
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
        bins = self.exp_spec.bins
        exptime = self.exp_spec.exptime
        tel_list = self.exp_spec.tel_list
        try:
            cam._pyroReconnect()
            cam.set_bins([bins, bins], tel_list)  # Assumes symmetric for now
            cam.set_spec(self.exp_spec.run_ID, self.exp_spec.target,
                         self.exp_spec.imgtype)
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
    pyroID = params.DAEMONS['exq']['PYROID']

    with Pyro4.Daemon(host=host, port=port) as pyro_daemon:
        exq_daemon = ExqDaemon()
        uri = pyro_daemon.register(exq_daemon, objectId=pyroID)
        Pyro4.config.COMMTIMEOUT = 5.

        # Start request loop
        exq_daemon.logfile.info('Daemon registered at %s', uri)
        pyro_daemon.requestLoop(loopCondition=exq_daemon.status_function)

    # Loop has closed
    exq_daemon.logfile.info('Daemon successfully shut down')
    time.sleep(1.)

if __name__ == "__main__":
    start()
