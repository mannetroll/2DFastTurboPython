"""Read an application's redirected X11 pixmap for explicit validation captures.

QScreen.grabWindow reads the root screen, which Xwayland exposes as black on
this Wayland compositor. XComposite names the application's actual backing pixmap.
"""
import ctypes as C
import ctypes.util
from PySide6.QtGui import QImage


def capture(window_id):
    x=C.CDLL(C.util.find_library('X11'))
    composite=C.CDLL(C.util.find_library('Xcomposite'))
    x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p
    x.XCloseDisplay.argtypes=[C.c_void_p]
    x.XFreePixmap.argtypes=[C.c_void_p,C.c_ulong]
    composite.XCompositeNameWindowPixmap.argtypes=[C.c_void_p,C.c_ulong]
    composite.XCompositeNameWindowPixmap.restype=C.c_ulong
    x.XGetGeometry.argtypes=[C.c_void_p,C.c_ulong,C.POINTER(C.c_ulong),C.POINTER(C.c_int),C.POINTER(C.c_int),
        C.POINTER(C.c_uint),C.POINTER(C.c_uint),C.POINTER(C.c_uint),C.POINTER(C.c_uint)]
    class Image(C.Structure):
        _fields_=[('width',C.c_int),('height',C.c_int),('xoffset',C.c_int),('format',C.c_int),('data',C.c_void_p),
            ('byte_order',C.c_int),('bitmap_unit',C.c_int),('bitmap_bit_order',C.c_int),('bitmap_pad',C.c_int),
            ('depth',C.c_int),('bytes_per_line',C.c_int),('bits_per_pixel',C.c_int)]
    x.XGetImage.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_ulong,C.c_int]
    x.XGetImage.restype=C.POINTER(Image)
    x.XDestroyImage.argtypes=[C.POINTER(Image)]
    display=x.XOpenDisplay(None)
    if not display:raise RuntimeError('No X11 display for window capture')
    pixmap=0;image=None
    try:
        pixmap=composite.XCompositeNameWindowPixmap(display,int(window_id))
        root,left,top=C.c_ulong(),C.c_int(),C.c_int()
        width,height,border,depth=C.c_uint(),C.c_uint(),C.c_uint(),C.c_uint()
        assert x.XGetGeometry(display,pixmap,C.byref(root),C.byref(left),C.byref(top),C.byref(width),C.byref(height),C.byref(border),C.byref(depth))
        image=x.XGetImage(display,pixmap,0,0,width,height,C.c_ulong(-1),2)
        assert image and image.contents.bits_per_pixel==32 and image.contents.byte_order==0
        info=image.contents
        return QImage(C.string_at(info.data,info.bytes_per_line*info.height),info.width,info.height,
                      info.bytes_per_line,QImage.Format.Format_RGB32).copy()
    finally:
        if image:x.XDestroyImage(image)
        if pixmap:x.XFreePixmap(display,pixmap)
        x.XCloseDisplay(display)
