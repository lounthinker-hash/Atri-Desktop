"""Bake d1 diary interruption lifts from captured stroke frames and the existing last lift."""
from pathlib import Path
import hashlib
import json
import math
import os

import numpy as np
from PIL import Image
from scipy.interpolate import RBFInterpolator

from read import ARMS, HAND, HELD, HERE, existing_piece, table

PEN = table(HELD / "pen-write/write.json")
WORK = table(HAND / "performance.json")["stages"]["write"]
CHEST = table(ARMS / "manifest.json")
OBJECTS = PEN["frames"]
COUNT = 11
FIRST = 65
LAST = 75
SCALE = 1706/(390*6)
SPINE = np.array([34,991], dtype=float)
ORIGIN = SPINE-SCALE*np.array([180,276])
SHOULDER = np.array(CHEST["shouldersActorPx"]["working"])
STARTS = [item["i"] for item in OBJECTS if item["paper_contact"]]
POSTURES = ("hip", "chest")


def array(pt):
    return np.asarray(pt,dtype=float)


def point(pt):
    return [round(float(n),4) for n in pt]


def rotate(angle):
    a=math.radians(angle)
    return np.array(((math.cos(a),-math.sin(a)),(math.sin(a),math.cos(a))))


def argument(vector):
    return math.degrees(math.atan2(vector[1],vector[0]))


def original_anchors(index,posture):
    row=WORK[index]
    chest=CHEST["stages"]["write"][index]
    shift=array(chest["delta"] if posture=="chest" else (0,0))
    return {
        "shoulderActorPx":point(SHOULDER), "spineActorPx":point(SPINE+shift),
        "bookShiftFromHipPx":point(shift),
        "wristActorPx":point(array(row["wrist_px"])+shift),
        "elbowActorPx":point(chest["workingElbowActorPx"] if posture=="chest" else row["elbow_guide_actor_px"]),
        "gripActorPx":point(array(row["grip_actor_px"])+shift),
        "tipActorPx":point(array(row["tip_actor_px"])+shift),
        "handAngleCw":row["angle_cw"],
    }


def geometry(start,step):
    idx=FIRST+step
    original=start if step==0 else idx
    pen=dict(OBJECTS[original])
    pen.update(ink_tile=OBJECTS[start]["ink_tile"],paper_contact=(step==0),
               phase="contact" if step==0 else ("lift" if step<=6 else "retract"))
    pen["stroke"]=OBJECTS[start]["stroke"] if step==0 else None
    if start==FIRST or step in (0,COUNT-1):
        return {"sample":step,"sourceWriteFrame":original,"pen":pen,
                "anchors":{s:original_anchors(original,s) for s in POSTURES}}
    a,zero,donor=OBJECTS[start],OBJECTS[FIRST],OBJECTS[idx]
    t=step/(COUNT-1)
    ease=t*t*(3-2*t)
    tail=1-ease
    tip=array(donor["tip"])+tail*(array(a["tip"])-array(zero["tip"]))
    yprogress=(donor["tip"][1]-zero["tip"][1])/(OBJECTS[LAST]["tip"][1]-zero["tip"][1])
    tip[1]=a["tip"][1]+yprogress*(OBJECTS[LAST]["tip"][1]-a["tip"][1])
    tilt=donor["rotation_cw"]+tail*(a["rotation_cw"]-zero["rotation_cw"])
    distance=donor["grip_distance"]+tail*(a["grip_distance"]-zero["grip_distance"])
    absolute=math.radians(tilt+PEN["pen"]["stowed_tilt_from_up_degrees"])
    grip=tip+distance*np.array([math.sin(absolute),-math.cos(absolute)])
    pen.update(tip=point(tip),grip=point(grip),rotation_cw=round(tilt,4),grip_distance=round(distance,4))
    hand_angle=WORK[idx]["angle_cw"]+tail*(WORK[start]["angle_cw"]-WORK[FIRST]["angle_cw"])
    wrist=ORIGIN+grip*SCALE-rotate(hand_angle)@np.array([-70.,6.])
    anchors={}
    for posture in POSTURES:
        src0=original_anchors(FIRST,posture);capt=original_anchors(start,posture);dst=original_anchors(idx,posture)
        shift=array(dst["bookShiftFromHipPx"])
        elbow=array(dst["elbowActorPx"])+tail*(array(capt["elbowActorPx"])-array(src0["elbowActorPx"]))
        anchors[posture]=dict(dst,wristActorPx=point(wrist+shift),elbowActorPx=point(elbow),
                              gripActorPx=point(ORIGIN+grip*SCALE+shift),tipActorPx=point(ORIGIN+tip*SCALE+shift),
                              handAngleCw=round(hand_angle,4))
    return {"sample":step, "pen":pen, "anchors":anchors}


def hand_warp(part,src,dst):
    im,xy=part
    from_wrist=array(src["wristActorPx"]);to_wrist=array(dst["wristActorPx"])
    mat=rotate(dst["handAngleCw"]-src["handAngleCw"])
    corners=np.array([[0,0],[im.width,0],[0,im.height],im.size])+xy
    warped=(corners-from_wrist)@mat.T+to_wrist
    lo=np.floor(warped.min(axis=0)).astype(int)-2;hi=np.ceil(warped.max(axis=0)).astype(int)+2
    inv=mat.T
    bias=inv@(lo-to_wrist)+from_wrist-xy
    coeff=(inv[0,0],inv[0,1],bias[0],inv[1,0],inv[1,1],bias[1])
    result=im.convert("RGBa").transform(tuple(hi-lo),Image.Transform.AFFINE,coeff,
                                        Image.Resampling.BICUBIC).convert("RGBA")
    return result,lo


def sleeve_warp(part,src,dst):
    im,xy=part
    xy=array(xy)
    e,w=array(src["elbowActorPx"]),array(src["wristActorPx"])
    ee,ww=array(dst["elbowActorPx"]),array(dst["wristActorPx"])
    elbow_angle=argument(ee-SHOULDER)-argument(e-SHOULDER)
    cuff_angle=argument(ww-ee)-argument(w-e)
    source=[];target=[]
    def joint(a,b,offsets,angle):
        r=rotate(angle)
        for delta in offsets:
            source.append(a+array(delta));target.append(b+r@array(delta))
    joint(SHOULDER,SHOULDER,[(0,0),(-25,-14),(26,-12),(0,32)],0)
    joint(e,ee,[(0,0),(-27,0),(27,6),(0,24)],elbow_angle)
    joint(w,ww,[(0,0),(-22,0),(22,0),(0,-24),(0,23)],cuff_angle)
    source=array(source);target=array(target)
    delta=target-source
    lo=np.floor(xy+np.minimum(delta.min(axis=0),0)).astype(int)-12
    hi=np.ceil(xy+im.size+np.maximum(delta.max(axis=0),0)).astype(int)+12
    inv=RBFInterpolator(target,source-target,kernel="thin_plate_spline",smoothing=.05)
    xs=np.unique(np.r_[np.arange(lo[0],hi[0],10),hi[0]])
    ys=np.unique(np.r_[np.arange(lo[1],hi[1],10),hi[1]])
    xx,yy=np.meshgrid(xs,ys);q=np.stack((xx,yy),axis=-1)
    uv=q+inv(q.reshape(-1,2)).reshape(q.shape)-xy
    mesh=[]
    for i in range(len(ys)-1):
        for j in range(len(xs)-1):
            box=(xs[j]-lo[0],ys[i]-lo[1],xs[j+1]-lo[0],ys[i+1]-lo[1])
            quad=(*uv[i,j],*uv[i+1,j],*uv[i+1,j+1],*uv[i,j+1])
            mesh.append((box,quad))
    return im.convert("RGBa").transform(tuple(hi-lo),Image.Transform.MESH,mesh,
                                        Image.Resampling.BICUBIC).convert("RGBA"),lo


def mix(a,b,t):
    lo=np.minimum(a[1],b[1]).astype(int)
    hi=np.maximum(np.add(a[1],a[0].size),np.add(b[1],b[0].size)).astype(int)
    def placed(part):
        im=Image.new("RGBa",tuple(hi-lo))
        im.paste(part[0].convert("RGBa"),tuple(part[1]-lo))
        return im
    tile=Image.blend(placed(a),placed(b),t).convert("RGBA")
    bb=tile.getchannel("A").getbbox()
    return tile.crop(bb),lo+bb[:2]


class AtlasWriter:
    def __init__(self):
        self.sequence=0;self.files={};self.reset()

    def reset(self):
        self.image=Image.new("RGBA",(2048,2048));self.x=self.y=1;self.height=0
        self.right=self.bottom=0;self.sequence+=1
        self.filename=f"parts-{self.sequence:02d}.webp"

    def flush(self):
        if self.bottom:
            tile=self.image.crop((0,0,self.right+1,self.bottom+1));p=HERE/self.filename
            tile.save(p,format="WEBP",lossless=True,method=3)
            self.files[self.filename]=hashlib.sha256(p.read_bytes()).hexdigest()

    def insert(self,im,origin):
        if self.x+im.width+1>2048:
            self.y+=self.height+2;self.x=1;self.height=0
        if self.y+im.height+1>2048:self.flush();self.reset()
        self.image.paste(im,(self.x,self.y))
        rect=[self.x,self.y,self.x+im.width,self.y+im.height]
        self.right=max(self.right,rect[2]);self.bottom=max(self.bottom,rect[3])
        self.x=rect[2]+2;self.height=max(self.height,im.height)
        return {"file":self.filename,"rect":rect,"xy":[int(x) for x in origin]}


def bake():
    atlas=AtlasWriter();starts={}
    for start in STARTS:
        rows=[]
        for step in range(COUNT):
            row=geometry(start,step);rows.append(row)
            if "sourceWriteFrame" in row:continue
            donor=FIRST+step
            t=step/(COUNT-1);ease=t*t*(3-2*t)
            row["art"]={}
            for name,posture in [("far","hip"),("near","hip"),("sleeve","hip"),("sleeve","chest")]:
                warp=sleeve_warp if name=="sleeve" else hand_warp
                registered=[]
                for index in [start,donor]:
                    registered.append(warp(existing_piece(name,index,posture),
                                           original_anchors(index,posture),row["anchors"][posture]))
                im,xy=mix(*registered,ease)
                row["art"][posture+"Sleeve" if name=="sleeve" else name]=atlas.insert(im,xy)
        starts[str(start)]={"inkTile":OBJECTS[start]["ink_tile"],"stroke":OBJECTS[start]["stroke"],"frames":rows}
        print("Baked",start,flush=True)
    atlas.flush()
    doc={"schemaVersion":1,"id":"d1-diary-captured-lift","fps":24,"starts":starts,"sha256":atlas.files,
         "sourcePaths":{"hand":"../../action-hand/write/performance.json","pen":"../../pen-write/write.json",
                        "chestArms":"../../writing-posture/arms/manifest.json"},
         "coordinates":"Pen positions use 432×348 book pixels. Hand, sleeve and actor anchors use native unpadded d1/p1 666×1706. Crop bounds are half-open.",
         "sequence":"Show frames[0] only as the already displayed held frame, then frames[1:11] at 24fps. Frame 10 has the exact write[75] hand/pen posture and captured ink. Continue using write[76:102], then return[0:17], always with the captured inkTile. Suppress page stamping, writing side effects, and old clip callbacks after interruption.",
         "layers":"Support and page, frozen ink, support front, far fingers, loose pen, near fingers, working sleeve. Preserve the original rest erase while the sleeve exists. Only the existing final return pose gives back the original rest arm.",
         "sourceMethod":"Three-quarter chest and original hip use the same held pen geometry. Samples 1–9 re-register real finger/arm textures from the captured pose and the original last lift; shoulder, elbow and wrist have independent sleeve controls. The original write[65] already enters this lift, so all 11 of its samples use exact original body parts. The page never advances.",
         "sourceWriteFrame":"Reuse only the referenced hand/sleeve and pen pose. The row's own pen.ink_tile remains authoritative even on fully reused poses.",
         "reader":"read.py parts(start,sample,posture) returns far/near/sleeve, pen, anchors."}
    dest=HERE/"manifest.json";temp=HERE/"manifest.tmp"
    temp.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    os.replace(temp,dest)


if __name__=="__main__":
    bake()
