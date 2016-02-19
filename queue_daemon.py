#!/usr/bin/env python

########################################################################
#                           queue_daemon.py                            #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS daemon to control image acquisition queue           #
#                    Martin Dyer, Sheffield, 2015-16                   #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
########################################################################

### Import ###
# Python modules
from math import *
import time
import Pyro4
import threading
import os, sys, commands
from string import split,find
from collections import MutableSequence
# TeCS modules
from tecs_modules import logger
from tecs_modules import misc
from tecs_modules import params

########################################################################
# Queue daemon functions
class ExposureSpec:
    """
    Exposure specification class
    
    Contains 3 functions:
    - line_to_spec(str)
    - spec_to_line()
    - info()
    
    Exposures contain the folowing infomation:
    - run ID      [int] <automatically assigned>
    - exptime     [int] -- REQUIRED --
    - filter      [str] -- REQUIRED --
    - tel         [int] <default = 0 (all)>
    - bin factor  [int] <default = 1>
    - frame type  [str] <default = 'normal'>
    - target      [str] <default = 'N/A'>
    - image type  [str] <default = 'SCIENCE'>
    """
    def __init__(self,run_ID,exptime,filt,tel=0,bins=1,frametype='normal',target='N/A',imgtype='SCIENCE'):
        self.creation_time = time.gmtime()
        self.run_ID = run_ID
        self.exptime = exptime
        self.filt = filt
        self.tel = tel
        self.bins = bins
        self.frametype = frametype
        self.target = target
        self.imgtype = imgtype
    
    @classmethod
    def line_to_spec(cls,line):
        """Convert a line of data to exposure spec object"""
        run_ID, exptime, filt, tel, bins, frametype, target, imgtype = line.split(',')
        exp = cls(int(run_ID),int(exptime),filt,int(tel),int(bins),frametype,target,imgtype)
        return exp
    
    def spec_to_line(self):
        """Convert exposure spec object to a line of data"""
        line = '%i, %i, %s, %i, %i, %s, %s, %s\n'\
           %(self.run_ID, self.exptime, self.filt, self.tel, self.bins, self.frametype, self.target, self.imgtype)
        return line
    
    def info(self):
        """Return a readable string of summary infomation about the exposure"""
        s = 'RUN NUMBER %i\n' %self.run_ID
        s += '  '+time.strftime('%Y-%m-%d %H:%M:%S UT',self.creation_time)+'\n'
        if self.tel == 0:
            s += '  Unit telescope(s): ALL\n' %self.tel
        else:
            s += '  Unit telescope(s): %i\n' %self.tel
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
        self.queue_file = params.QUEUE_PATH + 'queue'
        
        if not os.path.exists(self.queue_file):
            f = open(self.queue_file,'w')
            f.write('#\n')
            f.close()
        
        with open(self.queue_file) as f:
            lines = f.readlines()
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
        n = len(self.data)
        s ='%i items in queue\n' %n
        for x in self.data:
            s += '\n' + x.info()
        return s

class QueueDaemon:
    """
    Queue daemon class
    
    Contains 6 functions:
    - get_info()
    - add(exptime,filt,tel,bins,frametype,target,imgtype)
    - clear()
    - get()
    - pause()
    - resume()

    """
    def __init__(self):
        self.running = True
        self.start_time = time.time()

        ### set up logfile
        self.logfile = logger.Logfile('queue',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### function flags
        self.get_info_flag = 1
        self.set_filter_flag = 0
        self.take_image_flag = 0

        ### queue variables
        self.info = {}
        self.flist = params.FILTER_LIST
        self.tel_dict = params.TEL_DICT
        self.run_number_file = 'run_number'
        self.exp_queue = Queue()
        self.exp_spec = None
        self.current_ID = None
        self.current_filter = None
        self.working = 1
        self.abort = 0
        self.paused = 1 # start paused
        
        ### start control thread
        t = threading.Thread(target=self.queue_thread)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary queue thread
    def queue_thread(self):
        
        while(self.running):
            self.time_check = time.time()
            
            ### queue processes
            # connect to daemons
            CAM_DAEMON_ADDRESS = params.DAEMONS['cam']['ADDRESS']
            cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
            cam._pyroTimeout = params.PROXY_TIMEOUT
            
            FILT_DAEMON_ADDRESS = params.DAEMONS['filt']['ADDRESS']
            filt = Pyro4.Proxy(FILT_DAEMON_ADDRESS)
            filt._pyroTimeout = params.PROXY_TIMEOUT

            # check daemon statuses
            try:
                cam_status = {}
                for tel in self.tel_dict.keys():
                    cam_status[tel] = str(cam.get_info()['status'+str(tel)])
            except:
                print 'ERROR: No responce from camera daemon'
                self.running = False
                break
            try:
                filt_status = {}
                for tel in self.tel_dict.keys():
                    filt_status[tel] = str(filt.get_info()['status'+str(tel)])
            except:
                print 'ERROR: No responce from filter wheel daemon'
                self.running = False
                break
            
            # set working flag
            if 'Exposing' in cam_status.values() or 'Moving' in filt_status.values():
                self.working = 1
            else:
                self.working = 0
            
            # check the queue, take off the first entry (if not paused) 
            self.queue_len = len(self.exp_queue)
            if (self.queue_len > 0) and not self.paused and self.current_ID == None:
                if self.current_ID == None and not self.working:
                    self.exp_spec = self.exp_queue.pop(0)
                    self.current_ID = self.exp_spec.run_ID
                    self.logfile.log('Taking exposure %s' %str(self.current_ID))
            elif self.current_ID == None:
                self.current_ID = None
                time.sleep(0.5)
            
            # take the exposure
            if self.current_ID != None:
                # set the filter
                if not self.working:
                    tel_list = [self.exp_spec.tel]
                    if tel_list == [0]:
                        tel_list = self.tel_dict.keys()
                    for tel in tel_list:
                        current_filter = self.flist[filt.get_info()['current_filter_num'+str(tel)]]
                        if current_filter != self.exp_spec.filt: # only needs to be true for one of the active tels
                            self.set_filter_flag = 1
                            self.working = 1
                # take the image
                if not self.working:
                    self.take_image_flag = 1
                    self.working = 1
                    # That's all to do here
                    self.current_ID = None
            
            ### control functions
            # request info
            if(self.get_info_flag):
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
                    info['current_exptime'] = self.exp_spec.exptime
                    info['current_filter'] = self.exp_spec.filt
                    info['current_tel'] = self.exp_spec.tel
                    info['current_bins'] = self.exp_spec.bins
                    info['current_frametype'] = self.exp_spec.frametype
                    info['current_target'] = self.exp_spec.target
                    info['current_imgtype'] = self.exp_spec.imgtype
                info['uptime'] = time.time() - self.start_time
                info['ping'] = time.time() - self.time_check
                self.info = info
                self.get_info_flag = 0
            
            # set filter
            if(self.set_filter_flag):
                new_filt = self.exp_spec.filt
                tel_list = [self.exp_spec.tel]
                try:
                    if tel_list == [0]:
                        tel_list = self.tel_dict.keys()
                    filt.set_filter(new_filt,tel_list)
                    self.current_filter = new_filt
                    self.working = 1
                    self.set_filter_flag = 0
                except:
                    print 'ERROR: No response from filter wheel daemon'
            
            # take image
            if(self.take_image_flag):
                bins = self.exp_spec.bins
                exptime = self.exp_spec.exptime
                frametype = self.exp_spec.frametype
                tel_list = [self.exp_spec.tel]
                try:
                    if tel_list == [0]:
                        tel_list = self.tel_dict.keys()
                    cam.set_bins([bins,bins],tel_list) # Assumes symmetric for now
                    cam.set_spec(self.exp_spec.run_ID,self.exp_spec.target,self.exp_spec.imgtype)
                    cam.take_image(exptime,frametype,tel_list)
                    self.working = 1
                    self.take_image_flag = 0
                except:
                    print 'ERROR: No responce from camera daemon'
            
            time.sleep(0.0001) # To save 100% CPU usage
            
        self.logfile.log('Queue thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Queue functions
    def get_info(self):
        """Return queue status info"""
        self.get_info_flag = 1
        time.sleep(0.1)
        return self.info
    
    def add(self,exptime,filt,tel=0,bins=1,frametype='normal',target='N/A',imgtype='SCIENCE'):
        """Add an exposure to the queue"""
        # Find run number
        with open(self.run_number_file,) as f:
            lines = f.readlines()
            new_run_ID = int(lines[0]) + 1
        with open(self.run_number_file,'w') as f:
            f.write(str(new_run_ID))
            
        self.exp_queue.append(ExposureSpec(new_run_ID,exptime,filt.upper(),tel,bins,frametype,target,imgtype))
        if(self.paused):
            return 'Added exposure to queue, queue is currently paused'
        else:
            return 'Added exposure to queue'
    
    def clear(self):
        """Empty the queue"""
        self.exp_queue.clear()
        return 'Queue cleared'
    
    def get(self):
        """Return info on exposures in the queue"""
        return self.exp_queue.get()
    
    def pause(self):
        """Pause the queue"""
        self.paused = 1
        return 'Queue paused'
    
    def resume(self):
        """Unpause the queue"""
        self.paused = 0
        return 'Queue resumed'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Other daemon functions
    def ping(self):
        dt_control = abs(time.time() - self.time_check)
        if dt_control > params.DAEMONS['queue']['PINGLIFE']:
            return 'Last control thread time check was %.1f seconds ago' %dt_control
        else:
            return 'ping'
    
    def prod(self):
        return
    
    def status_function(self):
        return self.running
    
    def shutdown(self):
        self.running=False

########################################################################
# Create Pyro control server 
pyro_daemon = Pyro4.Daemon(host=params.DAEMONS['queue']['HOST'], port=params.DAEMONS['queue']['PORT'])
queue_daemon = QueueDaemon()

uri = pyro_daemon.register(queue_daemon,objectId = params.DAEMONS['queue']['PYROID'])
print 'Starting queue daemon at',uri

Pyro4.config.COMMTIMEOUT = 5.
pyro_daemon.requestLoop(loopCondition=queue_daemon.status_function)

print 'Exiting queue daemon'
time.sleep(1.)
