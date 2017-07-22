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
import socket
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

        # save reply string
        self._reply_string = reply_string

        # split the parameters from the reply string
        reply = reply_string.split(';')

        # a quick check
        if not len(reply) == 11:
            raise ValueError('Invalid SiTech return string')

        # parse boolian flags
        bools = int(reply[0])
        self._initialized = (bools & 1) > 0
        self._tracking = (bools & 2) > 0
        self._slewing = (bools & 4) > 0
        self._parking = (bools & 8) > 0
        self._parked = (bools & 16) > 0
        self._direction = 'east' if (bools & 32) > 0 else 'west'
        self._blinky = (bools & 64) > 0
        self._connection_error = (bools & 128) > 0
        self._limit_switches = {'primary_plus' : (bools & 256) > 0,
                               'primary_minus' : (bools & 512) > 0,
                               'secondary_plus' : (bools & 1024) > 0,
                               'secondary_minus' : (bools & 2048) > 0,
                               }
        self._homing_switches = {'primary' : (bools & 4096) > 0,
                                'secondary' : (bools & 8192) > 0,
                                }

        # parse values
        self._ra = float(reply[1])
        self._dec = float(reply[2])
        self._alt = float(reply[3])
        self._az = float(reply[4])
        self._secondary_angle = float(reply[5])
        self._primary_angle = float(reply[6])
        self._sidereal_time = float(reply[7])
        self._jd = float(reply[8])
        self._hours = float(reply[9])

        # find the message and return it
        message = reply[10][1:-1] # strip leading '_' and trailing '\n'
        if len(message) == 0:
            return None
        else:
            return message

    def _update_status(self):
        '''Read and store status values'''
        command = self.commands['GET_STATUS']
        reply_string = self._tcp_command(command)
        self._parse_reply_string(reply_string) # no message

    @property
    def status(self):
        self._update_status()
        if self._connection_error:
            status = 'CONNECTION ERROR'
        elif self._blinky:
            status = 'IN BLINKY MODE'
        elif self._slewing:
            status = 'Slewing'
        elif self._tracking:
            status = 'Tracking'
        elif self._parking:
            status = 'Parking'
        elif self._parked:
            status = 'Parked'
        else:
            status = 'Stopped'
        return status

    @property
    def tracking(self):
        self._update_status()
        return self._tracking

    @property
    def slewing(self):
        self._update_status()
        return self._slewing

    @property
    def parking(self):
        self._update_status()
        return self._parking

    @property
    def parked(self):
        self._update_status()
        return self._parked

    @property
    def direction(self):
        self._update_status()
        return self._direction

    @property
    def direction(self):
        self._update_status()
        return self._direction

    @property
    def blinky(self):
        self._update_status()
        return self._blinky

    @property
    def connection_error(self):
        self._update_status()
        return self._connection_error

    @property
    def limit_switches(self):
        self._update_status()
        return self._limit_switches

    @property
    def homing_switches(self):
        self._update_status()
        return self._homing_switches

    @property
    def ra(self):
        self._update_status()
        return self._ra

    @property
    def dec(self):
        self._update_status()
        return self._dec

    @property
    def alt(self):
        self._update_status()
        return self._alt

    @property
    def az(self):
        self._update_status()
        return self._az

    @property
    def secondary_angle(self):
        self._update_status()
        return self._secondary_angle

    @property
    def primary_angle(self):
        self._update_status()
        return self._primary_angle

    @property
    def sidereal_time(self):
        self._update_status()
        return self._sidereal_time

    @property
    def jd(self):
        self._update_status()
        return self._jd

    @property
    def hours(self):
        self._update_status()
        return self._hours

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

    def track(self):
        '''Start tracking at the siderial rate'''
        command = self.commands['SET_TRACKMODE'].format(1, 1, 0, 0)
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
        if ra_rate == 0 and dec_rate == 0:
            command = self.commands['SET_TRACKMODE'].format(1, 1, 0, 0)
        else:
            command = self.commands['SET_TRACKMODE'].format(1, 1, float(ra), float(dec))
        reply_string = self._tcp_command(command)
        message = self._parse_reply_string(reply_string)
        return message

    def set_blinky_mode(self, activate):
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
