"""Two registered legs of the original white uniform. Foot targets are in body heights."""
from functools import lru_cache
from dataclasses import dataclass
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
import math

import numpy as np
from PIL import Image


_STUDIES=Path(__file__).resolve().parent / "assets/motion/pose-studies"


@lru_cache(maxsize=3)
def _art_module(directory, filename="read"):
    spec=spec_from_file_location(f"atri_{directory}_{filename}",_STUDIES/directory/(filename+".py"))
    module=module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class StepArt:
    tile: Image.Image
    origin: tuple
    upper: tuple
    correction: tuple
    joints: tuple
    support: int
    kind: str

    def feet(self):
        rest=((71.,416.),(110.,417.)) if self.kind=="rear" else (
            (82.75*420/390,388.5*420/390),(109*420/390,374*420/390))
        delta=np.zeros((2,4))
        for j,leg in enumerate(self.joints):
            delta[j,:2]=(np.asarray(leg["sole"])+self.correction-rest[j])/420
        return delta


def warm_step_art():
    """Decode the three read-only atlases on the companion's preparation worker."""
    horizontal=_art_module("small-walk-review")
    for case in ("menu-left","target-right"):
        horizontal.sample_lower(case,0)
    _art_module("small-up-step").sample_lower(0)
    _front_parts()


@lru_cache(maxsize=1)
def _front_parts():
    # Use the art team's two complete leg plates, including the skin hidden
    # by the standing skirt. An occluded calf cannot be recovered by moving
    # pixels from an already composited crossing frame.
    art=_art_module("small-walk-review","art")
    hem,legs,_=art.sources()
    def resize(part):
        return part.convert("RGBa").resize((168,424),Image.Resampling.LANCZOS).convert("RGBA")
    return resize(hem),tuple(resize(legs[side]) for side in ("left","right"))


def _front_tile(legs,shifts,upper):
    from desktop_motion import raster_grid
    hem,plates=_front_parts()
    rest=(((64.5,211),(66.6,282.5),(73.,358.),(82.75,388.5)),
          ((91.5,211),(93.,281.),(113.5,347.),(109.,374.)))
    moved=tuple({k:p+shift*(.19 if k=="hip" else 1.) for k,p in leg.items()}
                for leg,shift in zip(legs,shifts))
    top=156
    bottom=max(424,math.ceil(max(p["sole"][1] for p in moved)+5))
    xs,ys,x,y=_grid(168,bottom-top,0)
    gy=y+top
    result=Image.new("RGBA",(168,bottom-top))
    # The painted near leg crosses in front of the far leg and the skirt
    # covers both moving thigh roots. Each toe is drawn exactly once.
    for side in (1,0):
        original=np.asarray(rest[side])*420/390
        placed=np.asarray([moved[side][k] for k in ("hip","knee","ankle","sole")])
        # Carry the complete shoe on the registered sole. The ankle lies
        # inside its leather upper and receives the same translation.
        placed[2]=original[2]+placed[3]-original[3]
        moved[side]["ankle"]=placed[2]
        sy=np.r_[0.,original[:,1],470.]
        ty=np.r_[0.,placed[:,1],470.+placed[3,1]-original[3,1]]
        offsets=placed[:,0]-original[:,0]
        sx=np.r_[offsets[0],offsets,offsets[-1]]
        mapping=np.stack((x-np.interp(gy,ty,sx),np.interp(gy,ty,sy)),axis=-1)
        result.alpha_composite(raster_grid(plates[side],xs,ys,mapping))
    result.alpha_composite(hem,(round(upper[0]),round(upper[1])-top))
    return result,moved


def _aim_tile(tile,origin,legs,shifts,rear):
    """Move each drawn calf and shoe together, joining the thigh beneath the hem."""
    from desktop_motion import raster_grid
    shifts=np.asarray(shifts)
    if np.max(np.abs(shifts))<.05:
        return tile,legs
    raised=tuple({k:np.asarray(v)+shift for k,v in leg.items()} for leg,shift in zip(legs,shifts))
    extra=max(0,math.ceil(max(leg["sole"][1]+4-origin[1]-tile.height for leg in raised)))
    xs,ys,x,y=_grid(tile.width,tile.height+extra,0)
    gy=y+origin[1]
    line=[]
    for leg in raised:
        points=sorted((leg[k] for k in ("knee","ankle","sole")),key=lambda v:v[1])
        line.append(np.interp(gy,[v[1] for v in points],[v[0] for v in points]))
    middle=(line[0]+line[1])/2
    width=np.maximum(1.4,np.abs(line[0]-line[1])*.17)
    right=np.clip(.5+(x-middle)*np.sign(line[1]-line[0])/width,0.,1.)
    right=right*right*(3.-2.*right)
    # Only the proximal thigh stretches. Registered knees, ankles and the
    # full leather upper and sole share the same vector in the raster.
    follow=np.clip((gy-(241 if rear else 250))/(36 if rear else 35),0.,1.)
    follow=follow*follow*(3.-2.*follow)
    delta=(shifts[0]+(shifts[1]-shifts[0])*right[:,:,None])*follow[:,:,None]
    mapped=np.stack((x,y),axis=-1)-delta
    return raster_grid(tile,xs,ys,mapped),raised


def _rear_step(travel,actual):
    dx,dy=travel
    wx,wy=actual
    total=-dy
    distance=min(total,max(0.,-wy))
    pairs=max(1,round(total/37.2))
    position=distance/total*pairs
    pair=min(pairs-1,int(position));part=position-pair
    stride=total/pairs
    # Preserve the asymmetric entrance and exit. The middle three cycles
    # share a complete weight exchange and can repeat for a longer route.
    if pairs==1:
        ref_pair=0 if part<.5 else 4
    else:
        ref_pair=0 if pair==0 else 4 if pair==pairs-1 else 1+(pair-1)%3
    reference=-(ref_pair+part)*37.2
    reader=_art_module("small-up-step")
    tile,origin,row=reader.sample_lower(max(-186.,min(0.,reference)))
    original=row["rearJoints420Px"]
    offsets=[]
    for side,rest in enumerate(((71.,416.),(110.,417.))):
        swing=max(0.,min(1.,part*2-side))
        peak=math.sin(math.pi*swing)**2
        mark=.75+.5*side
        last=0. if pair==0 else (pair-1+mark)*stride
        landing=total if pair==pairs-1 else (pair+mark)*stride
        advanced=last+(landing-last)*ease(swing)
        desired=(rest[0]+(1-2*side)*1.2*peak+1.5*(advanced*dx/total-wx),
                 rest[1]+1.5*(-advanced-wy)-3.6*peak)
        offsets.append(np.asarray(desired)-original[side]["sole"])
    tile,legs=_aim_tile(tile,origin,original,offsets,True)
    return StepArt(tile,origin,(0,0),(0,0),legs,int(part<.5),"rear")


def _sample_step(pose,travel,actual,previous_support):
    dx,dy=travel
    if pose=="p4" and dy<-.5:
        return _rear_step(travel,actual)
    total=math.hypot(dx,dy)
    if pose!="p1" or total<1:
        return None
    unit=np.asarray(travel)/total
    progress_px=min(total,max(0.,float(np.dot(actual,unit))))
    sign=-1 if dx<0 else 1
    case="menu-left" if sign<0 else "target-right"
    native_stride=25. if sign<0 else 43.25
    count=max(1,round(total/(55.*280/390)))
    position=progress_px/total*count
    cycle=min(count-1,int(position))
    amp=total/(count*native_stride)
    # Use the real x unchanged on the two matching horizontal routes. For
    # another length, select the same drawn phase on the measured travel axis.
    native_x=(position-cycle)*native_stride*sign
    if abs(dx-173)<.01 and abs(dy)<.01:native_x=actual[0]
    row=_art_module("small-walk-review").sample_lower(case,native_x)
    anchors=row["anchors390"]
    legs=tuple({k:np.asarray(anchors[side][k])*420/390 for k in ("hip","knee","ankle","sole")}
               for side in ("left","right"))
    shifts=[]
    for leg,start in zip(legs,(82.75,109.)):
        delta=leg["sole"][0]-start*420/390
        shifts.append(delta*(unit*sign*amp-np.array((1.,0.))))
    origin=row["tileOriginPhysical"]
    tile=row["lower"]
    if np.max(np.abs(shifts))>.05:
        tile,legs=_front_tile(legs,shifts,row["upperOffsetPhysical"])
    correction=(unit*(progress_px+row["actorCorrectionLogical"]*sign*amp)-actual)*1.5
    contacts=[j for j,side in enumerate(("left","right")) if anchors[side]["contact"]]
    # During the drawn double-support interval both shoes are planted.
    # Transfer weight over the centre of the stride rather than waiting
    # until the toe of the unloading shoe has already left the ground.
    preferred=int(.25<=position-cycle<.75)
    support=preferred if preferred in contacts else contacts[0] if contacts else previous_support
    return StepArt(tile,origin,row["upperOffsetPhysical"],correction,legs,support,"horizontal")


def step_art(pose,gesture,height,travel,actual,u,previous_support):
    """Register the authored lower poses to measured two-dimensional window travel."""
    if abs(height-280)>.5 or gesture not in ("step","walk_back","tiptoe"):
        return None
    art=_sample_step(pose,travel,actual,previous_support)
    strength=ease(u/.14)*ease((1-u)/.14)
    if art is not None and gesture=="tiptoe" and strength>.002:
        _tiptoe(art,strength)
    return art


def _tiptoe(art,strength):
    from desktop_motion import raster_grid
    tile=art.tile
    xs,ys,x,y=_grid(tile.width,tile.height,0)
    upper_y=y+art.origin[1]
    soles=[np.asarray(leg["sole"],dtype=float) for leg in art.joints]
    mid=(soles[0][0]+soles[1][0])*.5
    side=np.clip(.5+(x-mid)*np.sign(soles[1][0]-soles[0][0])/4,0.,1.)
    level=np.clip((upper_y-342.)/29.,0.,1.)
    field=np.zeros((*x.shape,2))
    joints=[]
    for j,pivot in enumerate(soles):
        angle=(1-2*j)*strength*.16
        ca,sa=math.cos(angle),math.sin(angle)
        vx=x-pivot[0];vy=upper_y-pivot[1]
        # Rotation about the planted toe raises the original shoe's heel.
        influence=(side if j else 1-side)*level
        field[:,:,0]+=influence*((ca-1)*vx+sa*vy)
        field[:,:,1]+=influence*(-sa*vx+(ca-1)*vy)
        leg=dict(art.joints[j])
        v=np.asarray(leg["ankle"])-pivot
        fraction=np.clip((leg["ankle"][1]-342)/29,0.,1.)
        leg["ankle"]=np.asarray(leg["ankle"])+fraction*np.array(((ca-1)*v[0]-sa*v[1],sa*v[0]+(ca-1)*v[1]))
        joints.append(leg)
    art.tile=raster_grid(tile,xs,ys,np.stack((x,y),axis=-1)+field)
    art.joints=tuple(joints)


@lru_cache(maxsize=5)
def _front_upper(width,height):
    # Original d1/p1 arm, torso and hair layer at its registered FG crop.
    # All changing face ink comes from the current ExpressionRenderer canvas.
    with Image.open(_STUDIES.parents[1]/"sprites/layers/tatr01_715.png") as part:
        layer=part.convert("RGBA")
    scale=height/1706
    top=round(145*scale)
    layer=layer.resize((width,round(1084*scale)-top),Image.Resampling.LANCZOS)
    source=Image.new("RGBA",(width,height))
    source.paste(layer,(0,top))
    return source


def paint_art(image,step,facing):
    w,h=image.size
    scale=h/420.
    x,y=(round(k*scale) for k in step.correction)
    tx,ty=(round(k*scale) for k in step.origin)
    upper=image.crop((0,0,w,ty))
    if step.kind=="horizontal":
        intact=_front_upper(w,h).copy()
        intact.paste(upper,(0,0))
        upper=intact
    tile=step.tile
    if h!=420:
        size=tuple(max(1,round(v*scale)) for v in tile.size)
        tile=tile.resize(size,Image.Resampling.LANCZOS)
    size=(w,max(h,ty+y+tile.height))
    result=Image.new("RGBA",size)
    result.alpha_composite(tile,(tx+x,ty+y))
    # Include the same fractional-frame registration on both halves, once.
    ux,uy=(round(k*scale) for k in step.upper)
    result.alpha_composite(upper,(x+ux,y+uy))
    result.info["stride_heading"]=float(facing)
    return result


def shift_drawn_feet(image,delta,original_height,step):
    from desktop_motion import raster_grid
    w,h=image.size
    xs,ys,x,y=_grid(w,h,0)
    h0=original_height
    a,b=(np.asarray(j["ankle"])+step.correction for j in step.joints)
    mid=(a[0]+b[0])*.5*h0/420.
    switch=np.clip((x-mid)/max(1.,.015*h0)+.5,0,1)
    follow=np.clip((y/h0-.60)/.15,0.,1.)
    dx=(delta[0,0]*(1-switch)+delta[1,0]*switch)*h0*follow
    dy=(delta[0,1]*(1-switch)+delta[1,1]*switch)*h0*follow
    return raster_grid(image,xs,ys,np.stack((x-dx,y-dy),axis=-1))


def ease(value):
    value=max(0.,min(1.,value))
    return value*value*(3.-2.*value)


def progress(u):
    # Allow the trailing leg to take the weight before, and after, the window travels.
    return ease((u-.095)/.795)


def vector(value):
    if np.isscalar(value):
        return np.array((value,0.),dtype=float)
    if hasattr(value,"toTuple"):
        value=value.toTuple()
    return np.asarray(value,dtype=float)


def pair_count(name,travel,duration=None):
    dx,dy=vector(travel)
    stride=.079 if name=="tiptoe" else .13
    pairs=math.ceil(math.hypot(dx,dy/.84)/stride)
    if duration is not None:
        half_step=.22 if name=="tiptoe" else .30
        pairs=min(pairs,math.floor(duration*.795/(2*half_step)))
    return max(1,pairs)


def step_clock(u):
    return max(0.,min(1.,(u-.095)/.795))


def first_foot(dx,dy,pose="p1"):
    if abs(dy)>.16*math.hypot(dx,dy):
        return int(dy<=0) if pose=="p4" else int(dy>=0)
    return int(dx>=0)


def stance(name,u,travel,world,duration=None,pose="p1"):
    """Local shoe displacement yielding piecewise stationary support in desktop coordinates."""
    dx,dy=vector(travel)
    world_x,world_y=vector(world)
    count=pair_count(name,travel,duration)
    # Step on a time clock, even while the window's eased velocity changes.
    # Faster middle steps cover more ground without shortening the contact time.
    position=step_clock(u)*count
    i=min(count-1,int(position)); within=position-i
    engaged=ease(u/.095)*ease((1-u)/.11)
    # The drawn near foot already touches the lower canvas edge. On a depth
    # step let that foot trail going down and lead going up. The farther shoe
    # can move forward into the space below its naturally higher sole.
    depth=abs(dy)/max(1.e-8,math.hypot(dx,dy))
    first=first_foot(dx,dy,pose)
    support=1-first if within<.5 else first
    # On a diagonal the leading shoe sometimes crosses towards the other
    # foot. Widen the base during weight transfer before translating.
    widen=min(.014,max(0.,.43*abs(dx)/count-.023)*.5)*engaged
    targets=[]
    for index in range(2):
        swing=max(0.,min(1.,within*2-(0 if index==first else 1)))
        plant=1-math.sin(math.pi*swing)**2
        rise=(1-plant)*(.019 if name=="tiptoe" else .026)*(1-.26*depth)
        # Place the toe around the middle of its coming support interval.
        # Stagger the two feet along travel instead of both reaching a future
        # pair boundary. Offset lateral placement away from the opposite calf.
        center=.75 if index==first else 1.25
        outward=(1 if index==1 else -1)*(1 if dx>=0 else -1)
        center_x=center+.115*outward
        # In the back drawing both soles are at the lower edge. Schedule their
        # depth contact at the far end of the support interval when going up
        # (near end when going down), before the step is projected to pixels.
        shallow=pose=="p4" or index==0
        center_y=center+((-.25 if dy>=0 else .25) if shallow else 0.)
        previous_x=0. if i==0 else ease((i-1+center_x)/count)
        previous_y=0. if i==0 else ease((i-1+center_y)/count)
        landing_x=1. if i==count-1 else ease((i+center_x)/count)
        landing_y=ease((i+center_y)/count)
        blend=ease(swing)
        x=dx*((1-blend)*previous_x+blend*landing_x)-world_x
        y=dy*((1-blend)*previous_y+blend*landing_y)-world_y
        x+=-widen if index==0 else widen
        if u>.89:
            y*=ease((1-u)/.11)
        # The original right sole is 14.1 px above the near sole at h=390.
        # Preserve the horizontal floor registration. Reserve room for the toe
        # to advance in depth when it carries the actor towards the viewer.
        gap=(.010 if index==0 else 0.) if pose=="p4" else (.0365 if index==1 else 0.)
        drop=max(0.,gap-.47*abs(dy)/count)*engaged
        roll=engaged*(.26 if name=="tiptoe" else .034)*plant
        roll+=.105*math.sin(2*math.pi*swing)
        targets.append((x,y+drop-rise,roll if index==0 else -roll,rise))
    return np.asarray(targets),support


def feet(name,u,travel,world):
    return stance(name,u,travel,world)[0][:,:3]


@lru_cache(maxsize=6)
def _plates(width,height,top,raw,pose):
    a=np.frombuffer(raw,np.uint8).reshape(height-top,width,4)
    x=np.arange(width)+.5
    y=(np.arange(top,height)+.5)/height
    boundaries=[.52,.52,.485,.46,.455,.465] if pose=="p4" else [.52,.53,.548,.581,.609,.63]
    split=np.interp(y,[.652,.70,.78,.86,.94,1.0],boundaries)*width
    side=x[None,:]<split[:,None]
    # Find the original skin/hem edge independently in each column. The skirt
    # stays in front while the visible thighs travel underneath it.
    rgb=a[:,:,:3].astype(np.int16)
    skin=(rgb[:,:,0]>rgb[:,:,1]+13)&(rgb[:,:,1]>rgb[:,:,2]+3)&(a[:,:,3]>64)
    below=(y[:,None]>.641)&(skin | (y[:,None]>.695))
    boundary=np.argmax(below,axis=0)
    leg_ink=np.arange(len(y))[:,None]>=boundary
    layers=[]
    for mask in (side&leg_ink,~side&leg_ink,~leg_ink):
        arr=a.copy()
        arr[:,:,3]*=mask
        arr[~mask,:3]=0
        layers.append(Image.fromarray(arr))
    return layers


@lru_cache(maxsize=8)
def _grid(width,height,top):
    xs=np.unique(np.linspace(0,width,max(12,round(width/5.3))).round().astype(np.int32))
    ys=np.unique(np.linspace(0,height-top,max(18,round((height-top)/4.5))).round().astype(np.int32))
    x,y=np.meshgrid(xs,ys)
    return xs,ys,x,y


def paint(image,motion,facing=0.,pose="p1"):
    """Inverse-sample the two shoes separately, so a lifted leg never drags the other leg."""
    if motion is None or np.max(np.abs(motion))<.0001:
        return image
    from desktop_motion import raster_grid
    w,h=image.size;top=round(h*.625)
    crop=image.crop((0,top,w,h))
    layers=_plates(w,h,top,crop.tobytes(),pose)
    # During an upward stop, a planted toe may use the existing margin below
    # the original PNG. Extend the raster only; retain the body's registration.
    toes=((.381,.989),(.589,.998)) if pose=="p4" else ((.551,.995),(.711,.9585))
    lower=max(toes[i][1]+motion[i,1] for i in (0,1))
    extra=math.ceil(.018*h) if lower>.999 else 0
    xs,ys,x,y=_grid(w,h+extra,top)
    output=Image.new("RGBA",(w,h-top+extra))
    # Keep the shoulders, face and posed hem in the original drawing. The proximal
    # knee follows a smaller arc; the distal calf delivers the planted shoe intact.
    for i,leg in enumerate(motion):
        foot_x,foot_y,angle=leg[:3]
        dx=foot_x*h;dy=foot_y*h
        # Flexion belongs to the swinging leg. Translation of a planted foot
        # in the window is not a knee bend. A knee comes forwards along travel.
        flex=leg[3] if len(leg)>3 else 0.
        knee_push=flex*h*(.44*facing+.035*(1 if i else -1))
        # Original knee and upper ankle centres as fractions of the registered
        # drawing. Preserve the projected shin length when changing its angle.
        if pose=="p4":
            knee_x,ankle_x=((.435,.310),(.606,.600))[i]
        else:
            knee_x,ankle_x=((.446,.477),(.621,.741))[i]
        knee_y=.722*h;ankle_y=.916*h
        shin_x=(ankle_x-knee_x)*w
        shin_y=ankle_y-knee_y
        knee_dx=.65*dx+knee_push
        new_shin_x=shin_x+dx-knee_dx
        new_shin_y=math.sqrt(max(shin_y**2*.82,shin_x**2+shin_y**2-new_shin_x**2))
        knee_dy=dy+shin_y-new_shin_y
        heights=np.array([top,.675*h,knee_y,ankle_y,1.055*h])
        x_offset=np.array([.39*knee_dx,.68*knee_dx,knee_dx,dx,dx])
        y_offset=np.array([min(-.004*h,.44*knee_dy),min(-.004*h,.63*knee_dy),knee_dy,dy,dy])
        toe_x,toe_y=toes[i][0]*w,toes[i][1]*h
        # Rotate the ankle and shoe about its toe during push-off. Keep the toe's
        # point of contact while the heel changes angle.
        z=np.clip((y+top-dy-.84*h)/(.087*h),0.,1.)
        a=-angle*z*z*(3-2*z)
        from_x=x-(toe_x+dx);from_y=y+top-(toe_y+dy)
        tx=toe_x+dx+from_x*np.cos(a)-from_y*np.sin(a)
        ty=toe_y+dy+from_x*np.sin(a)+from_y*np.cos(a)
        source_y=np.interp(ty,heights+y_offset,heights)
        source_x=tx-np.interp(source_y,heights,x_offset)
        # The heel turns behind the forefoot, around the same planted toe.
        ankle=np.clip((source_y/h-.87)/.065,0.,1.)
        heel=np.clip((toe_y-source_y)/h,0.,.053)
        source_x+=facing*.59*heel*h*ankle
        knots=np.stack((source_x,source_y-top),axis=-1)
        plate=raster_grid(layers[i],xs,ys,knots)
        output.alpha_composite(plate)
    output.alpha_composite(layers[2])
    result=Image.new("RGBA",(w,h+extra)) if extra else image.copy()
    if extra:result.paste(image)
    result.paste(output,(0,top))
    result.info["stride_heading"]=float(facing)
    return result
