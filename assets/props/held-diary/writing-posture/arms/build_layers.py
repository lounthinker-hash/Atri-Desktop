"""Offline prep for the two sleeves of the d1/p1 writing pose."""
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
HELD = HERE.parents[1]
ROOT = HELD.parents[2]
P1 = ROOT / 'assets/sprites/d1-p1-f18e483b6.webp'
P2 = ROOT / 'assets/sprites/d1-p2-4607d8bac.webp'
OLD = HELD / 'action-hand/write'
TURN = HELD / 'action-hand/turn'
RECT = (-110, 500, 675, 1130)
TEX_SHIFT = np.array((161., -166.))
SUPPORT_WRIST = np.array((75., 959.))
WORK_ROOT = np.array((450., 548.))
SUPPORT_ROOT = np.array((257., 533.))
ACROSS = np.linspace(0, 1, 7)
ALONG = np.array([0,.06,.17,.28,.40,.52,.64,.74,.80,.87,.96,1.])
LEFT = np.array([
 [(247,525),(263,538)],[(228,552),(266,559)],[(211,598),(251,615)],
 [(191,647),(241,665)],[(171,700),(224,716)],[(147,761),(207,779)],
 [(119,815),(184,841)],[(97,872),(168,895)],[(87,898),(157,930)],
 [(75,918),(150,950)],[(63,936),(130,1008)],[(63,943),(125,1024)]],float)
LEFT_ELBOW = np.array((169.,768.))
FINAL_ELBOW = np.array((143.,868.))


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def smooth(t):
    t = float(np.clip(t, 0, 1))
    return t*t*(3-2*t)


def geometry(stage, row):
    index = row['local_frame']
    if stage == 'write':
        t = 1.
    elif stage == 'turn_to_pen':
        t = smooth(index/14)
    else:
        t = smooth((index if stage == 'reach' else 16-index)/16)
    shift = np.rint(TEX_SHIFT*t + (0., -12.*math.sin(math.pi*t))).astype(int)
    old_w = np.array(row['wrist_px'])
    old_e = np.array(row.get('elbow_guide_actor_px', WORK_ROOT+.53*(old_w-WORK_ROOT)+[54.,39.]))
    work_elbow = old_e + t*np.array((88.,-14.))
    return dict(weight=t, delta=shift.tolist(), spineActorPx=(shift+[34,991]).tolist(),
                supportWristActorPx=(SUPPORT_WRIST+shift).tolist(),
                supportElbowActorPx=np.round((1-t)*LEFT_ELBOW+t*FINAL_ELBOW,3).tolist(),
                workingWristActorPx=np.round(old_w+shift,3).tolist(),
                workingElbowActorPx=np.round(work_elbow,3).tolist(),
                oldElbow=old_e.tolist())


def left_repair():
    # All p2 source anatomy uses the same standing d1 body, with its arms overhead.
    box=(0,515,288,1070)
    mask=Image.new('L',(666,1706))
    ImageDraw.Draw(mask).polygon([(239,519),(275,535),(270,592),(252,648),
        (235,711),(208,795),(182,870),(157,950),(133,1020),(129,1057),
        (0,1057),(0,708),(149,699),(188,621),(210,553)],fill=255)
    mask=mask.filter(ImageFilter.GaussianBlur(1.2)).crop(box)
    a=np.asarray(mask).astype(float)
    y=np.arange(box[1],box[3])
    ramp=np.clip((y-544)/52,0,1)*np.clip((1062-y)/24,0,1)
    mask=Image.fromarray(np.rint(a*ramp[:,None]).astype('uint8'))
    with Image.open(P2) as src:
        plate=src.convert('RGBA').crop((box[0]+1,box[1],box[2]+1,box[3]))
    alpha=np.asarray(plate.getchannel('A'),float)*np.asarray(mask)/255
    plate.putalpha(Image.fromarray(np.rint(alpha).astype('uint8')))
    mask.save(HERE/'support-erase.png',optimize=True);plate.save(HERE/'support-repair.png',optimize=True)
    return box


@lru_cache(maxsize=1)
def texture():
    with Image.open(P1) as im:
        return np.asarray(im.convert('RGBA')).astype(float)


def unit(v):
    return v/max(.001,np.linalg.norm(v))


def support_mesh(t, shift):
    src=LEFT[:,0,None,:]*(1-ACROSS[None,:,None])+LEFT[:,1,None,:]*ACROSS[None,:,None]
    root=src[0].mean(axis=0)
    # The cloth opening turns with the forearm; the skin socket stays on the same palm.
    final=SUPPORT_WRIST+TEX_SHIFT+np.array((-22.,-25.))
    elbow=FINAL_ELBOW
    bend0=elbow+.16*(root-elbow);bend1=elbow+.32*(final-elbow)
    def point(s):
        if s<.43:return root+(bend0-root)*(s/.43)
        if s<.72:
            p=(s-.43)/.29
            return (1-p)**2*bend0+2*p*(1-p)*elbow+p*p*bend1
        return bend1+(final-bend1)*((s-.72)/.28)
    centers=src.mean(axis=1)
    now=centers.copy()
    for i,s in enumerate(ALONG):
        now[i]=(1-t)*centers[i]+t*point(s)+s*(shift-t*TEX_SHIFT)
    dst=[]
    for i,s in enumerate(ALONG):
        oldvec=src[i,-1]-src[i,0]
        if i:
            tangent=unit(now[min(len(now)-1,i+1)]-now[i-1])
            normal=np.array([tangent[1],-tangent[0]])
            prog=min(t*1.25,1.)
            phi=math.atan2(oldvec[1],oldvec[0])
            spin=(math.atan2(normal[1],normal[0])-phi+np.pi)%(2*np.pi)-np.pi
            direction=np.array((math.cos(phi+prog*spin),math.sin(phi+prog*spin)))
        else:direction=unit(oldvec)
        vec=direction*np.linalg.norm(oldvec)*(1-.09*t*np.sin(s*np.pi))
        dst.append(now[i]+(ACROSS[:,None]-.5)*vec)
    return src,np.asarray(dst)


def support_texture(t,shift):
    import importlib.util
    global _rig
    if '_rig' not in globals():
        spec=importlib.util.spec_from_file_location('atri_posture_source_rig',TURN/'arm_rig.py')
        _rig=importlib.util.module_from_spec(spec);spec.loader.exec_module(_rig)
    src,dst=support_mesh(t,shift)
    lo=np.floor(dst.min(axis=(0,1))-2).astype(int);hi=np.ceil(dst.max(axis=(0,1))+2).astype(int)
    arr=np.zeros((hi[1]-lo[1],hi[0]-lo[0],4),'uint8')
    for i in range(len(src)-1):
        for j in range(src.shape[1]-1):
            for ix in [[(i,j),(i,j+1),(i+1,j)],[(i,j+1),(i+1,j+1),(i+1,j)]]:
                ii,jj=zip(*ix)
                _rig._tri(dst[ii,jj],src[ii,jj],texture(),arr,lo)
    return Image.fromarray(arr),lo


@lru_cache(maxsize=4)
def old_image(name):
    with Image.open(OLD/name) as im:return im.convert('RGBA')


def work_texture(row, info, perf):
    # Retarget the live sleeve through its native textile strip. Each frame keeps its
    # actual inked wrist/cuff tile, and the underlying d1 sleeve fills antialiased tapers.
    piece=row['sleeve']
    if piece is None:
        info['workingElbowActorPx']=info['oldElbow']
        return None
    live=old_image(perf['atlases']['sleeve'][piece['image']]).crop(piece['rect'])
    origin=np.array(piece['xy'])
    shift=np.asarray(info['delta']);wrist=np.asarray(row['wrist_px'])
    t=info['weight'];stage=row['stage'];index=row['local_frame']
    lift=(smooth((index if stage=='reach' else 16-index)/16) if stage in ('reach','return') else 1.)
    if not shift.any():
        info['workingElbowActorPx']=info['oldElbow']
        return live,origin
    if '_rig' not in globals():support_texture(0.,np.zeros(2))
    source=_rig.strip_coords()
    live_source=_rig.sleeve_mesh(wrist,lift,row['angle_cw'])
    target=_rig.sleeve_mesh(wrist+shift,lift,row['angle_cw'])
    base=target.mean(axis=1)
    goal=np.asarray(info['oldElbow'])+np.array((82.,-17.))*t
    adjust=(goal-base[5])*.74*t
    weight=np.sin(np.pi*_rig.ALONG)**2/(math.sin(np.pi*_rig.ALONG[5])**2)
    moved=base+weight[:,None]*adjust
    for i in range(len(base)):
        dv=base[min(i+1,len(base)-1)]-base[max(i-1,0)]
        dn=moved[min(i+1,len(base)-1)]-moved[max(i-1,0)]
        turn=(math.atan2(dn[1],dn[0])-math.atan2(dv[1],dv[0])+np.pi)%(2*np.pi)-np.pi
        turn*=min(1.,i/2.,(len(base)-1-i)/2.)
        co,si=np.cos(turn),np.sin(turn)
        target[i]=(target[i]-base[i])@np.array([[co,si],[-si,co]])+moved[i]
    info['workingElbowActorPx']=np.round(moved[5],3).tolist()
    lo=np.floor(target.min(axis=(0,1))-3).astype(int);hi=np.ceil(target.max(axis=(0,1))+3).astype(int)
    body=np.zeros((hi[1]-lo[1],hi[0]-lo[0],4),'uint8');details=np.zeros_like(body)
    raw=np.asarray(live,dtype=float)
    # Pad the live crop (the remapper clamps bilinear neighbours at texture borders).
    raw=np.pad(raw,((3,3),(3,3),(0,0)))
    for i in range(source.shape[0]-1):
        for j in range(source.shape[1]-1):
            for ix in [[(i,j),(i,j+1),(i+1,j)],[(i,j+1),(i+1,j+1),(i+1,j)]]:
                ii,jj=zip(*ix)
                _rig._tri(target[ii,jj],source[ii,jj],texture(),body,lo)
                _rig._tri(target[ii,jj],live_source[ii,jj]-origin+3,raw,details,lo)
    im=Image.fromarray(body);im.alpha_composite(Image.fromarray(details))
    return im,lo


def bake():
    perf=read(OLD/'performance.json')
    if '_rig' not in globals():support_texture(0.,np.zeros(2))
    entry=perf['stages']['write'][0]
    endpoint_offset=np.asarray(entry['elbow_guide_actor_px'])-_rig.sleeve_mesh(
        np.asarray(entry['wrist_px']),1,entry['angle_cw']).mean(axis=1)[5]
    out={'supportPatchRect':list(left_repair()),'patchRect':list(RECT),'stages':{}}
    for key,rows in perf['stages'].items():
        out['stages'][key]=[]
        for row in rows:
            info=geometry(key,row)
            if 'elbow_guide_actor_px' not in row:
                i=row['local_frame']
                lift=smooth((i if key=='reach' else 16-i)/16) if key in ('reach','return') else 1.
                center=_rig.sleeve_mesh(np.asarray(row['wrist_px']),lift,row['angle_cw']).mean(axis=1)[5]
                info['oldElbow']=np.round(center+endpoint_offset*lift,3).tolist()
            out['stages'][key].append(info)
    return perf,out


def publish():
    from hashlib import sha256
    perf,meta=bake();chunks=[];sheet=None;x=y=shelf=0
    read_pen=read(HELD/'pen-write/write.json')['frames']
    support_cache={};work_cache={}
    meta.update(schemaVersion=1, sourceCanvas=[666,1706],fps=24,
        coordinates='未形变 d1/p1 母版像素，左上为原点。rect / patchRect 均为半开 [左,上,右,下)；xy 为图层在母版上的左上坐标。路径相对此清单。每一帧原始手、笔、纸的同一原生整像素位移见 rigidOffsetActorPx。',
        shouldersActorPx={'support':SUPPORT_ROOT.tolist(),'working':WORK_ROOT.tolist()},
        sources={'actor':'../../../../sprites/d1-p1-f18e483b6.webp','bodyRestore':'../../../../sprites/d1-p2-4607d8bac.webp',
            'handPerformance':'../../action-hand/write/performance.json','penAndInk':'../../pen-write/write.json',
            'originalHeldGrip':'../../manifest.json','rightRepair':'../../action-hand/turn/turn.json#rest_patch',
            'handPerformanceSha256':sha256((OLD/'performance.json').read_bytes()).hexdigest(),
            'penAndInkSha256':sha256((HELD/'pen-write/write.json').read_bytes()).hexdigest()},
        supportRepair={'erase':'support-erase.png','restore':'support-repair.png','rect':meta['supportPatchRect'],
            'weight':'min(1,6*weight)，先 DestinationOut 再绘 p2 移位侧发与裙片。所有帧另使用原托手局部擦除。'},
        drawOrder=['在传入动态 d1/p1 立绘上用局部遮罩去除原臂和手；铺项目现有右侧修补及本组左侧 p2 同装侧发/裙。','新托书袖、原后掌、书/累积墨、原前指/短袖缘。','原活动手远指、真实活动笔/笔套、原活动手近指、新工作袖。'],
        compositing='composite.py: render_layers(stage,index,page) -> (L erase, RGBA restore, RGBA overlay)，同 patchRect。render_native(actor,stage,index,page,pad_x=112) 保留传入的头和身体；pad_x=112 时输出 890×1706，原点 (112,0)。',
        motion='24 fps。reach 17 帧 / write 102 帧 / return 17 帧。先前接单页可用 turn_to_pen 15 帧。起收对称，写字持续保持书脊 [195,825]；落笔、提笔、停笔和墨迹完全来自 propFrame。',
        art='本组对原游戏 d1/p1 左工作袖做双段变形，以站姿 d1/p2 同装和同侧长发修补遮住的裙侧。右工作袖采样原 102 帧工作袖与 d1/p1 布面，通过肩、肘、腕重新定形。执笔手沿用既有前/后指合成。')
    def finish():
        if sheet is None:return
        height=max(1,y+shelf+2);width=sheet.width
        image=sheet.crop((0,0,width,height))
        path=HERE/('sleeves-%02d.webp'%(len(chunks)+1));image.save(path,lossless=True,method=2,exact=True)
        chunks.append({'file':path.name,'size':list(image.size),'sha256':sha256(path.read_bytes()).hexdigest()})
    def pack(obj,opacity=1.):
        nonlocal sheet,x,y,shelf
        if obj is None:return None
        tile,xy=obj
        if opacity<1:
            tile=tile.copy();tile.putalpha(tile.getchannel('A').point(lambda a:round(a*opacity)))
        bb=tile.getchannel('A').getbbox()
        if not bb:return None
        tile=tile.crop(bb);xy=np.asarray(xy)+bb[:2]
        if sheet is None:sheet=Image.new('RGBA',(2048,2048));x=y=2;shelf=0
        if x+tile.width+2>sheet.width:x=2;y+=shelf+3;shelf=0
        if y+tile.height+2>sheet.height:
            finish();sheet=Image.new('RGBA',(2048,2048));x=y=2;shelf=0
        sheet.paste(tile,(x,y))
        loc={'file':'sleeves-%02d.webp'%(len(chunks)+1),'rect':[x,y,x+tile.width,y+tile.height],'xy':xy.astype(int).tolist()}
        shelf=max(shelf,tile.height);x+=tile.width+3
        return loc
    for stage,rows in perf['stages'].items():
        for i,row in enumerate(rows):
            info=meta['stages'][stage][i];t=info['weight'];shift=np.array(info['delta'])
            sk=(tuple(shift),round(t,8))
            if sk not in support_cache:
                support_cache[sk]=pack(support_texture(t,shift),min(1.,t*6)) if t else None
            info['supportSleeve']=support_cache[sk]
            work_key=(str(row['sleeve']),tuple(row['wrist_px']),row['angle_cw'],tuple(info['oldElbow']),tuple(shift),round(t,8))
            if work_key not in work_cache:
                tile=work_texture(row,info,perf)
                work_cache[work_key]=(pack(tile),info['workingElbowActorPx'])
            info['workSleeve'],info['workingElbowActorPx']=work_cache[work_key]
            info['rigidOffsetActorPx']=info['delta'];info['propFrame']=row['prop_frame']
            info['stowed']=read_pen[row['prop_frame']]['stowed']
            info['barrelContact']=read_pen[row['prop_frame']]['barrel_contact']
            info['paperContact']=row['paper_contact'];info['stroke']=row['stroke']
            info['inkTile']=read_pen[row['prop_frame']]['ink_tile']
            for old,new in [('tip_actor_px','tipActorPx'),('grip_actor_px','gripActorPx')]:
                info[new]=None if row[old] is None else np.round(row[old]+shift,3).tolist()
            bo=shift+np.array((34.,991.))-.729059829059829*np.array((180,276))
            info['bookOriginActorPx']=np.round(bo,4).tolist()
            info['supportElbowActorPx']=np.round(info['supportElbowActorPx'],3).tolist()
            info.pop('oldElbow')
        print(stage,len(rows),flush=True)
    finish()
    meta['atlases']=chunks
    meta['artRevision']=2
    meta['supportContact']=paint_support_contact()
    for k in ('rear','front'):
        name=meta['supportContact'][k]
        meta['supportContact'][k+'Sha256']=sha256((HERE/name).read_bytes()).hexdigest()
    (HERE/'manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n','utf-8')
    print('published',len(chunks),'atlases',flush=True)



def paint_support_contact():
    """Two small retouch plates confined to the palm and the underside of the binding."""
    import io
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    bounds=(162,796,254,844)
    parts={
        'support-contact-back.png': """
        <path d="M194 825 Q206 818 222 816 L240 816 Q243 818 238 823 C229 830 221 837 211 839 Q203 840 196 836 Z" fill="#547f92" stroke="#415b70" stroke-width="1.2"/>
        <path d="M196 823 L236 818 Q238 821 231 824 Q220 835 211 836 L200 835 Z" fill="#39869c"/>
        <path d="M218 828 Q227 825 234 818 L237 818 Q230 828 221 832 Z" fill="#d4e5df"/>
        <path d="M179 820 C185 816 193 816 202 817 C212 818 219 819 224 822 C219 828 210 832 199 834 C191 836 183 834 181 831 C179 830 178 828 179 825 Z" fill="url(#skin)" stroke="#9a7368" stroke-width="1.15"/>
        <path d="M183 830 C188 833 193 833 200 833 C205 833 209 831 212 830" fill="none" stroke="#dba58f" stroke-width="1.35" stroke-linecap="round"/>
        """,
        'support-contact-front.png': """
        <path d="M170 808 C191 805 221 801 237 805 Q243 807 241 812 C233 817 219 819 210 821 C196 825 184 827 176 825 C172 823 169 821 170 818 Z" fill="#7c5750" opacity=".22"/>
        <path d="M176 810 C188 807 199 804 210 804 C222 802 230 799 234 801 Q237 803 238 808 Q235 812 227 814 C216 817 208 818 200 820 C194 822 191 825 185 825 Q181 825 181 822 Q181 819 184 817 C182 818 180 820 177 821 Q173 821 173 818 C173 815 174 812 176 810 Z" fill="url(#skin)" stroke="#ac7d6f" stroke-width="1.15"/>
        <path d="M181 819 C186 816 191 815 196 814" fill="none" stroke="#d59f8e" stroke-width="1.0" stroke-linecap="round"/>
        <path d="M184 823 C188 822 192 820 196 818" fill="none" stroke="#f2c2a8" stroke-width="1.2" stroke-linecap="round"/>
        """
    }
    for name,paths in parts.items():
        svg='''<svg xmlns="http://www.w3.org/2000/svg" viewBox="162 796 92 48"><defs>
        <linearGradient id="skin" x1="0" y1="0" x2="0.2" y2="1" gradientUnits="objectBoundingBox">
        <stop stop-color="#fce9d7"/><stop offset=".42" stop-color="#fce4d1"/><stop offset=".78" stop-color="#f8d4bd"/><stop offset="1" stop-color="#edbca6"/>
        </linearGradient></defs>'''+paths+'</svg>'
        rgba=QImage(368,192,QImage.Format.Format_ARGB32_Premultiplied);rgba.fill(0)
        qp=QPainter(rgba);QSvgRenderer(QByteArray(svg.encode())).render(qp);qp.end()
        buf=QBuffer();buf.open(QIODevice.OpenModeFlag.WriteOnly);rgba.save(buf,'PNG')
        im=Image.open(io.BytesIO(buf.data().data())).convert('RGBA').resize((92,48),Image.Resampling.LANCZOS)
        im.save(HERE/name,optimize=True)
    return {'rear':'support-contact-back.png','front':'support-contact-front.png',
            'originAtRestActorPx':[1,962],'boundsInSettledPose':list(bounds),
            'blend':'smoothstep(0,1,weight)，随后同整本书与托掌平移。后层先于书体，前层在原托手前指之下。',
            'art':'新增指腹在书底露出约 7–11 个原像素（390 档约 1.6–2.5 px）；同一只手将书缘夹在下方指腹与前指之间。掌根用原手肤色重绘并留一笔接触暗部。'}


if __name__=='__main__':
    publish()
