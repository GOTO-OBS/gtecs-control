#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                          mount_control.py                            #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#         G-TeCS module containing classes to control SiTechExe        #
#                     Martin Dyer, Sheffield, 2017                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
from __future__ import absolute_import
from __future__ import print_function
import os, sys, subprocess
import time
import serial
# TeCS modules
from gtecs.tecs_modules import params

########################################################################
# SiTech servo controller class using TCP/IP commands
class SiTech:
    def __init__(self, IP_address, port):
        self.IP_address = IP_address
        self.port = port
        self.buffer_size = 1024
        self.commands = {'GET_STATUS' : 'ReadScopeStatus\n',
                         'SLEW_RADEC' : 'GoTo {:.5f} {:.5f}\n',
                         'SLEW_ALTAZ' : 'GoToAltAz {:.5f} {:.5f}\n',
                         'SYNC_RADEC' : 'Sync {:.5f} {:.5f}\n',
                         'SYNC_ALTAZ' : 'SyncToAltAz {:.5f} {:.5f}\n',
                         'PARK' : 'Park\n',
                         'UNPARK' : 'UnPark\n',
                         'HALT' : 'Abort\n',
                         'SET_TRACKMODE' : 'SetTrackMode {:d} {:d} {:.5f} {:.5f}\n',
                         'PULSEGUIDE' : 'PulseGuide {:d} {:d}\n',
                         'BLINKY_ON' : 'MotorsToBlinky\n',
                         'BLINKY_OFF' : 'MotorsToAuto\n',
                         }

    def _tcp_command(self, command_str):
        '''Send a command string to the device, then fetch the reply
        and return it as a string.
        '''
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((self.IP_address, self.port))
            s.send(command_str.encode())
            reply = s.recv(self.buffer_size)
        return reply.decode()

    def _parse_reply_string(self, reply_string):
        '''Parse the return string from a SiTech command.

        The status  values are saved on on the SiTech object, and any attached
        message is returned.

        Returns None if there is no message,
        e.g. from just reading the status rather than sending a command.
        '''

        # split the parameters from the reply string
        reply = reply_string.split(';')

        # a quick check
        if not len(reply) == 11:
            raise ValueError('Invalid SiTech return string')

        # parse boolian flags
        bools = int(reply[0])
        self.initialized = (bools & 1) > 0
        self.tracking = (bools & 2) > 0
        self.slewing = (bools & 4) > 0
        self.parking = (bools & 8) > 0
        self.parked = (bools & 16) > 0
        self.direction = 'east' if (bools & 32) > 0 else 'west'
        self.blinky = (bools & 64) > 0
        self.connection_error = (bools & 128) > 0
        self.limit_switches = {'primary_plus' : (bools & 256) > 0,
                               'primary_minus' : (bools & 512) > 0,
                               'secondary_plus' : (bools & 1024) > 0,
                               'secondary_minus' : (bools & 2048) > 0,
                               }
        self.homing_switches = {'primary' : (bools & 4096) > 0,
                                'secondary' : (bools & 8192) > 0,
                                }

        # parse values
        self.ra = float(reply[1])
        self.dec = float(reply[2])
        self.alt = float(reply[3])
        self.az = float(reply[4])
        self.secondary_angle = float(reply[5])
        self.primary_angle = float(reply[6])
        self.sidereal_time = float(reply[7])
        self.jd = float(reply[8])
        self.hours = float(reply[9])

        # find the message and return it
        message = reply[10][1:-1] # strip leading '_' and trailing '\n'
        if len(message) == 0:
            return None
        else:
            return message

    def status(self):
        '''Read and store status values.

        Return a string for the current mount status.
        '''
        command = self.commands['GET_STATUS']
        reply_string = self._tcp_command(command)
        self._parse_reply_string(reply_string) # no message

        if self.tracking and not self.slewing:
            status = 'Tracking'
        elif self.slewing:
            status = 'Slewing'
        elif self.parking:
            status = 'Parking'
        elif self.parked:
            status = 'Parked'
        else:
            status = 'Stopped'

        return status

    def slew_to_radec(self, ra, dec):
        '''Slew to given RA and Dec coordinates
        NOTE: RA and Dec must be in JNow, not J2000
        '''
        self.target_radec = (ra, dec)

        command = self.commands['SLEW_RADEC'].format(float(ra), float(dec))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def slew_to_altaz(self, alt, az):
        '''Slew mount to given Alt/Az'''
        self.target_altaz = (alt, az)

        # NB SiTech takes Az first, then Alt
        command = self.commands['SLEW_ALTAZ'].format(float(az), float(alt))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def sync_radec(self, ra, dec):
        '''Set current pointing to given RA and Dec coordinates
        NOTE: RA and Dec must be in JNow, not J2000
        '''
        command = self.commands['SYNC_RADEC'].format(float(ra), float(dec))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def sync_altaz(self, alt, az):
        '''Set current pointing to given Alt/Az'''
        # NB SiTech takes Az first, then Alt
        command = self.commands['SYNC_ALTAZ'].format(float(az), float(alt))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def park(self):
        '''Move mount to park position'''
        command = self.commands['PARK']
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def unpark(self):
        '''Unpark the mount so it can accept slew commands'''
        command = self.commands['UNPARK']
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def halt(self):
        '''Abort slew (if slewing) and stop tracking (if tracking)'''
        command = self.commands['HALT']
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def set_trackrate(self, ra_rate, dec_rate):
        '''Set tracking rate in RA and Dec in arcseconds per second.
        If both RA and Dec are 0.0 then tracking will be (re)set
        to the siderial rate.
        '''
        if ra == 0 and dec == 0:
            command = self.commands['SET_TRACKMODE'].format(1, 0, 0, 0)
        else:
            command = self.commands['SET_TRACKMODE'].format(1, 1, float(ra), float(dec))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def blinky_mode(self, activate):
        '''Activate or deactivate "blinky" (manual) mode,
        cutting power to the motors
        '''
        if activate:
            command = self.commands['BLINKY_ON']
        else:
            command = self.commands['BLINKY_OFF']
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message
