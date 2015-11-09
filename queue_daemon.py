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
import os, sys, commands
from math import *
from string import split,find
import time
import Pyro4
import threading
import numpy
from collections import MutableSequence
# TeCS modules
import X_params as params
import X_misc as misc
import X_logger as logger

########################################################################
# Queue Daemon functions
class ExposureSpec:
    def __init__(self,exptime,filt,bins,frametype='normal',obj='',imgtype='SCIENCE',dbID='manual',glance=False):
        self.creation_time = time.gmtime()
        # Camera details
        self.exptime = exptime
        self.filt = filt
        self.bins = bins
        self.frametype = frametype
        # Image details
        self.obj = obj
        self.imgtype = imgtype
        self.dbID = dbID
        self.glance = glance
        self.filename = None
    
    @classmethod
    def from_line(cls,line):
        exptime,filt,bins,frametype,obj,imgtype,dbID,glance = line.split(',')
        glance = (glance == 'True') #Converts from string 'True'/'False' to boolean, safer than eval()
        exp = cls(float(exptime),filt,int(bins),frametype,obj,imgtype,dbID,glance)
        return exp
    
    def to_line(self):
        return '%i, %s, %i, %s, %s, %s, %s, %s\n' %(self.exptime,self.filt,self.bins,self.frametype,self.obj,self.imgtype,self.dbID,str(self.glance))
    
    def info(self):
        s = time.strftime('%Y-%m-%d %H:%M:%S UT',self.creation_time)+'\n'
        s += '  Exposure time: %i s\n' % self.exptime
        s += '  Filter: %s\n' % self.filt
        s += '  Bins: %i\n' % self.bins
        s += '  Frametype: %s\n' %self.frametype
        return s
    
class Queue(MutableSequence):
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
        self.data.insert(index,value)
        self.write_to_file()

    def clear(self):
        self.data=[]
        self.write_to_file()

    def get(self):
        n = len(self.data)
        s ='%i items in queue\n' %n
        for x in self.data:
            s += '\n' + x.info()
        return s

class Queue_Daemon:
    def __init__(self):
        
        ### activate
        self.running=True
        
        ### find current username
        self.username=os.environ["LOGNAME"]

        ### set up logfile
        self.logfile = logger.Logfile('queue',params.LOGGING)
        self.logfile.log('Daemon started')
        
        ### initiate flags
        self.get_info_flag = 0
        self.set_filter_flag = 0
        self.take_image_flag = 0

        ### queue
        self.exp_queue = Queue()
        self.exp_spec = 'None yet'
        self.current_ID = None
        self.working = 1
        self.current_filter = None
        self.abort = 0
        self.paused = 1
        
        ### status
        self.info='None yet'
        
        ### timing
        self.start_time=time.time()   #used for uptime
        
        ### start control thread
        t=threading.Thread(target=self.queue_thread)
        t.daemon = True
        t.start()
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Primary queue thread
    def queue_thread(self):
        while(self.running):
            print '~~~~~~~~'
            self.time_check = time.time()   #used for "ping"
            
            ### Connect to daemons
            CAM_DAEMON_ADDRESS = params.DAEMONS['cam']['ADDRESS']
            cam = Pyro4.Proxy(CAM_DAEMON_ADDRESS)
            
            FILT_DAEMON_ADDRESS = params.DAEMONS['filt']['ADDRESS']
            filt = Pyro4.Proxy(FILT_DAEMON_ADDRESS)

            ### Check daemon statuses
            try:
                cam_status = cam.get_info()['status']
            except:
                print 'No responce from camera daemon'
                self.running = False
                break
            try:
                filt_status = filt.get_info()['status']
            except:
                print 'No responce from filter wheel daemon'
                self.running = False
                break
            
            if cam_status == 'Exposing' or filt_status == 'Moving':
                self.working = 1
            else:
                self.working = 0
            print '   Cam: %s | Filt: %s | Working: %i' %(cam_status, filt_status,self.working)
            
            ### (If not paused) Check the queue, take off the first entry
            self.queue_len = len(self.exp_queue)
            print '   Exposures in queue: %i' %self.queue_len
            if (self.queue_len > 0) and not self.paused and self.current_ID == None:
                if self.current_ID == None and not self.working:
                    self.exp_spec = self.exp_queue.pop(0)
                    self.current_ID = self.exp_spec.dbID
                    print ' Taking exposure:',self.current_ID
            elif self.current_ID == None:
                print ' Queue is empty, or paused'
                self.current_ID = None
                time.sleep(0.5)
            
            ### Take the exposure
            if self.current_ID != None:
                # Set the filter
                if not self.working:
                    if self.exp_spec.filt != self.current_filter:
                        print '   Filter:'
                        self.set_filter_flag=1
                        self.working=1
                # Take the image
                if not self.working:
                    print '   Camera:'
                    self.take_image_flag=1
                    self.working=1
                    # That's all, clear the exposure
                    self.current_ID = None
            
            ### control functions
            if(self.get_info_flag): # Request info
                info = {}
                if self.paused:
                    info['status'] = 'Paused'
                elif self.working:
                    info['status'] = 'Taking image'
                else:
                    info['status'] = 'Ready'
                info['queue_length'] = self.queue_len
                if self.working and self.exp_spec != 'None yet':
                    info['current_exptime'] = self.exp_spec.exptime
                    info['current_filter'] = self.exp_spec.filt
                    info['current_bins'] = self.exp_spec.bins
                    info['current_frametype'] = self.exp_spec.frametype
                info['uptime'] = time.time()-self.start_time
                info['ping'] = time.time()-self.time_check
                self.info = info
                self.get_info_flag=0
            
            if(self.set_filter_flag):
                new_filt = self.exp_spec.filt
                try:
                    print '   Set filter to:', new_filt
                    filt.set_filter(new_filt)
                    self.current_filter = new_filt
                    self.working=1
                    self.set_filter_flag=0
                except:
                    print 'No response from filter wheel daemon'
            
            if(self.take_image_flag):
                bins = self.exp_spec.bins
                exptime = self.exp_spec.exptime
                frametype = self.exp_spec.frametype
                try:
                    print '   Set bin factor to:', bins
                    cam.set_binning(bins,bins) # Assumes symmetric for now
                    print '   Taking exposure of:', exptime
                    cam.take_image(exptime,frametype)
                    self.working=1
                    self.take_image_flag=0
                except:
                    print 'No responce from camera daemon'
            
            time.sleep(0.0001) # To save 100% CPU usage
            
        self.logfile.log('Queue thread stopped')
        return
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Queue functions
    def get_info(self):
        self.get_info_flag=1
        time.sleep(0.1)
        return self.info
    
    def add(self,exptime,filt='G',bins=1,frametype='normal',obj='',imgtype='SCIENCE',dbID='manual',glance=False):
        self.exp_queue.append(ExposureSpec(exptime,filt.upper(),bins,frametype,obj,imgtype,dbID,glance))

    def clear(self):
        self.exp_queue.clear()

    def get(self):
        return self.exp_queue.get()

    def pause(self):
        self.paused = 1
        return 'Queue paused'

    def resume(self):
        self.paused = 0
        return 'Queue resumed'
    
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Daemon pinger
    def ping(self):
        #print '  pinged'
        dt_control = abs(time.time()-self.time_check)
        if dt_control > params.DAEMONS['queue']['PINGLIFE']:
            return 'Last queue daemon control thread time check: %.1f seconds ago' % dt_control
        else:
            return 'ping'
    
    def prod(self):
        return
        
    #~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    # Status and shutdown
    def status_function(self):
        #print 'status query:', self.running
        return self.running
    
    def shutdown(self):
        self.running=False
        #print '  set status to', self.running


########################################################################
# Create Pyro control server 

pyro_daemon=Pyro4.Daemon(host=params.DAEMONS['queue']['HOST'], port=params.DAEMONS['queue']['PORT'])
queue_daemon=Queue_Daemon()

uri=pyro_daemon.register(queue_daemon,objectId = params.DAEMONS['queue']['PYROID'])

print 'Starting queue daemon, with Pyro URI:',uri

Pyro4.config.COMMTIMEOUT=5.
pyro_daemon.requestLoop(loopCondition=queue_daemon.status_function)
print 'Exiting queue daemon'
time.sleep(1.)
