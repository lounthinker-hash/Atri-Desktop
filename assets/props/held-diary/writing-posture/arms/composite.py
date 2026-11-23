"""Composite local d1/p1 writing layers onto the caller's unpadded actor artwork."""
import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

HERE=Path(__file__).resolve().parent
HELD=HERE.parents[1]
WR=HELD/'action-hand/write'
TURN=HELD/'action-hand/turn'
PEN=HELD/'pen-write'
BOUNDS=(-110,500,675,1130)
SIZE=(785,630)
ORIGIN=np.array(BOUNDS[:2])
ACTOR_SIZE=(666,1706)
SCALE=1706/(390*6)

def read(path):
    return json.loads(path.read_text('utf-8-sig'))

PERF=read(WR/'performance.json')
PROPS=read(PEN/'write.json')
HELD_INFO=read(HELD/'manifest.json')
TURN_INFO=read(TURN/'turn.json')
LEFT_RECT=(0,515,288,1070)

@lru_cache(maxsize=15)
def art(path):
    with Image.open(path) as im:return im.convert('RGBA')

@lru_cache(maxsize=3)
def mask(path):
    with Image.open(path) as im:return im.convert('L')

@lru_cache(maxsize=1)
def metadata():
    return read(HERE/'manifest.json')

def pose_metadata(stage,index):
    return metadata()['stages'][stage][index]

def local(xy):
    return tuple((np.floor(np.asarray(xy)-ORIGIN+.5)).astype(int))

def paste(layer,im,xy):
    layer.alpha_composite(im,local(xy))

def fade(im,f):
    if f>=1:return im
    out=im.copy();out.putalpha(im.getchannel('A').point(lambda a: round(a*f)));return out

@lru_cache(maxsize=42)
def sprite_piece(channel, stage, index):
    row=PERF['stages'][stage][index]
    q=row[channel]
    if q is None:return None
    return art(WR/PERF['atlases'][channel][q['image']]).crop(q['rect']),q['xy']

def stamp_book(layer,im,point,origin):
    pt=origin+np.asarray(point)*SCALE
    a=np.floor(pt+.5).astype(int);b=np.floor(pt+np.asarray(im.size)*SCALE+.5).astype(int)
    paste(layer,im.resize(tuple(b-a),Image.Resampling.LANCZOS),a)

def pen_pixels(layer,f,bo):
    pen=PROPS['pen'];c=math.cos(math.radians(f['rotation_cw']));s=math.sin(math.radians(f['rotation_cw']))
    tip=bo+np.asarray(f['tip'])*SCALE-ORIGIN
    a,b=pen['tip'];cx,cy=tip
    iv=(c/SCALE,s/SCALE,a-(cx*c+cy*s)/SCALE,-s/SCALE,c/SCALE,b+(cx*s-cy*c)/SCALE)
    im=art(PEN/pen['file']).convert('RGBa').transform(SIZE,Image.Transform.AFFINE,iv,Image.Resampling.BICUBIC).convert('RGBA')
    layer.alpha_composite(im)

@lru_cache(maxsize=40)
def support_front(t):
    im=art(HELD/'grip-front.png')
    if t<=0:return im
    col=np.asarray(im).copy()
    from scipy.ndimage import distance_transform_edt
    skin=(col[:,:,0].astype(int)-col[:,:,2]>15)&(col[:,:,3]>90)
    distance=distance_transform_edt(~skin)
    coverage=np.clip((distance-11)/3,0,1)*min(1.,t*2.5)
    col[:,:,3]=np.rint(col[:,:,3]*(1-(~skin)*coverage)).astype('uint8')
    return Image.fromarray(col)

def atlas_part(info,name):
    item=info.get(name)
    if not item:return None
    return art(HERE/item['file']).crop(item['rect']),item['xy']

def add(layer,part):
    if part is not None:paste(layer,*part)

def prepare_actor(stage,index):
    row=PERF['stages'][stage][index];info=pose_metadata(stage,index)
    erase=Image.new('L',SIZE);plate=Image.new('RGBA',SIZE)
    def replace(box, e, patched, opacity):
        pos=local(box[:2]);m=e.point(lambda v:round(v*opacity))
        full=Image.new('L',SIZE);full.paste(m,pos)
        nonlocal erase
        erase=ImageChops.lighter(erase,full)
        plate.alpha_composite(fade(patched,opacity),pos)
    erase.paste(mask(HELD/'rest-hand-erase.png'),local(HELD_INFO['hand_rect_actor_px'][:2]))
    left_t=min(1.,info['weight']*6)
    if left_t:replace(LEFT_RECT,mask(HERE/'support-erase.png'),art(HERE/'support-repair.png'),left_t)
    if not row['original_rest']:
        rp=TURN_INFO['rest_patch']
        replace(rp['actor_rect_px'],mask(TURN/rp['erase']),art(TURN/rp['restore']),1)
    return erase,plate

def render_layers(stage,index,page='blank'):
    row=PERF['stages'][stage][index];info=pose_metadata(stage,index);t=info['weight']
    erase,restore=prepare_actor(stage,index)
    obj=PROPS['frames'][row['prop_frame']]
    delta=np.array(info['delta']);bo=delta+np.array((34.,991.))-SCALE*np.array((180,276))
    layer=Image.new('RGBA',SIZE)
    add(layer,atlas_part(info,'supportSleeve'))
    point=delta+np.array(HELD_INFO['hand_rect_actor_px'][:2])
    pressure=t*t*(3-2*t)
    contact_pos=delta+np.array((1,962))
    if pressure:paste(layer,fade(art(HERE/'support-contact-back.png'),pressure),contact_pos)
    paste(layer,art(HELD/'grip-back.png'),point)
    table='original_stowed_pages' if obj['stowed'] else 'pages'
    stamp_book(layer,art(PEN/PROPS[table][page]),(0,0),bo)
    ink=PROPS['ink'];w,h=ink['tile_size'];q=obj['ink_tile'];ix=q%ink['columns']*w;iy=q//ink['columns']*h
    stamp_book(layer,art(PEN/ink['atlas']).crop((ix,iy,ix+w,iy+h)),ink['book_rect'][:2],bo)
    if pressure:paste(layer,fade(art(HERE/'support-contact-front.png'),pressure),contact_pos)
    paste(layer,support_front(t),point)
    for channel in ['far','pen','near']:
        if channel=='pen':
            if not obj['stowed']:
                pen_pixels(layer,obj,bo)
                if obj['loop_in_front']:stamp_book(layer,art(PEN/PROPS['loop_front']),(0,0),bo)
        else:
            part=sprite_piece(channel,stage,index)
            if part:paste(layer,part[0],np.array(part[1])+delta)
    add(layer,atlas_part(info,'workSleeve'))
    return erase,restore,layer

def render_native(actor,stage,index,page='blank',pad_x=112):
    if actor.size!=ACTOR_SIZE:raise ValueError('Expected unpadded d1/p1 native 666 × 1706 actor.')
    body=Image.new('RGBA',(actor.width+2*pad_x,actor.height));body.paste(actor.convert('RGBA'),(pad_x,0))
    erase,restore,top=render_layers(stage,index,page)
    box=(BOUNDS[0]+pad_x,BOUNDS[1],BOUNDS[2]+pad_x,BOUNDS[3])
    tile=body.crop(box);tile.putalpha(ImageChops.multiply(tile.getchannel('A'),ImageChops.invert(erase)))
    tile.alpha_composite(restore);tile.alpha_composite(top);body.paste(tile,box)
    return body

