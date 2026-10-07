# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What widgets share: connecting a signal without keeping the widget alive.

    connect_weak(obj, signal, self._method)   # the handler id

A widget that can be dropped (a pushed page, a dialog, a row) never connects a child's or an
owned object's signal to its own bound method directly: the closure holds the widget, the
widget holds the child, and the cycle runs through C, so the widget is never freed.
connect_weak holds the method's object through a GObject weak reference (not weakref:
PyGObject may drop a widget's Python wrapper while the widget lives and make a new one) and
disconnects itself once the object is gone.
"""


def connect_weak(obj, signal, method):
    ref = method.__self__.weak_ref()
    function = method.__func__
    handler = None

    def call(emitter, *args):
        nonlocal handler
        instance = ref()
        if instance is None:
            if handler is not None and emitter.handler_is_connected(handler):
                emitter.disconnect(handler)
            return None
        return function(instance, emitter, *args)

    handler = obj.connect(signal, call)
    return handler
