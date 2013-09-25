# -*- Mode: Python; coding: utf-8; indent-tabs-mode: nil; tab-width: 4 -*-
#
# Ubuntu Keyboard Test Suite
# Copyright (C) 2013 Canonical
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

from ubuntu_keyboard.emulators import UbuntuKeyboardEmulatorBase
from ubuntu_keyboard.emulators.keypad import KeyPad

from time import sleep
import logging

from autopilot.input import Pointer, Touch
from autopilot.introspection import (
    get_proxy_object_for_existing_process,
    ProcessSearchError
)


logger = logging.getLogger(__name__)


class KeyPadNotLoaded(Exception):
    pass


class QQuickLoader(UbuntuKeyboardEmulatorBase):
    # This is a work around so that when we select_single a KeyPad we don't get
    # back a generic version.
    pass


class Keyboard(object):

    _action_to_label = {
        'SHIFT': 'shift',
        '\b': 'backspace',
        'ABC': 'symbols',
        '?123': 'symbols',
        ' ': 'space',
        '\n': 'return',
    }

    # mallit is a class attribute because get_proxy_object_for_existing_process
    # clears backends for proxy objects, this means that with:
    #   kb = Keyboard()
    #   kb2 = Keyboard()
    # The proxy objects in kb have had their _Backends cleared which means we
    # can no longer query them.
    try:
        maliit = get_proxy_object_for_existing_process(
            connection_name='org.maliit.server',
            emulator_base=UbuntuKeyboardEmulatorBase
        )
    except ProcessSearchError as e:
        e.args += (
            "Unable to find maliit-server dbus object. Has it been "
            "started with introspection enabled?",
        )
        raise

    def __init__(self, pointer=None):
        try:
            self.orientation = Keyboard.maliit.select_single(
                "OrientationHelper"
            )
            if self.orientation is None:
                raise RuntimeError(
                    "Unable to find the Orientation Helper, aborting."
                )
        except ValueError as e:
            e.args += (
                "More than one OrientationHelper object was found, aborting."
            )
            raise

        try:
            self.keyboard = Keyboard.maliit.select_single(
                "QQuickItem",
                objectName="ubuntuKeyboard"
            )
            if self.keyboard is None:
                raise RuntimeError(
                    "Unable to find the Ubuntu Keyboard object within the "
                    "maliit server."
                )
        except ValueError as e:
            e.args += (
                "There was more than one Keyboard object found, aborting.",
            )
            raise

        self._character_keypad = None
        self._symbol_keypad = None

        self._store_current_orientation()
        self._store_current_language_id()

        if pointer is None:
            self.pointer = Pointer(Touch.create())
        else:
            self.pointer = pointer

    def _keyboard_details_changed(self):
        return self._language_changed() or self._orientation_changed()

    @property
    def character_keypad(self):
        if self._character_keypad is None or self._keyboard_details_changed():
            self._character_keypad = self._get_keypad("character")
        return self._character_keypad

    @property
    def symbol_keypad(self):
        if (self._symbol_keypad is None
                or (self._language_changed() or self._orientation_changed())):
            self._symbol_keypad = self._get_keypad("symbol")

        return self._symbol_keypad

    def _get_keypad(self, name):
        """Attempt to retrieve KeyPad object of either 'character' or 'symbol'

        *name* must be either 'character' or 'symbol'.

        Raises KeyPadNotLoaded exception if none or more than one keypad is
        found.

        """
        objectName = "{name}KeyPadLoader".format(name=name)
        loader = Keyboard.maliit.select_single(
            QQuickLoader,
            objectName=objectName
        )
        keypad = loader.select_single(KeyPad)
        if keypad is None:
            raise KeyPadNotLoaded("{name} keypad is not currently loaded.")

        return keypad

    def dismiss(self):
        """Swipe the keyboard down to hide it.

        :raises: *AssertionError* if the state.wait_for fails meaning that the
         keyboard failed to hide.

        """
        if self.is_available():
            x, y, h, w = self.keyboard.globalRect
            x_pos = int(w / 2)
            # start_y: just inside the keyboard, must be a better way than +1px
            start_y = y + 1
            end_y = y + int(h / 2)
            self.pointer.drag(x_pos, start_y, x_pos, end_y)

            self.keyboard.state.wait_for("HIDDEN")

    def is_available(self):
        """Returns true if the keyboard is shown and ready to use."""
        return (self.keyboard.state == "SHOWN")

    @property
    def current_state(self):
        return self.keyboard.state

    @property
    def active_keypad(self):
        if self.character_keypad.enabled:
            return self.character_keypad
        elif self.symbol_keypad.enabled:
            return self.symbol_keypad
        else:
            raise RuntimeError("There are no currently active KeyPads.")

    # Much like is_available, but attempts to wait for the keyboard to be
    # ready.
    def wait_for_keyboard_ready(self, timeout=10):
        """Waits for *timeout* for the keyboard to be ready and returns
        true. Returns False if the keyboard fails to be considered ready within
        the alloted time.

        """
        try:
            self.keyboard.state.wait_for("SHOWN", timeout=timeout)
            self.keyboard.hideAnimationFinished.wait_for(
                False,
                timeout=timeout
            )
            return True
        except RuntimeError:
            return False

    def press_key(self, key):
        """Tap on the key with the internal pointer

        :params key: String containing the text of the key to tap.

        :raises: *RuntimeError* if the keyboard is not available and thus not
          ready to be used.
        :raises: *ValueError* if the supplied key cannot be found on any of
          the the current keyboards layouts.
        """
        if not self.is_available():
            raise RuntimeError("Keyboard is not on screen")

        key = self._translate_key(key)
        active_keypad = None

        try:
            if self.character_keypad.contains_key(key):
                self._show_character_keypad()
                active_keypad = self.character_keypad
            elif self.symbol_keypad.contains_key(key):
                self._show_symbol_keypad()
                active_keypad = self.symbol_keypad
        except KeyPadNotLoaded:
            pass

        if active_keypad is None:
            raise ValueError(
                "Key '%s' was not found on the keyboard." % key
            )

        active_keypad.press_key(key)

    def type(self, string, delay=0.1):
        """Type the string *string* with a delay of *delay* between each key
        press

        .. note:: The delay provides a minimum delay, it may take longer
        between each press as the keyboard shifts between states etc.

        Only 'normal' or single characters can be typed this way.

        :raises: *ValueError* if one of the the supplied keys cannot be
          found on any of the the current keyboards layouts.

        """
        for char in string:
            self.press_key(char)
            sleep(delay)

    def _orientation_changed(self):
        if self._stored_orientation != self.orientation.orientationAngle:
            self._store_current_orientation()
            return True
        else:
            return False

    def _language_changed(self):
        if self._stored_language_id != self.keyboard.layoutId:
            self._store_current_language_id()
            return True
        else:
            return False

    def _store_current_orientation(self):
        self._stored_orientation = self.orientation.orientationAngle

    def _store_current_language_id(self):
        self._stored_language_id = self.keyboard.layoutId

    def _show_character_keypad(self):
        """Brings the characters KeyPad to the forefront."""
        if not self.character_keypad.enabled:
            # If the character keypad isn't enabled than the symbol keypad must
            # be active
            self.symbol_keypad.press_key("symbols")
            self.character_keypad.enabled.wait_for(True)
            self.character_keypad.opacity.wait_for(1.0)

    def _show_symbol_keypad(self):
        """Brings the symbol KeyPad to the forefront."""
        if not self.symbol_keypad.enabled:
            # If the symbol keypad isn't enabled than the character keypad must
            # be active
            self.character_keypad.press_key("symbols")
            self.symbol_keypad.enabled.wait_for(True)
            self.symbol_keypad.opacity.wait_for(1.0)

    def _translate_key(self, label):
        """Get the label for a 'special key' (i.e. space) so that it can be
        addressed and clicked.

        """
        return Keyboard._action_to_label.get(label, label)