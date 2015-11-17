#!/usr/bin/env python

########################################################################
#                           queue_daemon.py                            #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#           G-TeCS daemon to control image acquisition queue           #
#                     Martin Dyer, Sheffield, 2015                     #
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
import X_params as params
import X_misc as misc
import X_logger as logger

########################################################################
# Queue daemon functions
class ExposureSpec:
    """
    Exposure specification class
    
    Contains 3 functions:
    - from_line(line)
    - to_line()
    - info()
    
    Exposures contain the folowing infomation:
    - exptime     [int]
    - filter      [str]
    - bin factor  [int]
    - frametype   [str] <deafult = 'normal'>
    - object name [str] <deafult = ''>
    - image type  [str] <deafult = 'SCIENCE'>
    - database ID [str] <deafult = 'manual'>
    - glance      [T/F] <deafult = False>    
    """
    def __init__(self,exptime,filt,bins,frametype='normal',obj='',imgtype='SCIENCE',dbID='manual',glance=False):
        self.creation_time = time.gmtime()
        self.exptime = exptime
        self.filt = filt
        self.bins = bins
        self.frametype = frametype
        self.obj = obj
        self.imgtype = imgtype
        self.dbID = dbID
        self.glance = glance
        self.filename = None
    
    @classmethod
    def from_line(cls,line):
        """Convert a line of data to expsoure spec object"""
        exptime,filt,bins,frametype,obj,imgtype,dbID,glance = line.split(',')
        glance = (glance == 'True') #Converts from string 'True'/'False' to boolean, safer than eval()
        exp = cls(float(exptime),filt,int(bins),frametype,obj,imgtype,dbID,glance)
        return exp
    
    def to_line(self):
        """Convert exposure spec object to a line of data"""
        return '%i, %s, %i, %s, %s, %s, %s, %s\n' %(self.exptime,self.filt,self.bins,self.frametype,self.obj,self.imgtype,self.dbID,str(self.glance))
    
    def info(self):
        """Return a readable string of summary infomation about the exposure"""
        s = time.strftime('%Y-%m-%d %H:%M:%S UT',self.creation_time)+'\n'
        s += '  Exposure time: %i s\n' % self.exptime
        s += '  Filter: %s\n' % self.filt
        s += '  Bins: %i\n' % self.bins
        s += '  Frametype: %s\n' %self.frametype
        return s
    
class Queue(MutableSequence):
    """
    Queue sequence to hold exposures
    
    Contains x functions:
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
                    self.data.append(ExposureSpec.from_line(line))
    
    def write_to_file(self):
        """Write the current queue to the queue file"""
        with open(self.queue_file,'w') as f:
            for exp in self.data:
                f.write(exp.to_line())
    
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
    
    Contains x functions:
    - get_info()
    - add(exptime,filt,bins,frametype,obj,imgtype,dbID,glance)
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
            print '~~~~~~~~'
            # connect to daemons
            CAM_DAEMON_ADDRESS = params.DAEMONS['cam']['ADDRESS']
            cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
            
            FILT_DAEMON_ADDRESS = params.DAEMONS['filt']['ADDRESS']
            filt = Pyro4.Proxy(FILT_DAEMON_ADDRESS)

            # check daemon statuses
            try:
                cam_status = cam.get_info()['status']
            except:
                print 'ERROR: No responce from camera daemon'
                self.running = False
                break
            try:
                filt_status = filt.get_info()['status']
            except:
                print 'ERROR: No responce from filter wheel daemon'
                self.running = False
                break
            
            # set working flag
            if cam_status == 'Exposing' or filt_status == 'Moving':
                self.working = 1
            else:
                self.working = 0
            print '   Cam: %s | Filt: %s | Working: %i' %(cam_status, filt_status,self.working)
            
            # check the queue, take off the first entry (if not paused) 
            self.queue_len = len(self.exp_queue)
            print '   Exposures in queue: %i' %self.queue_len
            if (self.queue_len > 0) and not self.paused and self.current_ID == None:
                if self.current_ID == None and not self.working:
                    self.exp_spec = self.exp_queue.pop(0)
                    self.current_ID = self.exp_spec.dbID
                    self.logfile.log('Taking exposure %s' %str(self.current_ID))
                    print '     Taking exposure:',self.current_ID
            elif self.current_ID == None:
                print '     Queue is empty, or paused'
                self.current_ID = None
                time.sleep(0.5)
            
            # take the exposure
            if self.current_ID != None:
                # set the filter
                if not self.working and self.exp_spec.filt != self.current_filter:
                    print '   Filter:'
                    self.set_filter_flag = 1
                    self.working = 1
                # take the image
                if not self.working:
                    print '   Camera:'
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
                    info['status'] = 'Taking exposure'
                else:
                    info['status'] = 'Ready'
                info['queue_length'] = self.queue_len
                if self.working and self.exp_spec != None:
                    info['current_exptime'] = self.exp_spec.exptime
                    info['current_filter'] = self.exp_spec.filt
                    info['current_bins'] = self.exp_spec.bins
                    info['current_frametype'] = self.exp_spec.frametype
                info['uptime'] = time.time()-self.start_time
                info['ping'] = time.time()-self.time_check
                self.info = info
                self.get_info_flag = 0
            
            # set filter
            if(self.set_filter_flag):
                new_filt = self.exp_spec.filt
                try:
                    print '     Set filter to:', new_filt
                    filt.set_filter(new_filt)
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
                try:
                    print '     Set bin factor to:', bins
                    cam.set_binning(bins,bins) # Assumes symmetric for now
                    print '     Taking exposure of:', exptime
                    cam.take_image(exptime,frametype)
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
    
    def add(self,exptime,filt='G',bins=1,frametype='normal',obj='',imgtype='SCIENCE',dbID='manual',glance=False):
        """Add an exposure to the queue"""
        self.exp_queue.append(ExposureSpec(exptime,filt.upper(),bins,frametype,obj,imgtype,dbID,glance))
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
