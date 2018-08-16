"""Classes to control FLI cameras, filter wheels and focusers."""

from fliapi import FakeCamera, FakeFilterWheel, FakeFocuser
from fliapi import USBCamera as Camera
from fliapi import USBFilterWheel as FilterWheel
from fliapi import USBFocuser as Focuser

__all__ = [Camera, FilterWheel, Focuser, FakeCamera, FakeFilterWheel, FakeFocuser]
