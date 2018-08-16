#!/usr/bin/env python
"""Daemon to control the exposure queue."""

import datetime
import threading
import time

from gtecs import errors
from gtecs import misc
from gtecs import params
from gtecs.controls.exq_control import Exposure, ExposureQueue
from gtecs.daemons import HardwareDaemon, daemon_proxy


class ExqDaemon(HardwareDaemon):
    """Exposure queue hardware daemon class."""

    def __init__(self):
        super().__init__('exq')

        # exposure queue variables
        self.exp_queue = ExposureQueue()
        self.current_exposure = None

        self.working = 0
        self.paused = 1  # start paused

        # start control thread
        t = threading.Thread(target=self._control_thread)
        t.daemon = True
        t.start()

    # Primary control thread
    def _control_thread(self):
        self.log.info('Daemon control thread started')

        while(self.running):
            self.time_check = time.time()

            # check dependencies
            if (self.time_check - self.dependency_check_time) > 2:
                # Check the dependencies, will populate self.bad_dependencies
                self.check_dependencies()

                # React to self.bad_dependencies
                if len(self.bad_dependencies) > 0 and not self.dependency_error:
                    self.log.error('Dependencies {} not responding'.format(self.bad_dependencies))
                    self.dependency_error = True
                elif len(self.bad_dependencies) == 0 and self.dependency_error:
                    self.log.info('All dependencies responding again')
                    self.dependency_error = False
                self.dependency_check_time = time.time()

            if self.dependency_error:
                time.sleep(5)
                continue

            # exposure queue processes

            # check the queue, take off the first entry (if not paused)
            self.queue_len = len(self.exp_queue)
            if (self.queue_len > 0) and not self.paused and not self.working:
                # OK - time to add a new exposure
                self.current_exposure = self.exp_queue.pop(0)
                self.log.info('Taking exposure')
                self.working = 1

                # set the filter, if needed
                if self._need_to_change_filter():
                    try:
                        self._set_filter()
                    except Exception:
                        self.log.error('set_filter command failed')
                        self.log.debug('', exc_info=True)
                    # sleep briefly, to make sure the filter wheel has stopped
                    time.sleep(0.5)
                else:
                    self.log.info('No need to move filter wheel')

                # take the image
                try:
                    self._take_image()
                except Exception:
                    self.log.error('take_image command failed')
                    self.log.debug('', exc_info=True)

                # done!
                self.working = 0

            elif self.queue_len == 0 or self.paused:
                # either we are paused, or nothing in the queue
                time.sleep(1.0)

            time.sleep(params.DAEMON_SLEEP_TIME)  # To save 100% CPU usage

        self.log.info('Daemon control thread stopped')
        return

    # Exposure queue functions
    def get_info(self):
        """Return exposure queue status info."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # exq info is outside the loop
        info = {}
        if self.paused:
            info['status'] = 'Paused'
        elif self.working:
            info['status'] = 'Working'
        else:
            info['status'] = 'Ready'
        info['queue_length'] = self.queue_len
        if self.working and self.current_exposure is not None:
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
        """Return plain status dict, or None."""
        try:
            info = self.get_info()
        except Exception:
            return None
        return info

    def add(self, tel_list, exptime,
            filt=None, binning=1, frametype='normal',
            target='NA', imgtype='SCIENCE', glance=False):
        """Add an exposure to the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt and filt.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list %s' % str(params.FILTER_LIST))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError("Frame type must be in {}".format(params.FRAMETYPE_LIST))

        # Call the command
        exposure = Exposure(tel_list, exptime,
                            filt.upper() if filt else None,
                            binning, frametype,
                            target.replace(';', ''),
                            imgtype.replace(';', ''),
                            glance)
        self.exp_queue.append(exposure)
        if not glance:
            self.log.info('Added {:.0f}s {} exposure, now {:.0f} in queue'.format(
                          exptime, filt.upper() if filt else 'X', len(self.exp_queue)))
        else:
            self.log.info('Added {:.0f}s {} glance, now {:.0f} in queue'.format(
                          exptime, filt.upper() if filt else 'X', len(self.exp_queue)))

        # Format return string
        if not glance:
            s = 'Added {:.0f}s {} exposure,'.format(exptime, filt.upper() if filt else 'X')
        else:
            s = 'Added {:.0f}s {} glance,'.format(exptime, filt.upper() if filt else 'X')
        s += ' now {} items in queue'.format(len(self.exp_queue))
        if self.paused:
            s += ' [paused]'
        return s

    def add_multi(self, nexp, tel_list, exptime,
                  filt=None, binning=1, frametype='normal',
                  target='NA', imgtype='SCIENCE',
                  db_id=0):
        """Add multiple exposures to the queue as a set."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        for tel in tel_list:
            if tel not in params.TEL_DICT:
                raise ValueError('Unit telescope ID not in list {}'.format(sorted(params.TEL_DICT)))
        if int(exptime) < 0:
            raise ValueError('Exposure time must be > 0')
        if filt and filt.upper() not in params.FILTER_LIST:
            raise ValueError('Filter not in list %s' % str(params.FILTER_LIST))
        if int(binning) < 1 or (int(binning) - binning) != 0:
            raise ValueError('Binning factor must be a positive integer')
        if frametype not in params.FRAMETYPE_LIST:
            raise ValueError("Frame type must be in {}".format(params.FRAMETYPE_LIST))

        # Call the command
        for i in range(nexp):
            set_pos = i + 1
            set_total = nexp
            exposure = Exposure(tel_list, exptime,
                                filt.upper() if filt else None,
                                binning, frametype,
                                target.replace(';', ''),
                                imgtype.replace(';', ''), False,
                                set_pos, set_total, db_id)
            self.exp_queue.append(exposure)
            self.log.info('Added {:.0f}s {} exposure, now {:.0f} in queue'.format(
                          exptime, filt.upper() if filt else 'X', len(self.exp_queue)))

        # Format return string
        s = 'Added {}x {:.0f}s {} exposure(s),'.format(nexp, exptime,
                                                       filt.upper() if filt else 'X')
        s += ' now {} items in queue'.format(len(self.exp_queue))
        if self.paused:
            s += ' [paused]'
        return s

    def clear(self):
        """Empty the exposure queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Call the command
        num_in_queue = len(self.exp_queue)
        self.exp_queue.clear()

        self.log.info('Cleared {} items from queue'.format(num_in_queue))
        return 'Queue cleared'

    def get(self):
        """Return info on exposures in the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Call the command
        queue_info = self.exp_queue.get()

        return queue_info

    def get_simple(self):
        """Return simple info on exposures in the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Call the command
        queue_info_simple = self.exp_queue.get_simple()

        return queue_info_simple

    def pause(self):
        """Pause the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if self.paused:
            return 'Queue already paused'

        # Set values
        self.paused = 1

        self.log.info('Queue paused')
        return 'Queue paused'

    def resume(self):
        """Unpause the queue."""
        # Check restrictions
        if self.dependency_error:
            raise errors.DaemonStatusError('Dependencies are not running')

        # Check input
        if not self.paused:
            return 'Queue already resumed'

        # Set values
        self.paused = 0

        self.log.info('Queue resumed')
        return 'Queue resumed'

    # Internal functions
    def _need_to_change_filter(self):
        new_filt = self.current_exposure.filt
        if new_filt is None:
            # filter doesn't matter, e.g. dark
            return False

        tel_list = self.current_exposure.tel_list
        with daemon_proxy('filt') as filt_daemon:
            filt_info = filt_daemon.get_info()
        check = [params.FILTER_LIST[filt_info['current_filter_num' + str(tel)]] == new_filt
                 for tel in tel_list]
        if all(check):
            return False
        else:
            return True

    def _set_filter(self):
        new_filt = self.current_exposure.filt
        tel_list = self.current_exposure.tel_list
        self.log.info('Setting filter to {} on {!r}'.format(new_filt, tel_list))
        try:
            with daemon_proxy('filt') as filt_daemon:
                filt_daemon.set_filter(new_filt, tel_list)
        except Exception:
            self.log.error('No response from filter wheel daemon')
            self.log.debug('', exc_info=True)

        time.sleep(3)
        with daemon_proxy('filt') as filt_daemon:
            filt_info = filt_daemon.get_info()
        check = [params.FILTER_LIST[filt_info['current_filter_num' + str(tel)]] == new_filt
                 for tel in tel_list]
        while not all(check):
            with daemon_proxy('filt') as filt_daemon:
                filt_info = filt_daemon.get_info()
            check = [params.FILTER_LIST[filt_info['current_filter_num' + str(tel)]] == new_filt
                     for tel in tel_list]
            time.sleep(0.5)

            # keep ping alive
            self.time_check = time.time()
        self.log.info('Filter wheel move complete, now at {}'.format(new_filt))

    def _take_image(self):
        exptime = self.current_exposure.exptime
        binning = self.current_exposure.binning
        frametype = self.current_exposure.frametype
        tel_list = self.current_exposure.tel_list
        glance = self.current_exposure.glance
        if not glance:
            self.log.info('Taking exposure ({:.0f}s, {:.0f}x{:.0f}, {}) on {!r}'.format(
                          exptime, binning, binning, frametype, tel_list))
        else:
            self.log.info('Taking glance ({:.0f}s, {:.0f}x{:.0f}, {}) on {!r}'.format(
                          exptime, binning, binning, frametype, tel_list))
        try:
            with daemon_proxy('cam') as cam_daemon:
                cam_daemon.take_exposure(self.current_exposure)
        except Exception:
            self.log.error('No response from camera daemon')
            self.log.debug('', exc_info=True)

        time.sleep(2)

        with daemon_proxy('cam') as cam_daemon:
            cam_exposing = cam_daemon.is_exposing()
        while cam_exposing:
            with daemon_proxy('cam') as cam_daemon:
                cam_exposing = cam_daemon.is_exposing()

            time.sleep(0.05)
            # keep ping alive
            self.time_check = time.time()
        self.log.info('Camera exposure complete')


if __name__ == "__main__":
    daemon_id = 'exq'
    with misc.make_pid_file(daemon_id):
        ExqDaemon()._run()
