"""Small, foot-anchored deformations of the original white-dress artwork."""
from dataclasses import dataclass
from functools import lru_cache
import ctypes
import math
from pathlib import Path
import random

import numpy as np
from PIL import Image
from desktop_fabric import material_fields, material_offset
import desktop_stride


try:
    _raster = ctypes.CDLL(str(Path(__file__).resolve().parent/"desktop/renderer/warp_rgba.dll")).warp_rgba
    _raster.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
                        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    _raster.restype = None
except (OSError, AttributeError):
    _raster = None


@dataclass(frozen=True)
class Gesture:
    label: str
    mood: str
    # Weight shift, head tilt, chin drop, sleeve spread, hand lift.
    shape: tuple
    pose: str = 'p1'


GESTURES = {
    'settle': Gesture('站定呼吸', 'neutral', (0, 0, 0, 0, 0)),
    'glance': Gesture('抬眼看你', 'attentive', (0, 0, -.25, .1, .12)),
    'greet': Gesture('抬手招呼', 'relaxed', (-.3, .4, -.15, .55, .65)),
    'look_around': Gesture('左右张望', 'curious', (.15, .18, -.25, .15, 0)),
    'turn': Gesture('侧身回望', 'attentive', (-.85, -.7, -.28, .15, .2)),
    'turn_away': Gesture('转身远望', 'source', (.28, -.22, -.15, .05, .1), 'p4'),
    'step': Gesture('挪步换个位置', 'relaxed', (.08, -.12, 0, .22, .08)),
    'walk_back': Gesture('走回原处', 'neutral', (-.1, .16, .06, .15, .12)),
    'tiptoe': Gesture('踮脚靠近', 'curious', (.52, .48, -.5, .4, .35)),
    'peek': Gesture('探过头来', 'curious', (.95, .95, .12, -.05, .2)),
    'lean': Gesture('倾身靠近', 'attentive', (.8, .52, .25, -.1, .1)),
    'inspect': Gesture('俯身细看', 'focused', (.7, .67, .8, -.15, .35)),
    'look_down': Gesture('低头', 'thoughtful', (-.25, -.28, .74, -.1, .15)),
    'read': Gesture('低头细读', 'focused', (-.29, -.34, .68, -.28, .38)),
    'turn_page': Gesture('翻页手势', 'attentive', (-.12, -.15, .68, -.33, .6)),
    'write': Gesture('落笔记录', 'focused', (-.24, -.48, 1.18, -2.05, 3.12)),
    'think': Gesture('歪头思考', 'thoughtful', (-.5, -.82, .2, -.18, .35)),
    'count': Gesture('跟着节奏数数', 'focused', (.18, .3, .42, -.05, .56)),
    'listen': Gesture('侧耳倾听', 'attentive', (.47, .85, -.12, -.08, .1)),
    'watch': Gesture('静静注视', 'neutral', (-.16, -.29, -.18, -.06, .02)),
    'wait': Gesture('留在旁边', 'relaxed', (.28, -.24, .03, -.1, .08)),
    'hum': Gesture('哼着旋律', 'content', (.16, .08, -.15, .38, .22)),
    'sway': Gesture('随拍摇摆', 'happy', (0, .1, -.1, .43, .24)),
    'knead': Gesture('揉面的手势', 'focused', (.18, .13, .65, -.5, .5)),
    'stir': Gesture('搅拌的手势', 'attentive', (-.22, -.35, .55, -.25, .52)),
    'taste': Gesture('凑近尝一口', 'curious', (.55, .5, .65, -.35, .82)),
    'proud': Gesture('得意地挺胸', 'proud', (-.35, -.4, -.73, .58, .22)),
    'cheer': Gesture('举手欢呼', 'excited', (0, -.1, -.55, .62, .66), 'p2'),
    'hop': Gesture('高兴地轻跳', 'happy', (.12, .18, -.35, .4, .28), 'p3'),
    'beckon': Gesture('招呼你过来', 'relaxed', (-.24, -.38, -.14, .36, .58)),
    'point': Gesture('抬手示意', 'curious', (.44, .37, -.08, .54, .6)),
    'bow': Gesture('郑重鞠躬', 'content', (.04, .08, 1.55, -.45, .15)),
    'nod': Gesture('轻轻点头', 'relaxed', (0, .09, .22, -.06, .08)),
    'refuse': Gesture('摇头抗议', 'focused', (-.17, -.3, .1, .27, .17)),
    'surprise': Gesture('吃了一惊', 'curious', (-.53, -.4, -.62, .78, .75)),
    'shy': Gesture('害羞地低头', 'shy', (-.54, -.62, .7, -.48, .24)),
    'stretch': Gesture('伸个懒腰', 'relaxed', (.07, -.4, -.62, .35, .65), 'p2'),
    'yawn': Gesture('打哈欠', 'sleepy', (-.26, -.53, -.53, -.13, .4)),
    'doze': Gesture('打个盹', 'sleepy', (.26, .55, .87, -.14, -.2)),
    'rest': Gesture('安静歇一会', 'content', (.12, .4, .59, -.12, -.15)),
    'wake': Gesture('从困意中醒来', 'relaxed', (-.1, -.3, -.29, .37, .25)),
    'drag': Gesture('被带着移动', 'curious', (.12, .17, -.45, .6, .28)),
    'land': Gesture('站稳脚跟', 'relaxed', (0, -.12, .52, .31, -.1)),
}


def smooth(x):
    x = max(0., min(1., x))
    return x * x * (3 - 2 * x)


class BodyMotion:
    # Natural frequencies (radians/second). The head can lead a look while
    # sleeves, body weight and long hair take a little longer to follow.
    response = np.array([9.,14.,11.,8.5,11.,15.,16.,19.,7.5])

    def __init__(self, now):
        self.name = 'settle'
        self.start = now
        self.duration = 0.
        self.last = np.zeros(9)
        self.velocity = np.zeros(9)
        self.seed = random.uniform(0, 40)
        self.direction = 1
        self.cursor = 0.
        self.look_direction = 0.
        self.sample_time = now
        self.hair_lag = 0.
        self.lean_trace = 0.
        self.weight = self.weight_target = 0.
        self.weight_at = now + random.uniform(5, 11)
        self.drag_speed = np.zeros(2)
        self.drag_time = now
        self.walk_distance = None
        self.walk_height = 390.
        self.walk_axis = np.zeros(2)
        self.walk_pose = "p1"
        self.leg_facing = self.previous_facing = 0.
        self.legs = None
        self.previous_legs = None
        self.support_foot = 0
        self.braking = False
        self.brake_start = 0.
        self.brake_legs = None
        self.step_art = self.releasing_art = None

    def start_gesture(self, name, seconds, now):
        if name not in GESTURES:
            name = 'settle'
        self.previous_legs=self.legs
        self.releasing_art=self.step_art
        self.step_art=None
        self.previous_facing=self.leg_facing
        self.walk_distance=None
        if name not in ('settle','glance','inspect'):
            self.braking=False
        self.name = name
        self.start = now
        self.duration = max(0., seconds)

    def plan_walk(self,distance,height):
        displacement=desktop_stride.vector(distance)
        length=np.linalg.norm(displacement)
        self.walk_distance=displacement/height if length>=1 else None
        self.walk_axis=displacement/max(1.,length)
        self.walk_height=height

    def brake_walk(self,now,height):
        if self.legs is None or self.braking:
            return
        self.brake_legs=self.legs.copy()
        self.brake_start=now
        self.walk_height=height
        self.braking=True

    def _stop_feet(self,now,window_offset):
        age=max(0.,now-self.brake_start)
        self.braking=age<.48
        if not self.braking:
            self.brake_legs=self.previous_legs=None
            return None
        traveled=(desktop_stride.vector(window_offset)/self.walk_height
                  if window_offset is not None else np.zeros(2))
        # Set the moving shoe down first. The loaded toe resists the window's
        # actual coast until weight can transfer; only then does it draw in.
        target=self.brake_legs.copy()
        planted=self.support_foot
        other=1-planted
        target[other]*=1.-smooth(age/.26)
        release=smooth((age-.285)/.195)
        target[planted,:2]-=traveled
        target[planted]*=1.-release
        remaining=np.linalg.norm(target[planted,:2])
        target[planted,1]-=min(.008,.38*remaining)*math.sin(math.pi*release)
        return target

    @staticmethod
    def travel_fraction(u):
        return desktop_stride.progress(u)

    def walk_art(self,image,now,window_offset=None,*,pose="p1"):
        self.walk_pose=pose
        if self.braking:
            self.legs=self._stop_feet(now,window_offset)
            self.leg_facing=self.previous_facing*(1-smooth((now-self.brake_start)/.48))
            return self._release_step(image,self.legs,self.leg_facing,pose,now-self.brake_start)
        age=max(0.,now-self.start)
        phase=min(1.,age/max(.1,self.duration))
        walk=self.walk_distance
        target=None
        facing=0.
        if walk is not None:
            distance=(self.travel_fraction(phase)*walk if window_offset is None else
                      desktop_stride.vector(window_offset)/self.walk_height)
            facing=self.walk_axis[0]*smooth(age/.26)*smooth((self.duration-age)/.32)
            self.step_art=desktop_stride.step_art(pose,self.name,self.walk_height,walk*self.walk_height,
                                    distance*self.walk_height,phase,self.support_foot)
            if self.step_art is not None:
                self.support_foot=self.step_art.support
                self.legs=self.step_art.feet()
                self.leg_facing=facing
                return desktop_stride.paint_art(image,self.step_art,facing)
            target,self.support_foot=desktop_stride.stance(self.name,phase,walk,distance,self.duration,pose)
        release=1-smooth(age/.24)
        if self.previous_legs is not None and release>0:
            target=self.previous_legs*release if target is None else target+self.previous_legs*release
            facing+=self.previous_facing*release
        self.legs=target
        self.leg_facing=max(-1.,min(1.,facing))
        return self._release_step(image,target,self.leg_facing,pose,age)

    def _release_step(self,image,target,facing,pose,age):
        base=desktop_stride.paint(image,target,facing,pose)
        ink=self.releasing_art
        if age>=.16 or ink is None or target is None:
            self.releasing_art=None
            return base
        if (ink.kind=="rear")!=(pose=="p4"):
            return base
        # Register both successive drawings at the same two shoes before
        # returning the actor to the ordinary foot rig on a stop or a drag.
        source=desktop_stride.paint_art(image,ink,facing)
        delta=target-ink.feet()
        old=desktop_stride.shift_drawn_feet(source,delta,image.height,ink)
        size=(image.width,max(old.height,base.height))
        a=Image.new("RGBA",size);a.paste(old)
        b=Image.new("RGBA",size);b.paste(base)
        mixed=Image.blend(a.convert("RGBa"),b.convert("RGBa"),smooth(age/.16)).convert("RGBA")
        mixed.info["stride_heading"]=float(facing)
        return mixed

    def follow_drag(self, dx, dy, height, now, previous):
        """Window travel in body heights/second, sampled from actual mouse events."""
        elapsed = max(1/120,now-previous)
        speed = np.clip(np.array([dx,dy])/(height*elapsed),-3.,3.)
        retained = math.exp(-min(.5,elapsed)*15)
        self.drag_speed = self.drag_speed*retained+speed*(1-retained)
        self.drag_time = now

    def sample(self, now, pointer=0., animate=True):
        dt = max(0., min(.12,now-self.sample_time))
        self.sample_time = now
        age = max(0., now-self.start)
        active = self.name != 'settle' and age < self.duration
        name = self.name if active else 'settle'
        u = min(1., age/max(.1,self.duration))
        t = now + self.seed
        sway, tilt, chin, spread, hands = GESTURES[name].shape
        gait = lift = wrist = 0.
        beat = math.sin(age*4.2)
        arc = math.sin(math.pi*u)
        if name in ('step','walk_back','tiptoe') and self.walk_distance is not None:
            pairs=desktop_stride.pair_count(name,self.walk_distance,self.duration)
            cycle=desktop_stride.step_clock(u)*math.tau*pairs
            # The support is opposite the leading shoe, including when travelling
            # vertically. Anticipate the damped pelvis so weight arrives on time.
            lead=desktop_stride.first_foot(*self.walk_distance,self.walk_pose)
            gait_envelope=smooth(age/.23)*smooth((self.duration-age)/.28)
            gait=math.sin(cycle+.08*math.tau*pairs/(.795*self.duration))*(1-2*lead)*gait_envelope
            if self.step_art is not None:
                # Load the shoulder over the actual authored support foot.
                # The damped body channels keep velocity through double support.
                gait=(2*self.step_art.support-1)*.84*gait_envelope
            heading=self.walk_axis[0]*gait_envelope
            sway+=.38*heading+.28*gait
            tilt+=.11*heading-.12*gait
            chin-=.065*math.sin(cycle)**2
            hands-=.09*gait
            if name=='tiptoe':
                chin-=.40*arc
                spread+=.18*arc
        elif name=='hop':
            # The short beach hop ends this cue at the top of the arc. Its
            # following landing owns the descent, with the same body velocity.
            lift=1.8*(smooth(u) if self.duration<=.5 else arc**2)
            chin+=.16*arc
        elif name=='land':
            # One compression and release through the knees and torso after a drop.
            # The shoes stay planted; there is no second spring pulse on settling.
            chin*=arc**2
            hands*=arc**2
        elif name in ('read','write','turn_page','count','knead','stir','taste'):
            tilt += (.07 if name in ('read','write','turn_page') else .22)*math.sin(age*1.3)
            chin += (.045 if name in ('read','write','turn_page') else .13)*math.cos(age*.8)
            if name=='read':
                sway += .23*(2/math.pi)*math.asin(math.sin(age*1.35))
            else:
                speed={'write':2.1,'turn_page':2.6,'count':3.,'knead':3.5,'stir':3.2,'taste':1.7}[name]
                wrist=math.sin(age*speed)*arc
                if name=='write':
                    # The pen, fingers and nib perform the strokes in the original registered
                    # prop frames. Keep the supporting wrist still while they touch the page.
                    wrist*=.13
                hands += (.42 if name in ('knead','stir') else .16)*math.cos(age*speed)*arc
                if name=='knead':chin += .27*math.cos(age*speed)*arc
        elif name in ('hum','sway','beckon','greet','point'):
            sway += (.54 if name in ('hum','sway') else .2)*math.sin(age*2.4)
            tilt += .36*math.sin(age*2.4-.5)
            wrist = beat*arc
        elif name in ('turn','look_around','refuse'):
            speed = 5.2 if name=='refuse' else 1.25
            tilt += .57*math.sin(age*speed)*arc
            sway += .32*math.sin(age*speed-.35)*arc
        elif name in ('bow','nod','doze','wake','yawn'):
            chin += (.65 if name=='nod' else .32)*math.sin(age*(4 if name=='nod' else 1.45))*arc
        if name in ('drag','land'):
            horizontal,vertical = self.drag_speed*math.exp(-max(0.,now-self.drag_time)*5.)
            sway -= .36*horizontal
            tilt -= .24*horizontal
            chin -= .21*vertical
            hands -= .16*vertical
        # The eyes' target is already timed by the active window. Bend the neck
        # about its own attachment with the same sign in either direction. The
        # existing damped channels retain velocity on a reversal or interruption.
        look=max(-1.,min(1.,self.look_direction))
        if name in ('inspect','step','tiptoe','walk_back'):
            bias_sway,bias_tilt=GESTURES[name].shape[:2]
            sway-=abs(look)*bias_sway
            tilt-=abs(look)*bias_tilt
        sway+=.18*look
        tilt+=1.75*look
        self.cursor += (pointer-self.cursor)*(1-math.exp(-dt*5))
        if not active and animate and now>=self.weight_at:
            self.weight_target = random.uniform(-.42,.42)
            self.weight_at = now+random.uniform(9,18)
        self.weight += ((self.weight_target if not active and animate else 0)-self.weight)*(1-math.exp(-dt*1.7))
        # The chest rises before the sleeves and the ends of the hair follow.
        breath = (.60*math.sin(t*1.02+.13*math.sin(t*.19))+.15*math.sin(t*.62))*int(animate)
        sway += (.095*math.sin(t*.82)+.065*math.sin(t*.51))*int(animate)+self.weight
        tilt += .17*self.cursor + .09*math.sin(t*.88)*int(animate)
        lean = sway+.54*tilt
        self.lean_trace += (lean-self.lean_trace)*(1-math.exp(-dt*3.5))
        self.hair_lag += (self.lean_trace-lean-self.hair_lag)*(1-math.exp(-dt*7.5))
        hair = (.27*math.sin(t*1.68-.8) + .15*math.sin(t*.94))*int(animate)+gait*.6+1.3*self.hair_lag
        target = np.array([sway,tilt,chin,spread,hands,gait,lift,wrist,hair])
        # Re-target the moving body. Position and velocity survive every cue,
        # including settling, two inputs between paints, and a drag release.
        if dt:
            delta = self.last-target
            tangent = self.velocity+self.response*delta
            decay = np.exp(-self.response*dt)
            self.last = target+(delta+tangent*dt)*decay
            self.velocity = (self.velocity-self.response*tangent*dt)*decay
        return self.last, breath


@lru_cache(maxsize=20)
def _geometry(width,height):
    pad = max(6, round(height*.072))
    # Fine enough for the red necktie and the small hair locks at desktop size.
    # Knot spacing is in character units so high-DPI displays keep the same rig.
    columns = min(72,max(26,round((width+2*pad)/height*79)))
    xs = np.unique(np.linspace(0,width+2*pad,columns).round().astype(int))
    ys = np.unique(np.linspace(0,height+2*pad,83).round().astype(int))
    x,y=np.meshgrid(xs,ys)
    return pad,x-pad,y-pad,xs.astype(np.int32),ys.astype(np.int32)


@lru_cache(maxsize=20)
def _pillow_layout(xs,ys):
    indices=np.arange(len(xs)*len(ys)).reshape(len(ys),len(xs))
    order=np.stack((indices[:-1,:-1],indices[1:,:-1],indices[1:,1:],indices[:-1,1:]),axis=-1).reshape(-1,4)
    boxes=[(x,y,r,b) for y,b in zip(ys,ys[1:]) for x,r in zip(xs,xs[1:])]
    return order,boxes


def mesh_grid(width,height,pose=None):
    """Full mesh for inspection; the native rasterizer only needs the geometry."""
    pad,x,y,xs,ys=_geometry(width,height)
    order,boxes=_pillow_layout(tuple(xs.tolist()),tuple(ys.tolist()))
    return pad,x,y,order,boxes,xs,ys


def field_weights(dx,fy,pose):
    side=np.tanh(dx*20)
    outer=np.minimum(1,abs(dx)*7)
    crown=.16 if pose=='p3' else .24
    shoulder=crown+.09
    head=np.exp(-((fy-crown)/.165)**4)
    upper=np.clip((.98-fy)/.70,0,1)
    chest=np.exp(-((fy-(shoulder+.11))/.16)**2)
    sleeves=np.exp(-((fy-(.17 if pose=='p2' else .51))/(.20 if pose=='p2' else .16))**2)*outer
    hair=np.exp(-((fy-.46)/.24)**2)*outer**1.6
    legs=np.clip((fy-.64)/.32,0,1)
    return dx,fy,side,head,upper,chest,sleeves,hair,legs,shoulder


def displacement(weights,body,breath):
    dx,fy,side,head,upper,chest,sleeves,hair,legs,neck=weights
    sway,tilt,chin,spread,hands,gait,lift,wrist,lag=body
    # Inverse mesh. Both feet stay grounded except during an intentional step or hop.
    pelvis=np.clip((fy-.26)/.33,0.,1.)*np.clip((.91-fy)/.23,0.,1.)
    move_x = (.032*sway*upper + .105*tilt*(neck-fy)*head + .023*spread*side*sleeves
              + .0016*breath*side*chest
              + .014*wrist*sleeves*(side+.7) + .022*gait*pelvis)
    move_y = (.093*tilt*dx*head + .022*chin*(head+.25*chest) - .0030*breath*(chest+.14*head)
              - .032*hands*sleeves - .013*wrist*sleeves - .022*lift
              - .007*gait*side*(pelvis+.75*sleeves))
    return move_x,move_y


def attached_point(uv,width,height,pose,body,breath):
    """Place a prop on the same deformed hand that is drawn in the current frame."""
    u,v=uv
    x0=(u-.5)*width/height
    x,y=x0,v
    for _ in range(4):
        dx,dy=displacement(field_weights(x,y,pose),body,breath)
        x,y=x0+dx,v+dy
    return width/2+x*height,y*height


@lru_cache(maxsize=28)
def _registered_fields(w,h,registration):
    pose,width,height,left,top=registration
    _,x,y,_,_=_geometry(w,h)
    u,v=(x-left)/width,(y-top)/height
    return field_weights((u-.5)*width/height,v,pose),material_fields(u,v,pose)


@lru_cache(maxsize=12)
def _stride_profile(w,h,registration):
    pose,width,height,left,top=registration
    _,x,y,_,_=_geometry(w,h)
    fy=(y-top)/height
    # Half-width of the original front silhouette at hair, collar, shoulders,
    # sleeves and skirt. The projected front surface turns with its outline.
    marks=(.08,.12,.17,.21,.255,.29,.34,.40,.47,.55,.62,.67,.72,.85)
    radii=(.043,.048,.082,.075,.083,.093,.112,.129,.146,.171,.136,.079,.056,.035)
    center=np.interp(fy,marks,(.008,.008,.011,.013,.014,.012,.014,.013,.026,.012,.008,.009,.015,.016))
    radius=np.interp(fy,marks,radii)
    lateral=(x-left-width*.5)/height-center
    weight=np.clip((.80-fy)/.16,0.,1.)
    return lateral,radius,weight


def raster_grid(image,xs,ys,field,layout=None):
    """Sample straight or premultiplied source ink using inverse coordinates at grid knots."""
    width,height = int(xs[-1]),int(ys[-1])
    xs=np.ascontiguousarray(xs,dtype=np.int32)
    ys=np.ascontiguousarray(ys,dtype=np.int32)
    grid=np.ascontiguousarray(field,dtype=np.float64)
    source=image if image.mode=="RGBA" else image.convert("RGBA")
    if _raster is not None:
        output=np.empty((height,width,4),dtype=np.uint8)
        _raster(source.tobytes(),source.width,source.height,output.ctypes.data,width,
                xs.ctypes.data,len(xs),ys.ctypes.data,len(ys),grid.ctypes.data)
        return Image.fromarray(output)
    if layout is None:
        order,boxes=_pillow_layout(tuple(xs.tolist()),tuple(ys.tolist()))
    else:
        order,boxes=layout
    points=grid.reshape(-1,2)[order].reshape(-1,8)
    mesh=list(zip(boxes,map(tuple,points.tolist())))
    return source.transform((width,height),Image.Transform.MESH,mesh,Image.Resampling.BILINEAR)


def warp(image,pose,body,breath,fabric=None,rig=None):
    w,h=image.size
    pad,x,y,xs,ys=_geometry(w,h)
    rig = rig or (((pose,w,h,0,0),1.),)
    sx = sy = 0.
    for registration,weight in rig:
        if weight <= 0:
            continue
        structure,materials=_registered_fields(w,h,registration)
        dx,dy=displacement(structure,body,breath)
        heading=image.info.get("stride_heading",0.)
        if heading and registration[0]=="p1":
            lateral,fy,side,_,_,chest,sleeves,*_=structure
            across,radius,envelope=_stride_profile(w,h,registration)
            torso=np.clip((fy-.27)/.083,0.,1.)
            torso=torso*torso*(3.-2.*torso)
            span=1.-.15*abs(heading)*envelope*torso
            surface=np.maximum(0.,1.-(across/(radius*span))**2)
            # Keep the head silhouette at source width. Nose, cheeks and both
            # eyes move as one face patch, with a smaller shift in the far hair.
            rim=np.clip((abs(across)-.055)/.055,0.,1.)
            face=.009+.006*(1-rim*rim*(3.-2.*rim))
            dx=dx+across*(1-1/span)+heading*(.36*radius*surface*envelope*torso+face*(1-torso))
            dy=dy-.010*heading*side*(chest+.55*sleeves)*envelope
        if fabric is not None:
            fx,fy=material_offset(materials,fabric)
            dx,dy=dx+fx,dy+fy
        scale=weight*registration[2]
        sx,sy=sx+scale*dx,sy+scale*dy
    grid=np.stack((x-sx,y-sy),axis=-1)
    return raster_grid(image,xs,ys,grid),pad
