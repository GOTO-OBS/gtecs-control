#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#
#                           power_control.py                           #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#       G-TeCS module containing class to control power switches       #
#                     Martin Dyer, Sheffield, 2015                     #
#           ~~~~~~~~~~~~~~~~~~~~~~~##~~~~~~~~~~~~~~~~~~~~~~~           #
#                   Based on the SLODAR/pt5m system                    #
#oooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooooo#

### Import ###
# Python modules
import os, sys, commands
import socket
from pysnmp.entity.rfc3413.oneliner import cmdgen
from pysnmp.proto import rfc1902

########################################################################
# Fake APC PDU power class (8 ports)
class FakePower:
    def __init__(self,IP_address,port):
        self.IP_address = IP_address
        self.port = port
        # depends on hardware
        self.base_oid_ind = [1,3,6,1,4,1,318,1,1,12,3,3,1,1,4]
        self.base_oid_all = [1,3,6,1,4,1,318,1,1,12,3,1,1]
        self.commands = {'IND_ON':1, 'IND_OFF':2, 'IND_REBOOT':3, 'ALL_ON':2, 'ALL_OFF':3, 'ALL_REBOOT':4}
        # fake stuff
        self.temp_file = '/tmp/power'
        self._read_temp()
    
    def _new_temp(self):
        self.outlet_status = [2,2,2,2,2,2,2,2] # all start off
        self._write_temp()
    
    def _read_temp(self):
        if not os.path.exists(self.temp_file):
            self._new_temp()
        else:
            f = open(self.temp_file,'r')
            self.outlet_status = list(f.read().strip())
            f.close()
    
    def _write_temp(self):
        f = open(self.temp_file,'w')
        f.write(''.join(str(i) for i in self.outlet_status))
        f.close()
        
    def snmp_get(self,oid):
        outlet = oid[-1]
        return self.outlet_status[outlet]
    
    def snmp_set(self,oid,value):
        if oid == self.base_oid_all:
            if value == self.commands['ALL_ON'] or value == self.commands['ALL_REBOOT']:
                for i in range(8): self.outlet_status[i] = 1
            elif value == self.commands['ALL_OFF']:
                 for i in range(8): self.outlet_status[i] = 2
        elif oid[:-1] == self.base_oid_ind:
            outlet = oid[-1] -1
            if value == self.commands['IND_ON'] or value == self.commands['IND_REBOOT']:
                self.outlet_status[outlet] = 1
            elif value == self.commands['IND_OFF']:
                self.outlet_status[outlet] = 2
        self._write_temp()
    
    def status(self,outlet):
        if outlet == 0: # all
            status = ''
            for i in range(8):
                oid = self.base_oid_ind + [i]
                status += str(self.snmp_get(oid))
            return status
        else:
            oid = self.base_oid_ind + [outlet -1]
            return self.snmp_get(oid)
    
    def on(self,outlet):
        if outlet == 0: # all
            oid = self.base_oid_all
            value = self.commands['ALL_ON']
            return self.snmp_set(oid,value)
        else:
            oid = self.base_oid_ind + [outlet]
            value = self.commands['IND_ON']
            return self.snmp_set(oid,value)
    
    def off(self,outlet):
        if outlet == 0: # all
            oid = self.base_oid_all
            value = self.commands['ALL_OFF']
            return self.snmp_set(oid,value)
        else:
            oid = self.base_oid_ind + [outlet]
            value = self.commands['IND_OFF']
            return self.snmp_set(oid,value)
    
    def reboot(self,outlet):
        if outlet == 0: # all
            oid = self.base_oid_all
            value = self.commands['ALL_REBOOT']
            return self.snmp_set(oid,value)
        else:
            oid = self.base_oid_ind + [outlet]
            value = self.commands['IND_REBOOT']
            return self.snmp_set(oid,value)




########################################################################
# APC PDU power class (for AP7921, 8 ports)
# Based on APC control script written by Henry Roe
# http://henrysmac.org/blog/2012/2/16/controlling-an-apc-pdu-from-python-via-pysnmp.html

class PDUError(Exception):
    None

class APCPower:
    def __init__(self,IP_address,port):
        self.IP_address = IP_address
        self.port = port
        # depends on hardware
        self.base_oid_ind = [1,3,6,1,4,1,318,1,1,12,3,3,1,1,4]
        self.base_oid_all = [1,3,6,1,4,1,318,1,1,12,3,1,1]
        self.commands = {'IND_ON':1, 'IND_OFF':2, 'IND_REBOOT':3, 'ALL_ON':2, 'ALL_OFF':3, 'ALL_REBOOT':4}
    
    def snmp_get(self,oid):
        target = (self.IP_address,self.port)
        errorIndication,errorStatus,errorIndex,varBinds = cmdgen.CommandGenerator().getCmd(
            cmdgen.CommunityData('test-agent', 'public'),
            cmdgen.UdpTransportTarget(target),
            oid,
            (('SNMPv2-MIB', 'sysObjectID'), 0)
            )
        if errorIndication:
            raise PDUError(errorIndication)
        else:
            if errorStatus:
                raise PDUError('%s at %s\n'\
                    %(errorStatus.prettyPrint(),
                      errorIndex and varBinds[int(errorIndex)-1] or '?'))
            else:
                for name, val in varBinds:
                    if name == oid:
                        return str(val).split()
    
    def snmp_set(self,oid,value):
        target = (self.IP_address,self.port)
        errorIndication,errorStatus,errorIndex,varBinds = cmdgen.CommandGenerator().setCmd(
            cmdgen.CommunityData('private', 'private', 1),
            cmdgen.UdpTransportTarget(target),
            (oid, rfc1902.Integer(str(val)))
            )
        if errorIndication:
            raise PDUError(errorIndication)
        else:
            if errorStatus:
                raise PDUError('%s at %s\n'\
                    %(errorStatus.prettyPrint(),
                      errorIndex and varBinds[int(errorIndex)-1] or '?'))
            else:
                for name, val in varBinds:
                    if name == oid:
                        return str(val).split()
    
    def status(self,outlet):
        if outlet == 0: # all
            status = ''
            for i in range(8):
                oid = self.base_oid_ind + [i+1]
                status += str(self.snmp_get(oid))
            return status
        else:
            oid = self.base_oid_ind + [outlet]
            return self.snmp_get(oid)

    def on(self,outlet):
        if outlet == 0: # all
            oid = self.base_oid_all
            value = self.commands['ALL_ON']
            return self.snmp_set(oid,value)
        else:
            oid = self.base_oid_ind + [outlet]
            value = self.commands['IND_ON']
            return self.snmp_set(oid,value)
    
    def off(self,outlet):
        if outlet == 0: # all
            oid = self.base_oid_all
            value = self.commands['ALL_OFF']
            return self.snmp_set(oid,value)
        else:
            oid = self.base_oid_ind + [outlet]
            value = self.commands['IND_OFF']
            return self.snmp_set(oid,value)
    
    def reboot(self,outlet):
        if outlet == 0: # all
            oid = self.base_oid_all
            value = self.commands['ALL_REBOOT']
            return self.snmp_set(oid,value)
        else:
            oid = self.base_oid_ind + [outlet]
            value = self.commands['IND_REBOOT']
            return self.snmp_set(oid,value)
























