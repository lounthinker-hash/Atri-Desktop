"""Register an observed physical target to a destination on the actor's monitor."""
import ctypes
import math
from ctypes import wintypes
from PySide6.QtCore import QPoint,QRect
from PySide6.QtGui import QGuiApplication


def walk_destination(actor, gesture, height, area):
    """All points here are logical window origins on the current monitor."""
    here=actor.pos()
    left,top=area.left(),area.top()
    right=max(left,area.right()+1-actor.width())
    bottom=max(top,area.bottom()+1-actor.height())

    def inside(x,y):
        return QPoint(round(min(right,max(left,x))),round(min(bottom,max(top,y))))

    if gesture=="walk_back":
        return inside(actor.home.x(),actor.home.y())
    step=height*(.13 if gesture=="tiptoe" else .18)
    routine=actor.active_behavior
    if routine=="crab" and actor.props.ground is not None:
        # Stop one shoe-length short of the actual prop's near edge.
        offset=actor.props.ground.x()-(here.x()+actor.width()*.5)
        dx=math.copysign(min(step,max(0.,abs(offset)-height*.115)),offset)
        return inside(here.x()+dx,here.y())
    if routine=="watch" and actor.behavior_context:
        # A video or idle classification on its own has no object location.
        return here
    if routine in ("rest","watch"):
        # She can rest where she arrived. A longer spell standing in one spot
        # sometimes warrants a local stretch, with no permanent rest corner.
        if actor.standing_seconds() < (130. if routine=="rest" else 235.):
            return here
        step*=.74 if routine=="rest" else .88

    # For an unprompted stretch or a menu step, look for nearby room she
    # hasn't just crossed. Keep moving through an open patch until its edge
    # or her own recent footsteps give her a reason to change course.
    recent=actor.walk_history
    reach=2.8*step
    best=here
    best_score=-math.inf
    for ux,uy in ((1,0),(-1,0),(.8,-.6),(-.8,-.6),(.8,.6),(-.8,.6),(0,-1),(0,1)):
        p=inside(here.x()+step*ux,here.y()+step*uy)
        moved=math.hypot(p.x()-here.x(),p.y()-here.y())
        if moved<step*.65:continue
        ahead=inside(here.x()+reach*ux,here.y()+reach*uy)
        room=math.hypot(ahead.x()-here.x(),ahead.y()-here.y())/reach
        gaps=[math.hypot(p.x()-q.x(),p.y()-q.y())/step for q in recent]
        fresh=min(2.,min(gaps,default=2.))
        # A modest bias towards interior room avoids a fixed first direction.
        margin=min((p.x()-left),(right-p.x()),(p.y()-top),(bottom-p.y()))/step
        score=room+fresh*.73+min(3.,margin)*.16+moved/step*.24
        if routine is None:
            # A single short step is normally lateral. On repeated requests,
            # favor a different open patch once she has covered this direction.
            score+=.13*abs(ux)
            repeats=sum(max(0.,(ux*x+uy*y-.72)/.28) for x,y in actor.walk_headings)
            score-=.31*repeats
        if routine=="rest":
            score+=.22*(p.y()-here.y())/step
        elif routine=="watch":
            score+=.19*(here.y()-p.y())/step
        if actor.last_target_rect is not None:
            foot_x=p.x()+actor.width()*.5
            line=actor.last_target_rect
            if (line.left()-height*.20<=foot_x<=line.right()+height*.20 and
                    p.y()+actor.height()-height<=line.bottom() and p.y()+actor.height()>=line.top()):
                score-=4.
        if score>best_score:
            best,best_score=p,score
    return best


def native_monitor(actor):
    class Monitor(ctypes.Structure):
        _fields_=[("size",wintypes.DWORD),("bounds",wintypes.RECT),("work",wintypes.RECT),
                  ("flags",wintypes.DWORD),("device",ctypes.c_wchar*32)]
    user=ctypes.windll.user32
    hwnd=ctypes.c_void_p(int(actor.winId()))
    area=wintypes.RECT()
    if not user.GetClientRect(hwnd,ctypes.byref(area)):return None
    point=wintypes.POINT(area.right//2,area.bottom//2)
    if not user.ClientToScreen(hwnd,ctypes.byref(point)):return None
    user.MonitorFromPoint.argtypes=[wintypes.POINT,wintypes.DWORD]
    user.MonitorFromPoint.restype=ctypes.c_void_p
    handle=user.MonitorFromPoint(point,0)
    info=Monitor()
    info.size=ctypes.sizeof(info)
    if not handle or not user.GetMonitorInfoW(ctypes.c_void_p(handle),ctypes.byref(info)):return None
    r=info.bounds
    return info.device.casefold(),(r.left,r.top,r.right-r.left,r.bottom-r.top)


def beside(actor,target):
    if not isinstance(target,dict):return None
    screen=QGuiApplication.screenAt(actor.geometry().center())
    if screen is None:return None
    device=str(target.get("monitor","")).casefold()
    try:
        x,y,w,h=map(int,target["rect_px"])
        mx,my,mw,mh=map(int,target["monitor_px"])
        if w<=0 or h<=0 or not QRect(mx,my,mw,mh).contains(QRect(x,y,w,h)):return None
    except (KeyError,ValueError,TypeError):return None
    if QGuiApplication.platformName()=="windows":
        if native_monitor(actor)!=(device,(mx,my,mw,mh)):return None
    elif screen.name().casefold()!=device:
        return None
    scale=screen.devicePixelRatio()
    origin=screen.geometry().topLeft()
    rect=QRect(origin+QPoint(round((x-mx)/scale),round((y-my)/scale)),
               origin+QPoint(round((x+w-1-mx)/scale),round((y+h-1-my)/scale)))
    area=screen.availableGeometry()
    # Foot canvas spans the window, but only the central 40% carries the body.
    half=actor.width()//2
    clearance=round((actor.height()-80)*.24)+14
    positions=[]
    for center in (rect.left()-clearance,rect.right()+clearance):
        left=min(area.right()+1-actor.width(),max(area.left(),center-half))
        top=min(area.bottom()+1-actor.height(),max(area.top(),rect.center().y()-round(actor.height()*.33)))
        if abs((left+half)-rect.center().x())>=rect.width()/2+clearance-2:
            positions.append(QPoint(left,top))
    if not positions:return None
    destination=min(positions,key=lambda p:(p-actor.pos()).manhattanLength())
    return rect,destination
