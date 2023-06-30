#!/usr/bin/env python3
"""Interface to access camera hardware remotely."""

import argparse
import multiprocessing as mp
import os
import threading
import time

from astropy.time import Time

from gtecs.common.system import make_pid_file
from gtecs.control import params
from gtecs.control.daemons import BaseDaemon, get_daemon_host
from gtecs.control.fits import glance_location, image_location, make_fits, save_fits
from gtecs.control.hardware.fli import FLICamera, FakeCamera


class CameraInterfaceDaemon(BaseDaemon):
    """Camera interface daemon class."""

    def __init__(self, ut):
        super().__init__(f'cam{ut}')

        # params
        self.ut = ut
        self.params = params.UT_DICT[ut]['CAMERA']

        # hardware
        self.camera = None

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        """Primary control loop."""
        self.log.info('Daemon control thread started')
        self.check_period = params.DAEMON_CHECK_PERIOD
        self.check_time = 0
        self.force_check_flag = True

        while self.running:
            self.loop_time = time.time()

            # system check
            if self.force_check_flag or (self.loop_time - self.check_time) > self.check_period:
                self.check_time = self.loop_time
                self.force_check_flag = False

                # Try to connect to the hardware
                self._connect()

                # If there is an error then the connection failed.
                # Keep looping, it should retry the connection until it's successful
                if self.hardware_error:
                    continue

                # We should be connected, now try getting info
                self._get_info()

                # If there is an error then getting info failed.
                # Restart the loop to try reconnecting above.
                if self.hardware_error:
                    continue

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')

    # Internal functions
    def _connect(self):
        """Connect to hardware."""
        # Connect to camera
        if self.camera is None:
            if 'camera' not in self.bad_hardware:
                self.log.info('Connecting to Camera')
            try:
                if 'CLASS' not in self.params:
                    raise ValueError('Missing class')

                # Connect to appropriate hardware class
                if self.params['CLASS'] == 'FLI':
                    # FLI USB Camera, needs a serial number
                    if 'SERIAL' not in self.params:
                        raise ValueError('Missing serial number')
                    camera = FLICamera.locate_device(self.params['SERIAL'])
                    if camera is None and params.FAKE_INTF:
                        self.log.info('Creating a fake Camera')
                        camera = FakeCamera('/dev/fake', 'FakeCamera')
                        camera.serial_number = self.params['SERIAL']
                        camera.connected = True
                    if camera is None:
                        raise ValueError('Could not locate hardware')

                else:
                    raise ValueError('Unknown class: {}'.format(self.params['CLASS']))

                if not camera.connected:
                    raise ValueError('Could not connect to hardware')

                self.log.info('Connected to {}'.format(camera.serial_number))
                self.camera = camera
                if 'camera' in self.bad_hardware:
                    self.bad_hardware.remove('camera')

            except Exception:
                self.camera = None
                self.log.debug('', exc_info=True)
                if 'camera' not in self.bad_hardware:
                    self.log.error('Failed to connect to hardware')
                    self.bad_hardware.add('camera')

        # Finally check if we need to report an error
        self._check_errors()

    def _get_info(self):
        """Get the latest status info from the hardware."""
        temp_info = {}

        # Get basic daemon info
        temp_info['daemon_id'] = self.daemon_id
        temp_info['time'] = self.loop_time
        temp_info['timestamp'] = Time(self.loop_time, format='unix', precision=0).iso
        temp_info['uptime'] = self.loop_time - self.start_time

        temp_info['ut'] = self.ut

        # Get Camera info
        temp_info['params'] = self.params
        try:
            if not self.camera.connected:
                raise ValueError('Camera not connected')
            temp_info['serial'] = self.camera.serial_number
        except Exception:
            self.log.error('Failed to get Camera info')
            self.log.debug('', exc_info=True)
            temp_info['serial'] = None
            # Report the connection as failed
            self.camera = None
            if 'camera' not in self.bad_hardware:
                self.bad_hardware.add('camera')

        # Write debug log line
        # NONE, nothing really changes

        # Update the master info dict
        self.info = temp_info

        # Finally check if we need to report an error
        self._check_errors()

    # Control functions
    def set_exposure(self, exptime_ms, frametype):
        """Set exposure time and frametype."""
        self.log.info('Setting {:.3f}s {} exposure'.format(exptime_ms / 1000, frametype))
        self.camera.set_exposure(exptime_ms, frametype)

    def start_exposure(self):
        """Begin exposure."""
        self.log.info('Starting exposure')
        self.camera.start_exposure()

    def exposure_ready(self):
        """Check if an exposure is ready."""
        return self.camera.image_ready

    def fetch_exposure(self):
        """Fetch the image."""
        self.log.info('Fetching image')
        return self.camera.fetch_image()

    def _write_fits(self, hdu):
        """Write image HDU to a FITS file."""
        if not hdu.header['GLANCE']:
            filename = image_location(hdu.header['RUN'], self.ut, hdu.header['TEL'])
        else:
            filename = glance_location(self.ut, hdu.header['TEL'])

        self.log.info('Saving image to {}'.format(filename))
        save_fits(hdu, filename, log=self.log, log_debug=True, fancy_log=False)

        # Check that the file was created
        exists = os.path.isfile(filename)
        if exists:
            self.log.info('Image saved')
        else:
            self.log.warning('ERROR: Image failed to save')

    def save_exposure(self, header_cards=None, compress=False, measure_hfds=False, method='proc'):
        """Fetch the image data and save to a FITS file."""
        image_data = self.fetch_exposure()
        if image_data is None:
            self.log.error('ERROR: Failed to write image (nothing returned)')
            return None

        hdu = make_fits(image_data,
                        header_cards=header_cards,
                        compress=compress,
                        measure_hfds=measure_hfds,
                        log=self.log)

        if method == 'proc':
            # Start image saving in a new process
            p = mp.Process(target=self._write_fits, args=[hdu])
            p.start()
            p.join()  # Note this means we're not actually running in parallel
            self.log.info('Saving complete')
        elif method == 'thread':
            # Start image saving in a new thread
            t = threading.Thread(target=self._write_fits, args=[hdu])
            t.daemon = True
            t.start()
            # self.log.info(f'Saving complete')  # No log, we return before it finishes
        else:
            # Just save directly here
            self._write_fits(hdu)
            self.log.info('Saving complete')

        # return the image header
        return hdu.header

    def abort_exposure(self):
        """Abort current exposure."""
        self.log.info('Aborting exposure')
        self.camera.cancel_exposure()

    def clear_exposure_queue(self):
        """Clear exposure queue."""
        n = self.get_queue_length()
        self.log.info('Clearing {} images from exposure queue'.format(n))
        self.camera.image_queue.clear()

    def set_temp(self, target_temp):
        """Set the cooler temperature."""
        self.log.info('Setting temperature to {}'.format(target_temp))
        self.camera.set_temperature(target_temp)

    def set_flushes(self, target_flushes):
        """Set the number of times to flush the CCD before an exposure."""
        self.log.info('Setting flushes to {}'.format(target_flushes))
        self.camera.set_flushes(target_flushes)

    def set_binning(self, hbin, vbin):
        """Set the image binning."""
        self.log.info('Setting binning factor to ({},{})'.format(hbin, vbin))
        self.camera.set_image_binning(hbin, vbin)

    def set_window(self, x, y, dx, dy):
        """Set the image window area in unbinned pixels."""
        areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
        self.log.info('Setting image window to {}'.format(areastr))
        self.camera.set_image_size(x, y, dx, dy)

    def set_window_active(self):
        """Set the image window to the active area (excluding overscan)."""
        x, y, dx, dy = self.get_active_area()
        areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
        self.log.info('Setting image window to active {}'.format(areastr))
        self.camera.set_image_size(x, y, dx, dy)

    def set_window_full(self):
        """Set the image window to the full frame (including overscan)."""
        x, y, dx, dy = self.get_full_area()
        areastr = '({:.0f},{:.0f},{:.0f},{:.0f})'.format(x, y, dx, dy)
        self.log.info('Setting image window to full {}'.format(areastr))
        self.camera.set_image_size(x, y, dx, dy)

    def get_camera_info(self):  # Can't be get_info as that's a BaseDaemon function
        """Return information dictionary."""
        return self.camera.get_info()

    def get_camera_status(self):  # Can't be get_status as that's a BaseDaemon function
        """Return camera status string."""
        return self.camera.state

    def get_data_state(self):
        """Return True if data is available."""
        return self.camera.dataAvailable

    def get_queue_length(self):
        """Get the number of images in the image queue."""
        return len(self.camera.image_queue)

    def get_time_remaining(self):
        """Return exposure time remaining."""
        return self.camera.get_exposure_timeleft() / 1000.

    def get_temp(self, temp_type):
        """Return camera CCD/base temperature."""
        return self.camera.get_temperature(temp_type)

    def get_cooler_power(self):
        """Return Peltier cooler power."""
        return self.camera.get_cooler_power()

    def get_image_size(self):
        """Return the image size in binned pixels."""
        return self.camera.get_image_size()

    def get_window(self):
        """Return the current image area in unbinned pixels."""
        info = self.camera.get_info()['readout_pars']
        x = info['xstart']
        y = info['ystart']
        dx = info['nx'] * info['xbin']
        dy = info['ny'] * info['ybin']
        return (x, y, dx, dy)

    def get_active_area(self):
        """Return the active image area (excluding overscan) in unbinned pixels."""
        info = self.camera.get_info()['active_area']
        x = info[0]
        y = info[1]
        dx = info[2] - info[0]
        dy = info[3] - info[1]
        return (x, y, dx, dy)

    def get_full_area(self):
        """Return the full frame image area (including overscan) in unbinned pixels."""
        info = self.camera.get_info()['array_area']
        x = info[0]
        y = info[1]
        dx = info[2] - info[0]
        dy = info[3] - info[1]
        return (x, y, dx, dy)

    def get_serial_number(self):
        """Return camera unique serial number."""
        return self.camera.serial_number

    def get_class(self):
        """Return camera hardware class."""
        return self.params['CLASS']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('ut', type=int)
    args = parser.parse_args()

    ut = args.ut
    daemon = CameraInterfaceDaemon(ut)
    with make_pid_file(daemon.daemon_id):
        host, port = get_daemon_host(daemon.daemon_id)
        daemon._run(host, port, timeout=params.PYRO_TIMEOUT)
