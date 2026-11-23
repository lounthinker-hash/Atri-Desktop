"""Draw the game's diary at Atri's hand and its crab on the desktop ground."""

import hashlib
import json
import importlib.util
from io import BytesIO
import math
from zipfile import BadZipFile, ZipFile
from bisect import bisect_right
from functools import lru_cache
from pathlib import Path
from threading import Thread

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QImage, QPainter, QTransform

from desktop_motion import attached_point, displacement, field_weights, raster_grid
from desktop_pose_assets import read_plate


def lift_fingerprint(held_directory):
    """Bind pre-rendered RGBA to its support art, registered drawing and pen/book versions."""
    ingredients = (
        "write-interrupt/lift/manifest.json", "write-interrupt/lift/read.py",
        "write-interrupt/render/source.py", "book/book.json", "pen-write/write.json",
        "action-hand/write/performance.json", "writing-posture/arms/composite.py",
    )
    digest=hashlib.sha256()
    support_directory=held_directory/"small-review/book"
    support=json.loads((support_directory/"manifest.json").read_text(encoding="utf-8-sig"))
    # Explanatory notes and evidence links may be edited without changing
    # the registered art. Bind the actual crop, reader and two image files.
    digest.update(json.dumps([support["handRectActorPx"],support["spineActorPx"]],
                             separators=(",",":")).encode())
    ingredients+=tuple("small-review/book/"+s for s in
                       [support["integration"]["correctedLocalReader"],
                        *(support["layers"][key]["file"] for key in ("grip_back","grip_front"))])
    for name in ingredients:
        digest.update(name.encode()+b"\0")
        digest.update(hashlib.sha256((held_directory/name).read_bytes()).digest())
    return digest.hexdigest()


class DiaryPerformance:
    """Play the hand, leaf and pen in their shared, unpadded actor coordinates."""

    def __init__(self, directory, held):
        self.directory = directory
        self.held = held
        self.support_directory = directory/"small-review/book"
        support = self._json(self.support_directory/"manifest.json")
        self.support_files = {key:self.support_directory/support["layers"][key]["file"]
                              for key in ("grip_back","grip_front")}
        index = self._json(directory / held["actions_manifest"])
        self.canvas = tuple(index["actorCanvas"])
        self.fps = index["fps"]
        self.book_scale = index["support"]["bookPixelToActorPixel"]
        self.spine = np.array(index["support"]["spineActorPx"], dtype=float)
        self.book_origin = self.spine - self.book_scale * np.array(index["support"]["spineBookPx"])
        self.turn_dir, self.turn = self._catalog(index["pageTurn"]["hand"])
        self.write_dir, self.write = self._catalog(index["penWrite"]["hand"])
        self.plate_dir=directory.parents[1]/"motion"
        self.paper_dir, self.paper = self._catalog(index["pageTurn"]["paper"])
        self.pen_dir, self.pen = self._catalog(index["penWrite"]["object"])
        turn, write = self.turn["stages"], self.write["stages"]
        def sequence(*stages):
            return [(kind, frame) for kind, frames in stages for frame in frames]
        self.sequences = {
            "turn": sequence(*( ("turn", turn[s]) for s in ("reach", "turn", "return"))),
            "write": sequence(*( ("write", write[s]) for s in ("reach", "write", "return"))),
            "joined": sequence(("turn", turn["reach"]), ("turn", turn["turn"]),
                               ("write", write["turn_to_pen"][1:]), ("write", write["write"]),
                               ("write", write["return"])),
        }
        self.handoff = len(turn["reach"])+len(turn["turn"])
        self.index = {kind:{id(frame):i for i,(_,frame) in enumerate(self.sequences[kind])}
                      for kind in ("turn","write")}
        self.write_start=len(write["reach"])
        self.wrist_return=self.write_start+len(write["write"])
        manifest=directory/index["penWrite"]["interruptManifest"]
        files=self._json(manifest)["files"]
        spec=importlib.util.spec_from_file_location("atri_registered_diary_release",manifest.parent/files["playback"])
        self.exits=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.exits)
        self.lift_manifest=manifest.parent/files["lift"]
        self.lift_archive=directory.parents[2]/"desktop/renderer/diary-contact.zip"
        self.original_lift_data=self.exits.lift_data
        self.exits.lift_data=self.compact_lift_data
        # The brief hovers between handwritten strokes are held at their existing
        # lifted-pen drawings. The object, ink and fingers always advance together.
        self.ends = {}
        for clip, frames in self.sequences.items():
            elapsed = 0.
            ends = []
            for kind, frame in frames:
                elapsed += 1 / self.fps
                if kind == "write" and frame["stage"] == "write" and frame["prop_frame"] in (43, 56, 69):
                    elapsed += .12
                ends.append(elapsed)
            self.ends[clip] = ends
        self.bounds = (-110, 478, 675, 1130)
        self.warming = set()

    def warm(self, group):
        """Decode only this activity's original atlases while the book is opening."""
        if group in self.warming:
            return
        self.warming.add(group)
        if group=="write":
            paths=[self.write_dir/f for files in self.write["atlases"].values() for f in files]
            paths.append(self.pen_dir/self.pen["ink"]["atlas"])
        else:
            paths=[self.turn_dir/f for f in self.turn["atlases"].values()]
            paths.extend(self.paper_dir/f for f in self.paper["motion_24fps"]["atlas_files"].values())
        def load():
            prepared={}
            if group=="write":
                self.exits.lift_data()
                self.prepared_lifts()
                for stage in self.write["stages"].values():
                    for frame in stage:
                        sleeve=frame.get("sleeve")
                        if sleeve and sleeve["image"]<2:
                            path=self.write_dir/self.write["atlases"]["sleeve"][sleeve["image"]]
                            prepared.setdefault(path,tuple(sleeve["rect"]))
            for path in paths:
                if path in prepared and self.sleeve_tile(path,prepared[path]) is not None:
                    continue
                self.atlas(path)
        Thread(target=load,name="Atri diary artwork",daemon=True).start()

    @staticmethod
    def _json(path):
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def _catalog(self, path):
        path = self.directory / path
        return path.parent, self._json(path)

    def captured_path(self,ctx,clip,index):
        name=f"nib_lift_{ctx.start}"
        if name not in self.sequences:
            rows=[]
            for drawing in self.exits.lift_data()[str(ctx.start)]["frames"]:
                anchors=drawing["anchors"]["hip"]
                rows.append(("write",{"stage":"lift","local_frame":drawing["sample"],"original_rest":False,
                    "prop_frame":ctx.start,"paper_contact":False,"stroke":None,"wrist_px":anchors["wristActorPx"],
                    "tip_actor_px":anchors["tipActorPx"],"grip_actor_px":anchors["gripActorPx"]}))
            self.sequences[name]=rows
        path=[(clip,index)]
        for k in range(1,self.exits.catalog()["frameCount"]):
            stage,n=self.exits.source_frame(k)
            if stage=="lift" and n<10:path.append((name,n))
            elif stage=="return":path.append(("write",self.wrist_return+n))
            else:path.append(("write",self.write_start+(75 if stage=="lift" else n)))
        return path

    @lru_cache(maxsize=1)
    def prepared_lifts(self):
        fingerprint=lift_fingerprint(self.directory)
        primary=self.lift_archive
        for path in (primary,primary.parent/fingerprint/primary.name):
            archive=None
            try:
                archive=ZipFile(path)
                if archive.read("source.sha256").decode()==fingerprint:
                    self.lift_archive=path
                    return archive
            except (OSError,BadZipFile,KeyError,UnicodeError):
                pass
            if archive is not None:
                archive.close()
        return None

    @lru_cache(maxsize=1)
    def compact_lift_data(self):
        archive=self.prepared_lifts()
        if archive is not None and "lift-meta.json" in archive.NameToInfo:
            try:
                return json.loads(archive.read("lift-meta.json"))
            except (OSError,BadZipFile,ValueError):
                pass
        return self.original_lift_data()

    @lru_cache(maxsize=4)
    def contact_group(self,height,start):
        archive=self.prepared_lifts()
        return ZipFile(BytesIO(archive.read(f"{height}/{start}")))

    def cached_lift(self,ctx,index,height):
        # Compact rows are lossless official local_layer outputs. For another
        # monitor DPI, downsample the next larger one in premultiplied space.
        # The captured first frame and the native continuation stay exact.
        archive=self.prepared_lifts()
        if archive is not None and ctx.posture=="hip":
            sizes=(280,390,420,520,585,780)
            source_height=next((h for h in sizes if h>=height),sizes[-1])
            if "lift-meta.json" in archive.NameToInfo:
                archive=self.contact_group(source_height,ctx.start)
                prefix=f"{index}/"
            else:
                prefix=f"{source_height}/{ctx.start}/{index}/"
            raw=archive.read(prefix+"blank")
            if ctx.page!="blank":
                data=archive.read(prefix+ctx.page)
                raw=np.bitwise_xor(np.frombuffer(raw,np.uint8),np.frombuffer(data,np.uint8)).tobytes()
            l,t,r,b=self.bounds
            w,h=(int((v*height/self.canvas[1])+.5) for v in (r-l,b-t))
            if source_height!=height:
                original=tuple(int(v*source_height/self.canvas[1]+.5) for v in (r-l,b-t))
                scaled=Image.frombytes("RGBA",original,raw).convert("RGBa").resize((w,h),Image.Resampling.LANCZOS)
                raw=scaled.convert("RGBA").tobytes()
            if len(raw)!=w*h*4:raise ValueError("Incorrect cached nib-lift dimensions")
            return raw,w,h

    @lru_cache(maxsize=1)
    def support_source(self):
        spec=importlib.util.spec_from_file_location("atri_diary_support_source",self.support_directory/"source.py")
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @lru_cache(maxsize=36)
    def lift_image(self,ctx,index,height):
        try:
            prepared=self.cached_lift(ctx,index,height)
        except (OSError,BadZipFile,ValueError,KeyError):
            prepared=None
        if prepared:
            raw,w,h=prepared
        else:
            frame=self.support_source().local_layer(ctx.posture,"lift",index,ctx.page,ctx.ink_tile,height,ctx.start)
            raw,w,h=frame.tobytes(),*frame.size
        return QImage(raw,w,h,w*4,QImage.Format.Format_RGBA8888).copy()

    def release_path(self, clip, index):
        """Join the last displayed drawing to the original take-up and wrist return."""
        kind,frame=self.sequences[clip][index]
        canon=self.index[kind].get(id(frame))
        adjust=None
        if canon is None:  # Late page handover; pen is still in the book.
            path=[(clip,i) for i in range(index,self.handoff+len(self.write["stages"]["turn_to_pen"])-1)]
            path.extend(("write",i) for i in range(self.wrist_return,len(self.sequences["write"])))
        elif frame["stage"]=="reach" or (kind=="write" and frame["stage"]=="write" and frame["prop_frame"]<=26):
            path=[(kind,i) for i in range(canon,-1,-1)]
        elif kind=="write" and frame["stage"]=="write" and frame["prop_frame"]<65:
            end_stroke=self.write_start+65
            path=[(clip,index)]+[("write",i) for i in range(end_stroke+1,len(self.sequences["write"]))]
            ref=self.sequences["write"][end_stroke][1]
            offset=np.asarray(frame["tip_actor_px"])-ref["tip_actor_px"]
            angle=self.pen["frames"][frame["prop_frame"]]["rotation_cw"]-self.pen["frames"][65]["rotation_cw"]
            adjust=(*offset,angle)
        else:
            path=[(kind,i) for i in range(canon,len(self.sequences[kind]))]
        ink=self.pen["frames"][frame["prop_frame"]]["ink_tile"] if kind=="write" else None
        return path,adjust,ink

    def register_working_arm(self, image, scale, tip, correction):
        """Bring the nib and gripping fingers to the interrupted line; pin the upper sleeve."""
        w,h=image.size
        xs=np.unique(np.linspace(0,w,max(9,round(w/7))).round().astype(np.int32))
        ys=np.unique(np.linspace(0,h,max(9,round(h/7))).round().astype(np.int32))
        x,y=np.meshgrid(xs,ys)
        left,top=self.bounds[:2]
        nx=left+x/scale;ny=top+y/scale
        if len(correction)>3:
            blend,w,h,ox,oy,ax,ay,bx,by,breath,*body=correction
            # Invert the same torso mesh used to draw the rest sleeve over its
            # full length, keeping the book on its separate rigid registration.
            wx,wy=ox+ax*nx+bx*ny,oy+ay*nx+by*ny
            dx,dy=displacement(field_weights((wx-w/2)/h,wy/h,"p1"),body,breath)
            sx=nx+blend*((wx-h*dx)*self.canvas[0]/w-nx)
            sy=ny+blend*((wy-h*dy)*self.canvas[1]/h-ny)
            return raster_grid(image,xs,ys,np.stack(((sx-left)*scale,(sy-top)*scale),axis=-1))
        arm=np.clip((np.hypot(nx-453.,ny-563.)-30.)/225.,0.,1.)
        arm=arm*arm*(3.-2.*arm)
        dx,dy,angle=correction
        rad=-np.radians(angle)*arm
        rx=nx-tip[0]-dx*arm;ry=ny-tip[1]-dy*arm
        sx=tip[0]+rx*np.cos(rad)-ry*np.sin(rad)
        sy=tip[1]+rx*np.sin(rad)+ry*np.cos(rad)
        return raster_grid(image,xs,ys,np.stack(((sx-left)*scale,(sy-top)*scale),axis=-1))

    @staticmethod
    # The eleven installed atlases fit together. The wrist plates used by both
    # taking and putting away the pen survive subsequent page-turn preparation.
    @lru_cache(maxsize=12)
    def atlas(path):
        with Image.open(path) as source:
            return source.convert("RGBA")

    @staticmethod
    @lru_cache(maxsize=24)
    def art(path):
        with Image.open(path) as source:
            return source.convert("RGBA")

    @lru_cache(maxsize=4)
    def rest_patch(self, size):
        item = self.turn["rest_patch"]
        box = np.array(item["actor_rect_px"]).reshape(2, 2) * (np.array(size) / self.canvas)
        (x,y), (r,b) = (np.floor(box+.5)).astype(int)
        with Image.open(self.turn_dir / item["erase"]) as src:
            erase = src.convert("L").resize((r-x,b-y), Image.Resampling.LANCZOS)
        repair = self.art(self.turn_dir / item["restore"]).resize((r-x,b-y), Image.Resampling.LANCZOS)
        return (x,y), erase, repair

    def _arm(self, kind, frame, key):
        part = frame.get(key)
        if not part:
            return None
        if kind == "turn":
            path = self.turn_dir / self.turn["atlases"][key]
            rect, xy = part["atlas_rect_px"], part["actor_xy_px"]
        else:
            path = self.write_dir / self.write["atlases"][key][part["image"]]
            rect, xy = part["rect"], part["xy"]
            if key=="sleeve" and part["image"]<2:
                tile=self.sleeve_tile(path,tuple(rect))
                if tile is not None:
                    return tile,xy
        return self.atlas(path).crop(rect), xy

    @staticmethod
    @lru_cache(maxsize=2)
    def _sleeve_digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @lru_cache(maxsize=12)
    def sleeve_tile(self,path,rect):
        # The two large source sheets take >200 ms to decode. Each exposure
        # has its own lossless block, verified against the installed artwork.
        key=self._sleeve_digest(path)+"/"+"-".join(map(str,rect))
        return read_plate(self.plate_dir,key,archive_name="diary-sleeves.zip")

    @lru_cache(maxsize=36)
    def layer(self, clip, index, page, height, ink_override=None, correction=None):
        kind, frame = self.sequences[clip][index]
        left, top, right, bottom = self.bounds
        # Compose at physical output resolution. Every atlas crop and offset
        # comes from the same native coordinate system, including the pen tip.
        scale = height / self.canvas[1]
        xy = lambda point: tuple(np.floor((np.array(point)-(left,top))*scale+.5).astype(int))
        result = Image.new("RGBA", xy((right,bottom)))
        def place(img, pos, factor=1., target=result):
            x,y = xy(pos)
            r,b = xy(np.array(pos)+np.array(img.size)*factor)
            if r>x and b>y:
                target.alpha_composite(img.resize((r-x,b-y),Image.Resampling.LANCZOS),(x,y))
        def finger(key, target=result):
            part = self._arm(kind,frame,key)
            if part:
                place(*part,target=target)
        def support(key):
            place(self.art(self.support_files[key]),
                  self.held["hand_rect_actor_px"][:2])
        def book(img, local=(0,0), target=result):
            place(img,self.book_origin+self.book_scale*np.array(local),self.book_scale,target)
        work=Image.new("RGBA",result.size) if correction else result
        support("grip_back")
        if kind == "turn":
            stage = frame["stage"]
            motion = self.paper["motion_24fps"]
            pos = frame["index"] if stage=="turn" else -1 if stage=="return" else 0
            desc = motion["frames"][pos]
            def tile(key):
                return self.atlas(self.paper_dir/motion["atlas_files"][key]).crop(desc["atlas_rect_px"])
            book(tile("bed_file"))
            leaf = Image.new("RGBA",result.size)
            book(tile("leaf_front_file"),target=leaf)
            result.alpha_composite(leaf)
            support("grip_front")
            finger("sleeve",work)
            back = Image.new("RGBA",result.size)
            finger("back_finger",back)
            if stage=="turn" and (frame["on_page"] or 16<=frame["index"]<=17):
                back.paste((0,0,0,0), (0,0), leaf.getchannel("A"))
            work.alpha_composite(back)
            finger("front_finger",work)
        else:
            pen = self.pen["frames"][frame["prop_frame"]]
            table = "original_stowed_pages" if pen["stowed"] else "pages"
            book(self.art(self.pen_dir/self.pen[table][page]))
            ink = self.pen["ink"]
            w,h = ink["tile_size"]
            it = pen["ink_tile"] if ink_override is None else ink_override
            ix,iy = (it%ink["columns"])*w,(it//ink["columns"])*h
            book(self.atlas(self.pen_dir/ink["atlas"]).crop((ix,iy,ix+w,iy+h)),ink["book_rect"][:2])
            support("grip_front")
            finger("far",work)
            if not pen["stowed"]:
                src = self.art(self.pen_dir/self.pen["pen"]["file"]).convert("RGBa")
                rad = math.radians(pen["rotation_cw"])
                cs,sn = math.cos(rad),math.sin(rad)
                px,py = self.pen["pen"]["tip"]
                # Invert the pen-to-actor registration directly into this output crop.
                tip = (self.book_origin+self.book_scale*np.array(pen["tip"])-(left,top))*scale
                f = self.book_scale*scale
                cx,cy = tip
                inverse = (cs/f,sn/f,px-(cs*cx+sn*cy)/f,-sn/f,cs/f,py+(sn*cx-cs*cy)/f)
                work.alpha_composite(src.transform(result.size,Image.Transform.AFFINE,inverse,
                                                   Image.Resampling.BICUBIC).convert("RGBA"))
            if pen["loop_in_front"] and not pen["stowed"]:
                book(self.art(self.pen_dir/self.pen["loop_front"]))
            finger("near",work)
            finger("sleeve",work)
        if correction:
            pivot=frame.get("tip_actor_px") or frame["wrist_px"]
            result.alpha_composite(self.register_working_arm(work,scale,pivot,correction))
        return QImage(result.tobytes(),*result.size,result.width*4,QImage.Format.Format_RGBA8888).copy()


class PropRenderer:
    """Call paint before painting the actor. Times are monotonic seconds."""

    def __init__(self, root):
        self.directory = Path(root) / "assets" / "props"
        with (self.directory / "manifest.json").open(encoding="utf-8-sig") as file:
            manifest = json.load(file)
        self.props = manifest["props"]
        width, height = manifest["reference"]["canvas_px"]
        self.front_aspect = width / height
        self.images = {}
        held_path = self.directory / self.props["diary"]["held_variant_manifest"]
        self.held_directory = held_path.parent
        self.held = json.loads(held_path.read_text(encoding="utf-8-sig"))
        books = self.held_directory / self.held["book_manifest"]
        self.book_directory = books.parent
        self.books = json.loads(books.read_text(encoding="utf-8-sig"))
        self.performance = DiaryPerformance(self.held_directory,self.held)
        self.reset()

    def reset(self):
        self.visual_time = None
        self.opening = self.grasp = self.book_presence = 0.
        self.page = self.previous_page = "open_blank"
        self.page_changed = -10.
        self.paper_mix = 1.
        self.visual = None
        self.last_action = self.blocked_preview = None
        self.stow_mask = None
        self.hand_reference = self.palm_reference = self.wrist_world = None
        self.standing_hand_image=None
        self.hand_angle=0.
        self.moving_hand=False
        self.wrist_time=None
        self.wrist_velocity=np.zeros(2)
        self.clip = self.clip_activity = self.clip_trigger = self.action_frame = None
        self.clip_start = 0.
        self.release=None
        self.release_until=0.
        self.release_fps=48.
        self.interrupt_capture=None
        self.last_drawn_frame=self.last_support=self.arm_correction=self.stopped_ink=None
        self._reset_activity()

    def _reset_activity(self):
        self.activity = None
        self.started = 0.0
        self.last_time = 0.0
        self.last_gesture = None
        self.writing_seconds = 0.0
        self.ground = None
        self.state = None
        self.previous = None
        self.changed = 0.0

    @property
    def releasing(self):
        return self.release is not None

    def interrupt(self, now=None):
        """Retain exactly the drawing that reached the screen on the previous paint."""
        if self.release is None and self.last_drawn_frame and self.visual:
            now=self.visual_time if now is None else now
            clip,index,page=self.last_drawn_frame
            self.interrupt_capture=self.performance.exits.capture(clip,index,page,posture="hip")
            if self.interrupt_capture:
                path=self.performance.captured_path(self.interrupt_capture,clip,index)
                shift,ink=None,self.interrupt_capture.ink_tile
                self.release_fps=self.performance.exits.catalog()["fps"]
            else:
                path,shift,ink=self.performance.release_path(clip,index)
                self.release_fps=48.
            duration=max(.04,len(path)/self.release_fps)
            self.release=(now,duration,path,shift,page,self.last_support)
            self.release_until=now+duration+.49
            self.stopped_ink=ink
        self.blocked_preview=self.last_action
        self.clip_activity=None
        self._reset_activity()

    def support_body(self,body,now):
        if self.release is None or self.release[-1] is None:
            return body
        start,duration,_,_,_,source=self.release
        end=start+duration+.45
        fall=min(.60,duration+.45)
        keep=max(0.,min(1.,(end-now)/fall))
        keep=keep*keep*(3.-2.*keep)
        if not keep:return body
        posed=body.copy()
        channels=[3,4,7]
        posed[channels]+=keep*(source-posed[channels])
        return posed

    def _retract(self,now):
        start,duration,path,shift,page,_=self.release
        elapsed=max(0.,now-start)
        self.arm_correction=None
        if elapsed<=duration:
            i=min(len(path)-1,int(elapsed*self.release_fps+1e-7))
            clip,index=path[i]
            self.action_frame=(clip,index,page)
            if shift and 0<i<16:
                k=1.-i/16
                self.arm_correction=tuple(float(v*k*k*(3.-2.*k)) for v in shift)
            return 1.,1.,1.
        self.action_frame=None
        close=max(0.,min(1.,(elapsed-duration)/.255))
        tuck=max(0.,min(1.,(elapsed-duration-.195)/.29))
        opening=1.-close*close*(3.-2.*close)
        presence=1.-tuck*tuck*(3.-2.*tuck)
        return opening,min(1.,presence*2.2),presence

    def _advance_clip(self, activity, gesture, action, now):
        if activity != self.clip_activity:
            self.clip_activity = activity
            self.clip = self.clip_trigger = self.action_frame = None
        if self.opening < .995 or self.book_presence < .995:
            self.action_frame = None
            return
        if gesture in ("write","turn_page") and self.clip_trigger != action:
            if activity=="evening_log" and self.clip=="joined" and gesture=="write":
                self.clip_trigger=action
                self.clip_start=now-self.performance.ends["joined"][self.performance.handoff-1]
            elif not self.clip or now-self.clip_start >= self.performance.ends[self.clip][-1]:
                self.clip_trigger = action
                self.clip = ("joined" if activity=="evening_log" and gesture=="turn_page"
                             else "turn" if gesture=="turn_page" else "write")
                self.clip_start = now
        if self.clip:
            ends = self.performance.ends[self.clip]
            i = min(len(ends)-1,bisect_right(ends,now-self.clip_start+1e-8))
            if self.clip=="joined" and self.clip_trigger[0]=="turn_page":
                i=min(i,self.performance.handoff-1)
            self.action_frame = self.clip,i,self.page.removeprefix("open_")

    def _diary_state(self, now):
        age = max(0.0, now - self.started)
        if age < 0.65:
            return "closed"
        if self.activity=="class":
            return "open_filled"
        if self.activity.startswith("preview:"):
            # A single gesture preview lasts 4.8 seconds.
            return "open_blank" if age < 1.65 else "open_sparse" if age < 3.25 else "open_filled"
        if self.writing_seconds < 2.1:
            return "open_blank"
        if self.writing_seconds < 8.0:
            return "open_sparse"
        return "open_filled"

    def held_frame(self, *, routine, gesture, pose, dress, transitioning, now, age, remaining, final,
                   pointer_down=False, allow_preview=True):
        action=(gesture,round(now-age,5))
        self.last_action=action
        if routine=="class" or routine in self.props["diary"]["routine_ids"]:
            activity = routine
        elif allow_preview and not routine and gesture in ("write", "turn_page") and action!=self.blocked_preview:
            activity = "preview:" + gesture
        else:
            activity = None
        if self.visual_time is not None and now < self.visual_time:
            self.reset()
        dt=min(.15,now-self.visual_time) if self.visual_time is not None else 0.
        self.visual_time=now
        compatible=not transitioning and pose==self.held["pose"] and dress==self.held["dress"]
        if self.release and (now>=self.release_until or not compatible):
            self.release=None
            self.interrupt_capture=None
            self.last_drawn_frame=self.arm_correction=self.stopped_ink=self.action_frame=None
            self.clip=self.clip_trigger=self.clip_activity=None
            if compatible:
                self.opening=self.grasp=self.book_presence=0.
                self.visual=None
        if self.release:
            opening,grasp,presence=self._retract(now)
        elif activity and compatible:
            first_turn = activity in ("class","evening_log","preview:turn_page")
            self.performance.warm("turn" if first_turn else "write")
            if self.activity != activity:
                self._reset_activity()
                self.activity, self.started, self.last_time = activity, now, now
            if activity=="evening_log" and now-self.started>.8:
                self.performance.warm("write")
            if self.last_gesture == "write":
                self.writing_seconds += max(0., now-self.last_time)
            self.last_time, self.last_gesture = now, gesture
            state=self._diary_state(now)
            new_page="open_blank" if state=="closed" else state
            if self.page!=new_page:
                self.previous_page,self.page,self.page_changed=self.page,new_page,now
            elapsed=now-self.started
            opening=max(0.,min(1.,(elapsed-.48)/.56))
            if final:
                opening=min(opening,max(0.,(remaining-.24)/.56))
            grasp=1.
            presence=max(0.,min(1.,(elapsed-.04)/.22))
            if final and remaining<.24:
                presence=min(presence,max(0.,remaining/.24))
                grasp=min(grasp,max(0.,remaining/.14))
        elif self.visual and compatible and (pointer_down or gesture=="drag"):
            # Cancel writing on press, carry the actual page while the window
            # moves, then close it at the wrist on landing.
            opening,grasp,presence=self.opening,self.grasp,self.book_presence
        else:
            opening=0.
            presence=self.book_presence if self.opening>.025 else 0.
            grasp=self.grasp if self.book_presence>.45 else 0.
        def approach(value,target,speed):
            return value+max(-dt*speed,min(dt*speed,target-value))
        if self.release:
            self.opening,self.book_presence,self.grasp=opening,presence,grasp
        else:
            self.opening=approach(self.opening,opening,4.8 if compatible else 7.)
            self.book_presence=approach(self.book_presence,presence,4.8 if compatible else 7.)
            self.grasp=approach(self.grasp,grasp,6.5)
        self.paper_mix=min(1.,max(0.,(now-self.page_changed)/.18))
        if self.release is None:
            if activity and compatible:
                self._advance_clip(activity,gesture,action,now)
            elif not compatible or not (pointer_down or gesture=="drag"):
                self.action_frame = None
                self.clip_activity = None
        if self.grasp<.001 and self.book_presence<.001:
            self.visual=None
            self.last_drawn_frame=None
            return None
        frames=self.books["opening_frames_by_state"][self.page]
        index=math.floor(self.opening*(len(frames)-1)+.5)
        self.state="closed" if not index else self.page
        self.visual=self.state,frames[index]
        return self.visual

    @lru_cache(maxsize=8)
    def hand_layout(self, size):
        width, height = size
        ref_w,ref_h = self.held["actor"]["canvas_px"]
        near = lambda n: math.floor(n+.5)
        x0,y0,x1,y1 = self.held["hand_rect_actor_px"]
        pos = (near(x0*width/ref_w), near(y0*height/ref_h))
        hand_size = (near(x1*width/ref_w)-pos[0], near(y1*height/ref_h)-pos[1])
        x,y=self.held["contact"]["actor_px"]
        contact = (near(x*width/ref_w),near(y*height/ref_h))
        pivot = tuple(near(n*height/390) for n in self.books["pivot_display_px"])
        book_size = tuple(near(n*height/390) for n in self.books["display_size_px"])
        with Image.open(self.held_directory/self.held["layers"]["rest_hand_erase"]["file"]) as mask:
            erase = mask.resize(hand_size,Image.Resampling.LANCZOS)
        return pos,hand_size,contact,pivot,book_size,erase

    @lru_cache(maxsize=120)
    def layer_image(self, path, size):
        with Image.open(path) as original:
            image=original.convert("RGBa").resize(size,Image.Resampling.LANCZOS).convert("RGBA")
        return QImage(image.tobytes(), *size, size[0]*4, QImage.Format.Format_RGBA8888).copy()

    def warm_hand(self,size):
        if getattr(self,"warm_hand_size",None)==size:
            return
        self.warm_hand_size=size
        def load():
            _,hand_size,_,_,book_size,_=self.hand_layout(size)
            for key in ("grip_back","grip_front"):
                self.layer_image(self.performance.support_files[key],hand_size)
            first=self.books["opening_frames_by_state"]["open_blank"][0]
            self.layer_image(self.book_directory/first,book_size)
        Thread(target=load,name="Atri grip preparation",daemon=True).start()

    def _palm(self,actor,center,height):
        """Locate the skin adjoining the blue cuff in the moving arm plate."""
        # White d1/p1 and the lifting-arm plates always put the left hand
        # outside the face. The saturated blue cuff separates fingers from hair.
        left,top=0,0
        stride=max(1,round(height/390))
        tile=np.asarray(actor.crop((0,0,round(actor.width*.49),round(height*.65))))[::stride,::stride].astype("int16")
        height=height/stride
        r,g,b,a=tile.transpose(2,0,1)
        y,x=np.indices(a.shape)
        cuff=(b>r+38)&(g>r+24)&(a>160)
        if not cuff.any():
            return None
        skin=(r>200)&(r-g>13)&(g-b>1)&(a>160)&(distance_transform_edt(~cuff)<height*.053)
        if not skin.any():
            return None
        leftmost=int(np.min(x[skin]))
        skin &= x < leftmost + max(4,height*.032)
        sy,sx=np.mean(np.nonzero(skin),axis=1)
        nearby=cuff&((x-sx)**2+(y-sy)**2<(height*.057)**2)
        if not nearby.any():
            return None
        cy,cx=np.mean(np.nonzero(nearby),axis=1)
        return np.array([left+sx,top+sy])*stride, math.atan2(sy-cy,sx-cx)

    def prepare_hand(self,actor,offset,pose,in_transition):
        """Erase the open fingers on the actual arm, including while stowing the book."""
        origin=np.asarray(offset,dtype=float)
        moving=in_transition or pose!="p1"
        self.moving_hand=moving
        if not moving or self.hand_reference is None:
            self.hand_reference=actor.size
        size=self.hand_reference
        wrist=np.asarray((size[0]*.051,size[1]*.559))
        if not moving:
            if self.standing_hand_image is None or self.standing_hand_image.size!=size:
                self.standing_hand_image=actor
                self.palm_reference=None
            self.hand_angle=0.
            moved=wrist
        else:
            points=actor.info.get("atri_pose_registration")
            if points:
                moved=np.asarray(points[1][19])*actor.size
                tip=np.asarray(points[1][21])*actor.size-moved
                self.hand_angle=math.atan2(tip[1],tip[0])-math.atan2(.003*size[1],-.035*size[0])
            else:
                estimate=self.wrist_world-origin if self.wrist_world is not None else wrist
                feature=self._palm(actor,estimate,size[1])
                if self.palm_reference is None and self.standing_hand_image is not None:
                    self.palm_reference=(size,self._palm(self.standing_hand_image,wrist,size[1]))
                base=self.palm_reference[1] if self.palm_reference else None
                if feature and base:
                    moved=feature[0]+wrist-base[0]
                    angle=feature[1]-base[1]
                    dt=max(0.,min(.1,self.visual_time-self.wrist_time)) if self.wrist_time is not None else 0.
                    prediction=estimate+self.wrist_velocity*dt
                    if np.linalg.norm(moved-prediction)>size[1]*(.018+.45*dt):
                        moved=prediction
                    else:
                        change=math.atan2(math.sin(angle-self.hand_angle),math.cos(angle-self.hand_angle))
                        self.hand_angle+=np.clip(change,-7.*dt,7.*dt)
                else:
                    moved=estimate
        if self.wrist_time is not None and self.visual_time>self.wrist_time:
            dt=self.visual_time-self.wrist_time
            velocity=(origin+moved-self.wrist_world)/dt
            self.wrist_velocity=.4*self.wrist_velocity+.6*velocity if moving else np.zeros(2)
        self.wrist_time=self.visual_time
        self.wrist_world=origin+moved
        local=moved-wrist
        pos,hsize,_,_,_,mask=self.hand_layout(size)
        canvas=actor.copy()
        if self.grasp<1.:
            mask=mask.point(lambda a: round(a*self.grasp))
        if abs(self.hand_angle)>.005:
            # Rotate the source erasure about the same wrist as the two pieces of palm.
            rect=QRectF(pos[0],pos[1],hsize[0],hsize[1])
            t=QTransform().translate(float(moved[0]),float(moved[1])).rotateRadians(self.hand_angle)
            t.translate(-float(wrist[0]),-float(wrist[1]))
            box=t.mapRect(rect).toAlignedRect()
            inverse=t.inverted()[0]
            xy=inverse.map(QPointF(box.x(),box.y()))
            mask=mask.transform((box.width(),box.height()),Image.Transform.AFFINE,
                 (inverse.m11(),inverse.m21(),xy.x()-pos[0],inverse.m12(),inverse.m22(),xy.y()-pos[1]),
                 Image.Resampling.BILINEAR)
            at=(box.x(),box.y())
        else:
            at=(round(pos[0]+local[0]),round(pos[1]+local[1]))
        canvas.paste((0,0,0,0),at,mask)
        if self.action_frame and not moving:
            clip, index, _ = self.action_frame
            _, frame = self.performance.sequences[clip][index]
            if not frame["original_rest"]:
                patch_xy, erase, repair = self.performance.rest_patch(canvas.size)
                canvas.paste((0,0,0,0),patch_xy,erase)
                canvas.alpha_composite(repair,patch_xy)
        self.stow_mask=None
        if self.book_presence<.999:
            _,_,contact,pivot,bsize,_=self.hand_layout(size)
            tuck=1.-self.book_presence*self.book_presence*(3.-2.*self.book_presence)
            cx,cy=contact-wrist
            bx=cx-pivot[0]+tuck*size[1]*.024
            by=cy-pivot[1]-tuck*size[1]*.012
            cs,sn=math.cos(self.hand_angle),math.sin(self.hand_angle)
            start=moved+np.array((cs*bx-sn*by,sn*bx+cs*by))
            cover=canvas.getchannel("A").transform(bsize,Image.Transform.AFFINE,
                     (cs,-sn,start[0],sn,cs,start[1]),Image.Resampling.BILINEAR)
            # The solid coat hides part of the solid cover as it emerges.
            # Only the moving boundary has a one-pixel antialiasing band.
            cloth_depth=distance_transform_edt(np.asarray(cover)>=128)
            reveal=(1.-tuck)*math.hypot(*bsize)
            visible=np.clip(reveal-cloth_depth+.5,0.,1.)
            # The emerging corner stays at the grip. The far corner clears
            # the cuff last, so there is no loose cover floating over the coat.
            yy,xx=np.ogrid[:bsize[1],:bsize[0]]
            from_grip=np.hypot(xx+.5-pivot[0],yy+.5-pivot[1])
            visible=np.minimum(visible,np.clip(reveal-from_grip+.5,0.,1.))
            self.stow_mask=Image.fromarray(np.rint(255*visible).astype(np.uint8))
        return canvas,size,tuple(origin+local)

    def grip_geometry(self, held_frame, size, actor_rect, body, breath):
        _,filename=held_frame
        pos,hsize,contact,pivot,bsize,_=self.hand_layout(size)
        w,h=actor_rect.width(),actor_rect.height()
        ratio=size[1]/h
        if self.moving_hand:
            cs,sn=math.cos(self.hand_angle),math.sin(self.hand_angle)
            vx,vy=contact[0]-.051*size[0],contact[1]-.559*size[1]
            u=.051+(vx*cs-vy*sn)/size[0]
            v=.559+(vx*sn+vy*cs)/size[1]
            px,py=attached_point((u,v),w,h,"p1",body,breath)
            ex,ey=attached_point((u+.085,v-.018),w,h,"p1",body,breath)
            angle=math.degrees(math.atan2(ey-py,ex-px)-math.atan2(-.018*h,.085*w))
            transform=QTransform()
            transform.translate(actor_rect.x()+px,actor_rect.y()+py)
            transform.rotate(angle+math.degrees(self.hand_angle))
        else:
            contact_uv,along,down,_=self.book_plane(actor_rect,body,breath)
            scale=h/self.performance.canvas[1]
            transform=QTransform(*(along/scale),*(down/scale),actor_rect.x()+contact_uv[0],
                                 actor_rect.y()+contact_uv[1])
        hand_rect=QRectF((pos[0]-contact[0])/ratio,(pos[1]-contact[1])/ratio,
                         hsize[0]/ratio,hsize[1]/ratio)
        presence=self.book_presence*self.book_presence*(3.-2.*self.book_presence)
        book_rect=QRectF((-pivot[0]+(1-presence)*size[1]*.024)/ratio,
                         (-pivot[1]-(1-presence)*size[1]*.012)/ratio,bsize[0]/ratio,bsize[1]/ratio)
        def hand(key):
            path=self.performance.support_files[key]
            return hand_rect,self.layer_image(path,hsize)
        image=self.layer_image(self.book_directory/filename,bsize)
        if self.paper_mix<1.:
            idx=self.books["opening_frames_by_state"][self.page].index(filename)
            old=self.books["opening_frames_by_state"][self.previous_page][idx]
            if old!=filename:
                mix=round(12*self.paper_mix*self.paper_mix*(3.-2.*self.paper_mix))
                image=self.mixed_book(old,filename,bsize,mix)
        if self.interrupt_capture and self.action_frame is None:
            opening_index=self.books["opening_frames_by_state"][self.page].index(filename)
            ink=self.performance.exits.closing_ink(self.interrupt_capture,opening_index)
            if ink:
                layer,(x,y)=ink
                book_w,book_h=self.performance.pen["book_size"]
                rw,rh=bsize[0]/book_w,bsize[1]/book_h
                overlay=QImage(layer.tobytes(),*layer.size,layer.width*4,QImage.Format.Format_RGBA8888).copy()
                image=image.copy()
                brush=QPainter(image)
                brush.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                brush.drawImage(QRectF(x*rw,y*rh,layer.width*rw,layer.height*rh),overlay)
                brush.end()
        if self.stow_mask is not None:
            mask=QImage(self.stow_mask.tobytes(),*bsize,bsize[0],QImage.Format.Format_Grayscale8)
            image=image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            image.setAlphaChannel(mask)
        return transform, (hand("grip_back"),(book_rect,image),
                           hand("grip_front"))

    @lru_cache(maxsize=40)
    def mixed_book(self,first,second,size,step):
        result=QImage(*size,QImage.Format.Format_ARGB32_Premultiplied)
        result.fill(0)
        painter=QPainter(result)
        painter.setOpacity(1-step/12.)
        painter.drawImage(0,0,self.layer_image(self.book_directory/first,size))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        painter.setOpacity(step/12.)
        painter.drawImage(0,0,self.layer_image(self.book_directory/second,size))
        painter.end()
        return result

    def book_plane(self,rect,body,breath):
        performance=self.performance
        w,h=rect.width(),rect.height()
        def attached(native):
            u,v=np.asarray(native)/performance.canvas
            return np.asarray(attached_point((u,v),w,h,"p1",body,breath))
        spine=performance.spine
        shoulder=np.array((453.,563.))
        contact=attached(spine)
        along=(attached(spine+(100,0))-contact)/100
        along=self.writing_baseline(along,h/performance.canvas[1],body[4])
        down=(attached(shoulder)-contact-(shoulder[0]-spine[0])*along)/(shoulder[1]-spine[1])
        return contact,along,down,contact-spine[0]*along-spine[1]*down

    def paint_grip(self, painter, held_frame, size, actor_rect, body, breath):
        self.last_drawn_frame=self.action_frame
        self.last_support=np.asarray(body)[[3,4,7]].copy()
        if self.action_frame:
            performance = self.performance
            w,h = actor_rect.width(),actor_rect.height()
            clip,index,_=self.action_frame
            kind,frame=performance.sequences[clip][index]
            contact,along,down,origin=self.book_plane(actor_rect,body,breath)
            correction=self.arm_correction
            if frame["stage"] in ("reach","return") and not frame["original_rest"]:
                # The book and torso use different maps. As the working arm
                # approaches its original ink, bind it to the actual deformed
                # resting wrist and its local orientation, not the book plane.
                u=frame.get("local_frame",frame.get("index"))/16
                blend=u if frame["stage"]=="return" else 1.-u
                blend=blend*blend*(3.-2.*blend)
                if blend:
                    correction=(blend,w,h,*origin,*along,*down,breath,*body)
            if clip.startswith("nib_lift_"):
                image=performance.lift_image(self.interrupt_capture,index,size[1])
            else:
                image=performance.layer(*self.action_frame,size[1],self.stopped_ink,correction)
            l,t,r,b = performance.bounds
            painter.save()
            painter.setOpacity(1.)
            painter.setWorldTransform(QTransform(*along,*down,actor_rect.x()+origin[0],
                                                  actor_rect.y()+origin[1]),True)
            painter.drawImage(QRectF(l,t,r-l,b-t),image)
            painter.restore()
            return
        transform,layers=self.grip_geometry(held_frame,size,actor_rect,body,breath)
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setOpacity(1.)
        painter.setWorldTransform(transform,True)
        for (rect,image),opacity in zip(layers,(self.grasp,1.,self.grasp)):
            painter.setOpacity(opacity)
            painter.drawImage(rect,image)
        painter.restore()

    @staticmethod
    def writing_baseline(along,scale,lift):
        mix=max(0.,min(1.,(lift-.60)/1.6))
        mix=mix*mix*(3.-2.*mix)
        angle=max(-.12,min(.12,math.atan2(along[1],along[0])))
        rigid=scale*np.array((math.cos(angle),math.sin(angle)))
        return (1.-mix)*along+mix*rigid

    def _draw(self, painter, prop, state, height, anchor, opacity):
        item = self.props[prop]["states"][state]
        filename = item["file"]
        if filename not in self.images:
            self.images[filename] = QImage(str(self.directory / filename))
        image = self.images[filename]
        if image.isNull():
            return None
        width = max(1, math.floor(item["width_at_390"] * height / 390 + 0.5))
        scaled_height = max(1, math.floor(width * image.height() / image.width() + 0.5))
        u, v = item["pivot_uv"]
        dest = QRectF(anchor.x() - width * u, anchor.y() - scaled_height * v,
                      width, scaled_height)
        painter.setOpacity(opacity)
        painter.drawImage(dest, image)
        return dest

    def paint(self, painter, *, routine, gesture, actor_rect, pose, body, breath, now, window_pos,
              ground_rect=None, foreground_diary=False, allow_preview=True):
        """Use the actor's unpadded logical rect and window's global top-left.

        The caller supplies d1 (the desktop outfit) or skips this renderer.
        Pass gesture='settle' once a single-gesture preview has ended.
        """
        if routine in self.props["crab"]["routine_ids"]:
            activity, prop = routine, "crab"
        elif routine=="class" or routine in self.props["diary"]["routine_ids"]:
            if foreground_diary:
                return None
            activity, prop = routine, "diary"
        elif allow_preview and not routine and gesture in ("write", "turn_page"):
            if foreground_diary:
                return None
            activity, prop = "preview:" + gesture, "diary"
        else:
            if self.activity is not None:
                self._reset_activity()
            return None
        if self.activity != activity or now < self.last_time:
            self._reset_activity()
            self.activity, self.started, self.last_time = activity, now, now
        if self.last_gesture == "write":
            self.writing_seconds += max(0.0, now - self.last_time)
        self.last_time, self.last_gesture = now, gesture

        w, h = actor_rect.width(), actor_rect.height()
        if w <= 0 or h <= 0:
            return None
        definition = self.props[prop]["placement"]
        uv = definition["actor_anchor_uv"]
        if prop == "crab":
            if ground_rect is None:
                # Recover the standing reference for callers which provide only the pose.
                standing_h = h / (.922 if pose == "p3" else 1.)
                standing_x = actor_rect.center().x() - {"p2": 6, "p3": 11}.get(pose, 0) * standing_h / 390
                floor = actor_rect.bottom() - (3 * standing_h / 390 if pose == "p3" else 0)
                standing_w = standing_h * self.front_aspect
                ground_rect = QRectF(standing_x - standing_w / 2, floor - standing_h, standing_w, standing_h)
            h = ground_rect.height()
            origin = QPointF(window_pos)
            if self.ground is None:
                self.ground = origin + ground_rect.topLeft() + QPointF(uv[0] * ground_rect.width(), uv[1] * h)
            anchor = self.ground - origin
            state = "idle"
        else:
            if pose != definition["pose"]:
                return None
            x, y = attached_point(uv, w, h, pose, body, breath)
            anchor = actor_rect.topLeft() + QPointF(float(x), float(y))
            state = self._diary_state(now)

        if self.state != state:
            self.previous, self.state, self.changed = self.state, state, now
        mix = min(1.0, max(0.0, (now - self.changed) / 0.14)) if self.previous else 1.0
        painter.save()
        try:
            opacity = painter.opacity()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            if self.previous and mix < 1.0:
                self._draw(painter, prop, self.previous, h, anchor, opacity * (1.0 - mix))
            else:
                self.previous = None
            return self._draw(painter, prop, state, h, anchor, opacity * mix)
        finally:
            painter.restore()
