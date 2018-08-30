"""Custom exceptions."""


class DaemonConnectionError(Exception):
    """To be used when a command to a daemon fails.

    e.g. if the Daemon is not running or is not responding.
    """

    pass


class DaemonDependencyError(Exception):
    """To be used if a daemons's dependendecneis are not responding."""

    pass


class DaemonStatusError(Exception):
    """To be used if a daemon reports an error status.

    e.g. dependencies not responding, hardware not responding.
    """

    pass


class MultipleProcessError(Exception):
    """To be used if multiple instances of a daemon or script are detected."""

    pass


class InputError(Exception):
    """To be used if an input command or arguments aren't valid."""

    pass


class HardwareStatusError(Exception):
    """To be used if a command isn't possible due to the hardware status.

    e.g. trying to start an exposure when the cameras are already exposing.
    """

    pass


class HorizonError(Exception):
    """To be used if a slew command would bring the mount below the limit."""

    pass


class RecoveryError(Exception):
    """To be used if a hardware monitor is out of recovery commands."""

    pass
