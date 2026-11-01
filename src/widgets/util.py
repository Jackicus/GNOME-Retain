# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""What widgets share: connecting a signal without keeping the widget alive, and the picture
types a file chooser offers.

    connect_weak(obj, signal, self._method)   # the handler id
    add_picture_types(file_filter)            # the types a card can show

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


# The picture types both a card (WebKit, and Anki, which shares the .apkg) and the editor
# (Gdk.Texture) show. Gtk.FileFilter.add_pixbuf_formats (deprecated in GTK 4.20) offered
# every gdk-pixbuf loader's type, camera raw and HEIC among them, which no card displays.
PICTURE_TYPES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml',
                 'image/avif', 'image/bmp')


def add_picture_types(file_filter):
    for mime_type in PICTURE_TYPES:
        file_filter.add_mime_type(mime_type)
    return file_filter
