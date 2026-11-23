"""Offline, native-pixel d1/p1 working sleeve for the diary hand and pen hand."""
from functools import lru_cache
from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

DIR = Path(__file__).resolve().parent
ROOT = DIR.parents[4]
PATCH = (405, 515, 666, 1125)
SHOULDER = np.array([450., 548.])
REST_WRIST = np.array([627., 980.])
REST = np.array([
    [[427,543],[461,525]], [[437,566],[476,548]],
    [[445,625],[492,600]], [[447,686],[521,650]],
    [[456,751],[546,710]], [[468,802],[568,765]],
    [[478,859],[590,820]], [[489,909],[606,848]],
    [[496,947],[610,862]], [[502,965],[631,895]],
    [[510,998],[641,923]], [[517,1014],[643,933]]],dtype=float)
BOW = [0,0,0,0,0,1,4,13,29,30,28,28]
ALONG = np.array([0,.05,.17,.30,.43,.54,.65,.735,.80,.85,.96,1.])
ACROSS = np.linspace(0,1,13)

def rgba(path):
    with Image.open(path) as im:
        return im.convert('RGBA')

@lru_cache(maxsize=1)
def artwork():
    return rgba(ROOT/'assets/sprites/d1-p1-f18e483b6.webp')

def crops(im, rect):
    return im.crop(rect)

def hidden_arm_patch():
    """Erase only the original outside-arm area, then restore original p2 back hair and dress."""
    x,y,r,b=PATCH
    # Smooth join inside the solid dress. The outer edge extends past all resting fingers.
    mask=Image.new('L',(666,1706))
    ImageDraw.Draw(mask).polygon([(448,532),(477,525),(512,551),(565,611),(666,645),(676,1124),(526,1124),(507,1037),(499,987),(482,933),(472,874),(458,799),(445,725),(439,660),(435,590)],fill=255)
    mask=mask.filter(ImageFilter.GaussianBlur(1.3)).crop(PATCH)
    p2=rgba(ROOT/'assets/sprites/d1-p2-4607d8bac.webp')
    plate=p2.crop((x+1,y,r+1,b))
    # Raised p2's overhead sleeve occurs above this point. Blend down into its clean side.
    a=np.asarray(mask).copy()
    top=np.minimum(np.clip((np.arange(y,b)-548)/66.,0,1),np.clip((b-np.arange(y,b))/45.,0,1))
    a=(a*top[:,None]).round().astype('uint8')
    mask=Image.fromarray(a)
    plate.putalpha(Image.fromarray((np.asarray(plate.getchannel('A'),float)*a/255).round().astype('uint8')))
    return mask,plate

def strip_coords():
    rows=[]
    for (l,r),bow in zip(REST,BOW):
        line=l[None,:]*(1-ACROSS[:,None])+r[None,:]*ACROSS[:,None]
        line[:,1]+=bow*np.sin(np.pi*ACROSS)
        rows.append(line)
    return np.array(rows)

def unit(p):
    return p/max(np.linalg.norm(p),1e-6)

def path_points(wrist, angle_deg):
    # Two-link arm in front of the chest. The elbow remains on the viewer's right.
    wrist=np.array(wrist,float)
    tip=np.array([-math.cos(math.radians(angle_deg)),-math.sin(math.radians(angle_deg))])
    finish=wrist-6*tip
    v=finish-SHOULDER; length=np.linalg.norm(v); along=unit(v)
    reach_norm=np.array([along[1],-along[0]])
    upper,lower=255.,275.
    proj=(length*length+upper*upper-lower*lower)/(2*max(length,1.))
    offset=math.sqrt(max(0.,upper*upper-proj*proj))
    elbow=SHOULDER+along*proj+reach_norm*min(offset,137.)
    start=SHOULDER
    # Quadratic round-off around the elbow prevents a pinched edge at full extension.
    bend_in=elbow+.29*(start-elbow);bend_out=elbow+.29*(finish-elbow)
    def at(t):
        if t<.34:return start+(bend_in-start)*(t/.34)
        if t<.65:
            k=(t-.34)/.31
            return (1-k)**2*bend_in+2*(1-k)*k*elbow+k*k*bend_out
        k=(t-.65)/.35
        return (1-k)*bend_out+k*finish
    return at

def sleeve_mesh(wrist,lift,angle_deg):
    src=strip_coords(); rows=[]; path=path_points(wrist,angle_deg)
    half=np.array([20,26,33,40,46,49,51,53,57,58,59,59],float)
    for i,t in enumerate(ALONG):
        center=path(t)
        tangent=unit(path(min(1,t+.012))-path(max(0,t-.012)))
        normal=np.array([tangent[1],-tangent[0]])
        # Preserve the bell and let the near lip advance past the far lip.
        width=half[i]
        dst=center+(2*ACROSS[:,None]-1)*width*normal
        dst+=np.sin(np.pi*ACROSS)[:,None]*min(10.,BOW[i]*.40)*tangent
        rows.append(dst)
    dst=np.array(rows)
    # The caller always supplies the current socket, including in intermediate poses.
    move=(np.asarray(wrist)-REST_WRIST)*(1-lift)
    return src*(1-lift)+dst*lift+ALONG[:,None,None]*move[None,None,:]

def _tri(dst,src,tex,canvas,origin):
    a,b,c=dst
    mat=np.column_stack((b-a,c-a))
    det=np.linalg.det(mat)
    if abs(det)<.03:return
    low=np.maximum(np.floor(dst.min(axis=0)-origin).astype(int)-1,0)
    high=np.minimum(np.ceil(dst.max(axis=0)-origin).astype(int)+2,canvas.shape[1::-1])
    if np.any(high<=low):return
    yy,xx=np.mgrid[low[1]:high[1],low[0]:high[0]]
    coords=np.stack((xx+.5+origin[0]-a[0],yy+.5+origin[1]-a[1]),axis=-1)
    uv=coords@np.linalg.inv(mat).T
    ok=(uv[...,0]>=-.002)&(uv[...,1]>=-.002)&(uv.sum(axis=2)<=1.002)
    if not np.any(ok):return
    sample=src[0]+uv[ok]@np.array([src[1]-src[0],src[2]-src[0]])
    # Bilinear texture lookup in original artwork.
    floor=np.floor(sample).astype(int); frac=sample-floor
    x,y=floor.T
    x=np.clip(x,0,tex.shape[1]-2);y=np.clip(y,0,tex.shape[0]-2)
    ux,uy=frac[:,0,None],frac[:,1,None]
    col=(tex[y,x]*(1-ux)+tex[y,x+1]*ux)*(1-uy)+(tex[y+1,x]*(1-ux)+tex[y+1,x+1]*ux)*uy
    canvas[yy[ok],xx[ok]]=np.round(col).astype('uint8')

def arm_at(wrist,lift=1.0,angle_deg=0.0):
    """Tight RGBA sleeve, (x,y) on the unpadded 666x1706 actor. Wrist is the skin/socket point."""
    src=strip_coords(); dst=sleeve_mesh(wrist,lift,angle_deg)
    origin=np.floor(dst.min(axis=(0,1))-3).astype(int)
    size=np.ceil(dst.max(axis=(0,1))+3).astype(int)-origin
    pixels=np.zeros((size[1],size[0],4),dtype='uint8')
    tex=np.asarray(artwork()).astype(float)
    for i in range(src.shape[0]-1):
        for j in range(src.shape[1]-1):
            for corners in [[(i,j),(i,j+1),(i+1,j)],[(i,j+1),(i+1,j+1),(i+1,j)]]:
                ii,jj=zip(*corners)
                _tri(dst[ii,jj],src[ii,jj],tex,pixels,origin)
    im=Image.fromarray(pixels)
    if lift>0:
        # Preserve d1's pale cuff piping as it curves around the wrist opening.
        # The oblique neutral texture maps its stripe to a subpixel in frontal views.
        overlay=Image.new('RGBA',tuple(size*2))
        draw=ImageDraw.Draw(overlay)
        def lip(q):
            return [tuple(2*(point-origin)) for point in dst[-2]*(1-q)+dst[-1]*q]
        edge=list(lip(.27))+list(reversed(lip(.56)))
        draw.polygon(edge,fill=(206,228,226,round(242*min(1,lift*2))))
        draw.line(lip(.95),fill=(36,72,92,round(210*min(1,lift*2))),width=3)
        im.alpha_composite(overlay.resize(tuple(size),Image.Resampling.LANCZOS))
    return im, tuple(map(int,origin))

def placement(wrist,local,angle_deg):
    a=math.radians(angle_deg); u,v=local
    return (float(wrist[0]+u*math.cos(a)-v*math.sin(a)),float(wrist[1]+u*math.sin(a)+v*math.cos(a)))

def reach_pose(t, goal=(211.,989.)):
    """16 intervals/24 fps from untouched rest to a specified working wrist; reverse to return."""
    t=float(np.clip(t,0,1)); e=t*t*(3-2*t)
    wrist=REST_WRIST*(1-e)+np.array(goal)*e
    wrist[1]-=83*math.sin(math.pi*e)
    angle=-145*(1-e)
    return tuple(map(float,wrist)),e,angle

