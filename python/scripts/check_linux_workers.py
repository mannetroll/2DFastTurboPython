"""Verify native Linux workers, visible GPU output and canvas transport events."""
import argparse
import ctypes as C
import ctypes.util
import json
from pathlib import Path
import time
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog
from fastturbo.app import MainWindow, create_application


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('evidence/linux-native/workers.json'))
    args=parser.parse_args()
    app=create_application()
    window=MainWindow(config={'resolution':128,'k0':4},restore=False)
    window.show()
    def wait(predicate):
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            app.processEvents()
            state=window.native.call()
            assert not state['error_message'],state['error_message']
            if predicate(state):return state
            QTest.qWait(10)
        raise AssertionError('Native worker did not reach requested state')
    try:
        wait(lambda s:s['ready'] and s['presentations']>3 and s['stats']['steps']>0)
        before=window.native.call()
        time.sleep(.6)  # No Python/native control calls and no Qt event processing.
        after=window.native.call()
        assert after['stats']['steps']>before['stats']['steps']
        assert after['presentations']>before['presentations']+5
        assert len(json.dumps(after))<20000
        args.output.parent.mkdir(parents=True,exist_ok=True)
        from capture_linux_window import capture as capture_window
        whole=capture_window(window.winId())
        origin=window.canvas.mapTo(window,window.canvas.rect().topLeft())
        capture=whole.copy(origin.x(),origin.y(),window.canvas.width(),window.canvas.height())
        assert not capture.isNull(),'Cannot capture the visible native X11 canvas'
        capture.save(str(args.output.with_suffix('.png')))
        pixels=capture
        colors={pixels.pixelColor(x,y).rgb() for y in range(60,pixels.height()-60,11)
                for x in range(60,pixels.width()-60,11)}
        assert len(colors)>100,f'Visible canvas has only {len(colors)} colors'
        # Send actual X11 button-release events to the renderer-owned child.
        xlib=C.CDLL(C.util.find_library('X11'))
        xlib.XOpenDisplay.argtypes=[C.c_char_p];xlib.XOpenDisplay.restype=C.c_void_p
        xlib.XQueryTree.argtypes=[C.c_void_p,C.c_ulong,C.POINTER(C.c_ulong),C.POINTER(C.c_ulong),C.POINTER(C.POINTER(C.c_ulong)),C.POINTER(C.c_uint)]
        xlib.XFree.argtypes=[C.c_void_p];xlib.XFlush.argtypes=[C.c_void_p];xlib.XCloseDisplay.argtypes=[C.c_void_p]
        class Button(C.Structure):
            _fields_=[('type',C.c_int),('serial',C.c_ulong),('send_event',C.c_int),('display',C.c_void_p),
                ('window',C.c_ulong),('root',C.c_ulong),('subwindow',C.c_ulong),('time',C.c_ulong),
                ('x',C.c_int),('y',C.c_int),('x_root',C.c_int),('y_root',C.c_int),('state',C.c_uint),('button',C.c_uint),('same_screen',C.c_int)]
        class Event(C.Union):
            _fields_=[('button',Button),('padding',C.c_long*24)]
        xlib.XSendEvent.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_long,C.POINTER(Event)]
        display=xlib.XOpenDisplay(None)
        assert display
        try:
            root,parent,count=C.c_ulong(),C.c_ulong(),C.c_uint();children=C.POINTER(C.c_ulong)()
            assert xlib.XQueryTree(display,int(window.canvas.winId()),C.byref(root),C.byref(parent),C.byref(children),C.byref(count))
            assert count.value==1,f'Expected one native child, got {count.value}'
            child=children[0];xlib.XFree(children)
            def click(index):
                scale=window.canvas.devicePixelRatioF()
                event=Event();event.button=Button(type=5,display=display,window=child,root=root.value,
                    x=round((33+38*index)*scale),y=round((window.canvas.height()-31)*scale),button=1,same_screen=1)
                assert xlib.XSendEvent(display,child,False,1<<3,C.byref(event))
                xlib.XFlush(display)
            click(0);paused=wait(lambda s:s['ready'] and s['settled'] and not s['running'])
            steps=paused['stats']['steps']
            click(1);wait(lambda s:s['settled'] and s['stats']['steps']==steps+1)
            click(2);wait(lambda s:s['ready'] and s['settled'] and s['stats']['steps']==0)
        finally:
            xlib.XCloseDisplay(display)
        # Exercise the actual shared Save action and cancel only its own dialog.
        observed=[]
        def cancel():
            for widget in app.topLevelWidgets():
                if isinstance(widget,QFileDialog):observed.append(True);widget.reject()
        timer=QTimer();timer.setInterval(20);timer.timeout.connect(cancel);timer.start()
        window.save_action.trigger();timer.stop()
        assert observed,'The Save dialog was not opened'
        assert not window.state['error_message']
        # An export failure must not stop the render worker or poison the solver.
        try:
            window.native.call('export',png=str(args.output.parent/'missing-directory/image.png'))
        except Exception:
            pass
        else:
            raise AssertionError('Invalid export destination was accepted')
        window.native.call('step');wait(lambda s:s['settled'] and s['stats']['steps']==1)
        result=dict(passed=True,native_transport_buttons=True,native_save_dialog_cancel=True,
            export_failure_recovers=True,visible_canvas_sample_colors=len(colors),
            steps_without_python_events=after['stats']['steps']-before['stats']['steps'],
            presentations_without_python_events=after['presentations']-before['presentations'],
            rendering=after['rendering'])
        args.output.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:
        window.shutdown()


if __name__=='__main__':
    main()
