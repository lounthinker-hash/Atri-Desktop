"""Build the two-sided page-turning hand and reusable d1 cross-body sleeve sheets."""
from pathlib import Path
import json, math, os, sys
from hashlib import sha256
import numpy as np
from PIL import Image, ImageDraw, ImageChops, ImageFilter
from scipy.ndimage import map_coordinates
from scipy.interpolate import PchipInterpolator
from arm_rig import DIR, ROOT, PATCH, REST_WRIST, artwork, hidden_arm_patch, arm_at, placement, reach_pose
sys.dont_write_bytecode=True
HERE=DIR
BASE=HERE.parent.parent
RC=(785,540,1025,777)
# Original unpainted hand clipped at the cuff seam. Outline in the local CG crop.
OUT=[(28,87),(30,76),(35,66),(44,62),(58,60),(81,57),(88,54),(104,50),(125,46),(145,39),(160,30),(171,26),(179,27),(195,33),(212,42),(215,63),(216,102),(216,143),(215,173),(213,214),(198,217),(186,217),(176,215),(159,210),(144,205),(130,200),(110,196),(95,191),(80,190),(65,188),(58,185),(55,181),(55,177),(58,172),(63,169),(73,167),(89,164),(99,162),(119,161),(142,160),(158,161),(170,164),(148,155),(135,153),(125,150),(113,145),(103,140),(85,136),(69,131),(60,130),(48,135),(35,140),(28,141),(27,129),(24,116),(22,107),(22,100),(23,94)]
GRIP_FRONT=[(-8,-8),(245,-8),(245,172),(202,173),(177,168),(152,155),(128,151),(106,144),(91,136),(60,131),(46,145),(0,150)]
CG=Path(os.environ.get('TEMP',''))/'atri_read_work/cg/ev008bl.png'
FRONT_LOCAL=(-65.,-4.)

def mask_polygon(size,poly):
    a=Image.new('L',(size[0]*3,size[1]*3))
    ImageDraw.Draw(a).polygon([(round(3*x),round(3*y)) for x,y in poly],fill=255)
    return a.resize(size,Image.Resampling.LANCZOS)

def hand_layers():
    cg=Image.open(CG).convert('RGBA').crop(RC)
    allmask=mask_polygon(cg.size,OUT)
    front=mask_polygon(cg.size,GRIP_FRONT)
    # Cool sea illumination to the warm neutral skin used on the standing white dress sprite.
    pix=np.asarray(cg).copy(); rgb=pix[:,:,:3].astype(float)
    rgb[:,:,0]=np.minimum(255,rgb[:,:,0]*.995+1); rgb[:,:,1]=rgb[:,:,1]*.975-1;rgb[:,:,2]=rgb[:,:,2]*.94
    pix[:,:,:3]=rgb.clip(0,255).astype('uint8')
    hand=Image.fromarray(pix)
    a=hand.copy();b=hand.copy()
    a.putalpha(ImageChops.multiply(allmask,ImageChops.invert(front)));b.putalpha(ImageChops.multiply(allmask,front))
    return a,b

FRAGMENTS=hand_layers()
def stamp_hand(wrist,angle,pinch,scale=1.,index=0):
    # Warp only the free ends. The curved fingers and palm attached to the wrist remain continuous.
    part=FRAGMENTS[index].convert('RGBa')
    rad=math.radians(angle); rot=np.array([[math.cos(rad),-math.sin(rad)],[math.sin(rad),math.cos(rad)]])
    extent=np.array([[-100,-59],[68,-59],[68,60],[-100,60]])*scale
    pos=np.array(wrist); corners=extent@rot.T+pos; x,y=np.floor(corners.min(axis=0)-2).astype(int); r,b=np.ceil(corners.max(axis=0)+2).astype(int)
    yy,xx=np.mgrid[y:b,x:r]; uv=(np.stack([xx+.5,yy+.5],axis=-1)-pos)@rot /scale
    # Extend the wrist skin into the oblique bell. Its proximal cut then disappears behind the cuff.
    u_raw=uv[:,:,0].copy()
    extension=20+np.clip(uv[:,:,1],0,40)*.90
    # Invert the monotone linear stretch of the proximal 32 pixels.
    uv[:,:,0]=np.where(u_raw> -32,(u_raw-extension)/(1+extension/32),u_raw)
    # At contact the lower thumb curls under the paper to meet the index finger at the page corner.
    xy=np.zeros_like(uv); xy[:,:,0]=uv[:,:,0]/.365+214;xy[:,:,1]=uv[:,:,1]/.365+137
    for repeat in range(5):
        k=np.clip((180-xy[:,:,0])/125,0,1)
        if index==0:
            dx=-34.*pinch*k;dy=-48.*pinch*k
        else:
            dx=5.*pinch*k;dy=18.*pinch*k
        xy[:,:,0]=uv[:,:,0]/.365+214-dx;xy[:,:,1]=uv[:,:,1]/.365+137-dy
    arr=np.array(part); coords=np.array([xy[:,:,1],xy[:,:,0]])
    out=np.stack([map_coordinates(arr[:,:,c],coords,order=1,mode='constant',cval=0,prefilter=False) for c in range(4)],axis=2)
    im=Image.frombytes('RGBa',(r-x,b-y),out.tobytes()).convert('RGBA'); box=im.getchannel('A').getbbox()
    if not box:return Image.new('RGBA',(1,1)),(x,y)
    return im.crop(box), (x+box[0],y+box[1])

def neutral_fingers(wrist,frame_angle,scale=1):
    # Original resting skin at the right cuff, for the first frames before the palm turns.
    p=artwork().crop((600,943,666,1020))
    aa=np.array(p); rr,gg,bb=aa[:,:,:3].astype(float).transpose(2,0,1)
    skin=(rr>145)&(rr-gg>13)&(gg-bb>=1)
    yy,xx=np.mgrid[943:1020,600:666]
    skin &= ((xx>614)&(yy>956)&(xx>634-.52*(yy-965)))
    a=aa[:,:,3].copy();aa[:,:,3]=np.where(skin,a,0)
    src=Image.fromarray(aa)
    # Rotate around the original pulse, keeping neutral color and three fingertips.
    angle=frame_angle+145; a=math.radians(angle); c=math.cos(a);s=math.sin(a)
    p0=np.array([27.,37.]); size=(140,140); center=np.array([70.,70.]); m=np.array([[c,s],[-s,c]])
    off=p0-m@center
    dst=src.transform(size,Image.Transform.AFFINE,(c,s,off[0],-s,c,off[1]),Image.Resampling.BICUBIC)
    box=dst.getchannel('A').getbbox()
    return dst.crop(box),(round(wrist[0]-70)+box[0],round(wrist[1]-70)+box[1])

def merge_layers(*spec):
    valid=[(im,xy,opacity) for im,xy,opacity in spec if opacity>0]
    if not valid:return Image.new('RGBA',(1,1)),(0,0)
    lo=np.min([xy for im,xy,a in valid],axis=0);hi=np.max([np.array(xy)+im.size for im,xy,a in valid],axis=0)
    im=Image.new('RGBA',tuple(hi-lo))
    for pic,xy,a in valid:
        if a<1:
            pic=pic.copy();pic.putalpha(pic.getchannel('A').point(lambda k:round(k*a)))
        im.alpha_composite(pic,tuple(np.array(xy)-lo))
    return im,tuple(map(int,lo))

def compact(items,name):
    distinct={}; rows=[]
    for im,pos in items:
        box=im.getchannel('A').getbbox()
        if not box:rows.append(None);continue
        im=im.crop(box); pos=(int(pos[0]+box[0]),int(pos[1]+box[1]))
        key=(im.size,sha256(im.tobytes()).digest())
        if key not in distinct:distinct[key]=[im,None]
        rows.append([key,pos])
    mw=max(v[0].width for v in distinct.values())+4; mh=max(v[0].height for v in distinct.values())+4
    cols=max(1,min(8,3000//mw))
    atlas=Image.new('RGBA',(mw*cols,mh*math.ceil(len(distinct)/cols)))
    for j,(im,_) in enumerate(distinct.values()):
        x=j%cols*mw+2;y=j//cols*mh+2; atlas.paste(im,(x,y))
        key=(im.size,sha256(im.tobytes()).digest());distinct[key][1]=[x,y,x+im.width,y+im.height]
    atlas.save(HERE/name,compress_level=8)
    return [None if row is None else {'atlas_rect_px':distinct[row[0]][1],'actor_xy_px':list(row[1])} for row in rows]

def smooth(t):
    t=float(np.clip(t,0,1));return t*t*(3-2*t)

def turn_config(i,corner):
    # Let the bending leaf coast down after it passes the spine; this keeps the elbow within reach.
    goal=np.array([34.,991.])+(np.array(corner)-[180.,276.])*(1706/(390*6))
    # Index tip's inside pad lies at (-65,-4) on the local active hand.
    if i<=15:
        target=goal-np.array(FRONT_LOCAL)
        if i<3:target+=np.array([13.,-16.])*(1-smooth(i/3))
        return target,0.,(smooth(i/3) if i<3 else 1.),i>=3
    if i<=18:
        # Keep the same sheet material point while opening, then release beyond the spine.
        e=smooth((i-15)/3);target=goal-np.array(FRONT_LOCAL)
        return target+[4*e,-3*e],-3*e,1-e,False
    c18=np.array([34.,991.])+(np.array([143.,190.])-[180.,276.])*(1706/(390*6))
    w18=c18-np.array(FRONT_LOCAL)+[4.,-3.]
    e=smooth((i-18)/15); target=w18*(1-e)+np.array([211.,989.])*e;target[1]-=39*math.sin(math.pi*e)
    return target,-3+3*e,0.,False

def build():
    mask,plate=hidden_arm_patch();mask.save(HERE/'rest-arm-erase.png');plate.save(HERE/'rest-side-restore.png')
    page=json.loads((BASE/'page-turn/page.json').read_text('utf-8'))
    seq=page['motion_24fps']['frames']
    configs=[]
    startgoal=np.array([34.,991.])+(np.array(seq[0]['material_corner_inside_px'])-[180.,276.])*(1706/(390*6))-FRONT_LOCAL+np.array([13.,-16.])
    for i in range(17):
        w,l,a=reach_pose(i/16,startgoal);configs.append(('reach',i,w,l,a,0.,False))
    for i,row in enumerate(seq):
        w,a,p,c=turn_config(i,row['material_corner_inside_px'])
        configs.append(('turn',i,w,1.,a,p,c))
    for i in range(17):
        w,l,a=reach_pose(1-i/16,(211,989));configs.append(('return',i,w,l,a,0.,False))
    groups=[[],[],[]];meta=[]
    for kind,i,w,l,a,pin,contact in configs:
        pure=l<.00001
        if pure:
            for g in groups:g.append((Image.new('RGBA',(1,1)),(0,0)))
        else:
            arm,ap=arm_at(w,l,a)
            back,bp=stamp_hand(w,a,pin,.50+.50*l,0)
            front,fp=stamp_hand(w,a,pin,.50+.50*l,1)
            # Lower palm and finger join each other behind the cuff lip.
            mix=smooth(l/.36)
            if mix<1:
                n,np0=neutral_fingers(w,a)
                front,fp=merge_layers((n,np0,1-mix),(front,fp,mix))
                back=back.copy();back.putalpha(back.getchannel('A').point(lambda v:round(v*mix)))
            # Let the garment occlude the cut at the wrist while both fingertip ends stay free.
            for pic,xy in ((back,bp),(front,fp)):
                yy,xx=np.mgrid[xy[1]:xy[1]+pic.height,xy[0]:xy[0]+pic.width]
                rad=math.radians(a); u=(xx-w[0])*math.cos(rad)+(yy-w[1])*math.sin(rad)
                cuff=np.asarray(arm.getchannel('A').crop((xy[0]-ap[0],xy[1]-ap[1],xy[0]-ap[0]+pic.width,xy[1]-ap[1]+pic.height)),float)/255
                strength=np.clip((u+32)/14,0,1)*mix
                pic.putalpha(Image.fromarray((np.asarray(pic.getchannel('A'))*(1-strength*cuff)).round().astype('uint8')))
            groups[0].append((arm,ap));groups[1].append((back,bp));groups[2].append((front,fp))
        meta.append({'stage':kind,'index':i,'original_rest':pure,'wrist_px':[round(float(z),3) for z in w],'angle_deg':round(float(a),3),'lift':round(float(l),5),'pinch':round(float(pin),5),'on_page':bool(contact)})
    for code,name,g in zip(('sleeve','back_finger','front_finger'),('sleeve-atlas.png','back-atlas.png','front-atlas.png'),groups):
        refs=compact(g,name)
        for row,ref in zip(meta,refs): row[code]=ref
    out={'version':1,'actor':'d1/p1','actor_size_px':[666,1706],'fps':24,'coordinate_system':'Unpadded d1/p1 native pixels; image origins at upper left, half-open atlas rectangles. Subtract the crop origin only for a cropped render.',
    'sources':{'sleeve':'fgimage.xp3: アトリ/tatr01_m_715.tlg (d1 p1); assets/sprites/d1-p1-f18e483b6.webp, original sleeve pixels reprojected as a two-link garment.',
    'rest_body':'fgimage.xp3: アトリ/tatr01_m_877.tlg (d1 p2); assets/sprites/d1-p2-4607d8bac.webp shifted (-1,0) to p1 for rear hair and dress under the moving arm.',
    'fingers':'vol1.xp3: ev008bl.png, Adler32 cc211c73, native 3840x2160. Original shoe grip cut at ROI [785,540,1025,777]; individual fingers reposed, resized and color matched.'},
    'rest_patch':{'actor_rect_px':list(PATCH),'erase':'rest-arm-erase.png','restore':'rest-side-restore.png'},
    'atlases':{'sleeve':'sleeve-atlas.png','back_finger':'back-atlas.png','front_finger':'front-atlas.png'},'order':['p1, destination-out rest-arm-erase and original holding-hand erase','rest-side-restore, gripping back palm','registered book bed and original holding front fingers','active sleeve','active back finger','turning paper only','active front finger'],
    'notes':'For original_rest, use untouched viewer-right arm and no right-arm layers. The left supporting hand remains in its established hold. Exact rest at both clip ends. The active right index and thumb pinch on turn samples 3..15, open on samples 16..17, then release the leaf beyond the spine. The page completes its descent by inertia. The wrist is already masked behind the blue cuff edge.',
    'shared_rig':'arm_rig.py: reach_pose(t, goal) -> (wrist, lift, angle_deg); arm_at(wrist,lift,angle_deg) -> (tight native RGBA sleeve, actor_xy). Positive angles are screen-clockwise. At rest draw original actor. A working rotation of 0 points fingers to viewer left. The skin/palm starts at the wrist. Reuse the same erase/restore when targeting a pen.',
    'stages':{'reach':meta[:17],'turn':meta[17:51],'return':meta[51:]}}
    (HERE/'turn.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n','utf-8')

if __name__=='__main__':build()

