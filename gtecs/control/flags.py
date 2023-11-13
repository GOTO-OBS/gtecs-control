"""Classes to read external flag files."""

import json
import os
import time

from astropy.time import Time

from . import params


def load_json(fname, attempts=3):
    """Attempt to load a JSON file, with multiple tries."""
    data = None
    attempts_remaining = attempts
    while attempts_remaining:
        try:
            with open(fname, 'r') as fh:
                data = json.load(fh)
            if data is None or len(data) == 0:
                raise ValueError('Empty file')
            return data
        except Exception as err:
            attempts_remaining -= 1
            if attempts_remaining > 0:
                time.sleep(0.001)
                continue
            else:
                raise IOError('Cannot read {} (contents: "{}")'.format(fname, data)) from err


class Conditions:
    """A class to give easy access to the conditions flags."""

    def __init__(self):
        self.conditions_file = os.path.join(params.FILE_PATH, 'conditions_flags.json')
        self._load()

    def __repr__(self):
        repr_str = ', '.join(['{}={}'.format(flag, self.conditions_dict[flag])
                              for flag in sorted(self.conditions_dict)])
        return 'Conditions({})'.format(repr_str)

    def _load(self):
        """Load the conditions file."""
        if not os.path.exists(self.conditions_file):
            # We can't create a default file, so raise an error
            raise IOError('Conditions file ({}) does not exist!'.format(self.conditions_file))
        try:
            # Read the conditions file
            self.data = load_json(self.conditions_file)
            self.update_times = {flag.replace('_update_time', ''): Time(self.data[flag])
                                 for flag in [k for k in self.data if k.endswith('_update_time')]}
            self.conditions_dict = {flag: self.data[flag] for flag in self.update_times}
            self.info_flags = self.data['info_flags']
            self.ignored_flags = self.data['ignored_flags']

            # Get update time and calculate age flag
            self.current_time = Time(self.data['current_time'])
            self.age = float(Time.now().unix - self.current_time.unix)
            self.conditions_dict['age'] = int(self.age > params.MAX_CONDITIONS_AGE)

            # Store the total of all flags, excluding info flags
            self.total = 0
            self.bad_flags = []
            for flag, status in self.conditions_dict.items():
                if flag not in self.info_flags and flag not in self.ignored_flags:
                    self.total += status
                    if status > 0:
                        self.bad_flags += [flag]
            self.bad = bool(self.total)
        except Exception:
            # We can't create a default file, so raise an error
            raise IOError('Conditions file ({}) is corrupted!'.format(self.conditions_file))

    def get_formatted_string(self, good='G', bad='B', ignored=None):
        """Get a formatted string of the conditions flags."""
        arr = []
        for flag in sorted(self.conditions_dict):
            if flag in self.info_flags:
                continue
            if flag in self.ignored_flags and ignored is not None:
                arr.append('{} {}'.format(flag, ignored))
            elif self.conditions_dict[flag] == 0:
                arr.append('{} {}'.format(flag, good))
            else:
                arr.append('{} {}'.format(flag, bad))
        return ' - '.join(arr)


class Status:
    """A class to give easy access to the status flags."""

    def __init__(self):
        self.status_file = os.path.join(params.FILE_PATH, 'status_flags.json')
        self.emergency_file = params.EMERGENCY_FILE
        self.valid_modes = ['robotic', 'manual', 'engineering']
        self._load()

    def __repr__(self):
        self._load()
        repr_str = "mode='{}', ".format(self._mode)
        repr_str += "observer='{}', ".format(self._observer)
        repr_str += 'emergency_shutdown={}'.format(self.emergency_shutdown)
        return 'Status({})'.format(repr_str)

    def _load(self):
        """Load the status flags file and emergency shutdown file."""
        data = None
        if not os.path.exists(self.status_file):
            self._mode = 'robotic'
            self._observer = params.ROBOTIC_OBSERVER
            with open(self.status_file, 'w') as f:
                json.dump(self._status_dict, f)
        try:
            # Read the status file
            data = load_json(self.status_file)
            if data['mode'].lower() not in self.valid_modes:
                raise ValueError('Invalid mode: "{}"'.format(data['mode']))
            self._mode = data['mode'].lower()
            self._observer = str(data['observer'])
        except Exception:
            # Rewrite the file ourselves with defaults
            print('Status file corrupted')
            if data is not None:
                print(data)
            self._mode = 'robotic'
            self._observer = params.ROBOTIC_OBSERVER
            with open(self.status_file, 'w') as f:
                json.dump(self._status_dict, f)

        # Check for the emergency shutdown file
        self.emergency_shutdown = os.path.isfile(self.emergency_file)
        if self.emergency_shutdown:
            # Get the modification time
            mod_time = os.path.getmtime(self.emergency_file)
            self.emergency_shutdown_time = Time(mod_time, format='unix', precision=0).iso

            # Read the emergency shutdown reasons
            with open(self.emergency_file, 'r') as f:
                reasons = f.readlines()
                if len(reasons):
                    self.emergency_shutdown_reasons = [r.strip() for r in reasons]
                else:
                    self.emergency_shutdown_reasons = ['unknown']
        else:
            self.emergency_shutdown_time = None
            self.emergency_shutdown_reasons = []

    def _update_flags(self, key, value):
        """Update the given status value."""
        with open(self.status_file, 'r') as f:
            data = json.load(f)
        if key not in data:
            raise KeyError(key)
        data[key] = value
        with open(self.status_file, 'w') as f:
            json.dump(data, f)
        self._load()

    @property
    def _status_dict(self):
        """Get the current system status values."""
        status_dict = {'mode': self._mode,
                       'observer': self._observer}
        return status_dict

    @property
    def mode(self):
        """Get the current system mode."""
        self._load()
        return self._mode

    @mode.setter
    def mode(self, value):
        """Set the current system mode."""
        mode = value.lower()
        if mode not in self.valid_modes:
            raise ValueError('Invalid mode: "{}"'.format(mode))
        self._update_flags('mode', mode)
        if mode == 'robotic':
            # Set pilot as the observer
            self._update_flags('observer', params.ROBOTIC_OBSERVER)

    @property
    def observer(self):
        """Get the current observer name."""
        self._load()
        return self._observer

    @observer.setter
    def observer(self, value):
        """Set the current observer name."""
        name = str(value)
        self._update_flags('observer', name)

    def create_shutdown_file(self, reasons=None):
        """Create the emergency shutdown file."""
        self._load()
        cmd = 'touch ' + self.emergency_file
        os.system(cmd)

        if isinstance(reasons, str):
            reasons = [reasons]
        if reasons is None:
            reasons = ['no reason given']
        for reason in reasons:
            if reason not in self.emergency_shutdown_reasons:
                with open(self.emergency_file, 'a') as f:
                    f.write(reason + '\n')
            self._load()


class ModeError(Exception):
    """To be raised if a command isn't possible due to the system mode.

    e.g. trying to enable certain dome functions when in engineering mode.
    """

    pass
