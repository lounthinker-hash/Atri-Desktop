"""Select documented diary motifs and draw them beside their source passages."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter


ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
_READING = ASSETS / "props" / "diary-reading"
STICKER_CATALOG = _READING / "stickers" / "manifest.json"
_EARLIER_CATALOG = _READING / "marginalia" / "sources.json"

# Compatibility with the first art delivery, which contained artwork and labels
# but did not yet have textual signals. New motifs come from the current catalog.
_LEGACY = {
    "crab": ("observed", ("螃蟹", "小蟹", "寄居蟹"), ("activity/crab/inspect",)),
    "proud": ("emotion", ("得意", "自豪", "小事一桩", "我是高性能", "我可是高性能"),
              ("activity/testing/proud", "activity/passed/proud")),
    "reading": ("activity", ("看书", "读书", "课本", "书页", "翻书", "翻开书", "读了几页",
                              "读了一会", "翻完一页"), ("activity/class/read", "activity/science/read",
                                                          "activity/class/turn_page")),
}
_SENTENCES = re.compile(r"[^。！？!?；;\r\n]+[。！？!?；;]*")
_UNREAL = re.compile(r"如果|假如|假设|要是|也许|明天|下次|过几天|并未|从未|未曾|没|"
                     r"想象|梦见|梦到|打算|希望|准备去|想去|想要|可能|不曾|不想|并不|别去")
_DISCUSS = re.compile(r"我们聊|聊天|聊到|聊起|谈到|谈起|讨论|提到|提起|说起|告诉我|"
                      r"问我|我问|他问|她问|他说|她说|你说|用户说|照片|图鉴|视频|电视|故事里|"
                      r"书里的|听说|据说|只听|举例|打比方|纸上画")
_SAW = re.compile(r"看见|亲眼|瞧见|遇见|遇到|发现|找到|碰到|观察了|看到了|出现在|就在.{0,3}眼前|"
                  r"在我脚边|挥着|举着双钳|举着钳|举起.{0,3}钳|横着跑|横着爬")
_ACTION = re.compile(r"读|翻|看|复习|学|写|听|唱|跑|走|练|做|试|拿|戴|推|把|喝|吃|"
                     r"弹|摆|取|停|歇|醒|打开|合上|收好|完成|伸|坐|画|挽|放|拍|数")
_THIRD_PARTY = re.compile(r"(?:他|她|对方|同学|用户|妈妈|爸爸|老师).{0,7}(?:得意|自豪|骄傲)")
_OTHER_EVENT = re.compile(r"(?:你|您|他|她|用户|对方|同学|朋友|老师|妈妈|爸爸).{0,15}"
                          r"(?:亲眼|看见|看到|遇见|遇到|发现|观察|找到|捡|拾|拿起|握|捧|读|翻|弹|写|得意|自豪)")
_SELECTION_VERSION = "catalog-basis-v5"
_KINDS={"observed":"observed","activity":"performed","conversation":"conversation_topic",
        "emotion":"self_reported_feeling"}


def catalog_signature():
    path = STICKER_CATALOG if STICKER_CATALOG.is_file() else _EARLIER_CATALOG
    if not path.is_file():
        return _SELECTION_VERSION+"/empty"
    return _SELECTION_VERSION+"/"+hashlib.sha256(path.read_bytes()).hexdigest()


def _words(value):
    if isinstance(value,str):
        return tuple(w.strip() for w in re.split(r"[，、,;；|]",value) if w.strip())
    if isinstance(value,(list,tuple)):
        return tuple(w.strip() for w in value if isinstance(w,str) and w.strip())
    return ()


def _catalog():
    catalog = STICKER_CATALOG if STICKER_CATALOG.is_file() else _EARLIER_CATALOG
    if not catalog.is_file():
        return {}
    data = json.loads(catalog.read_text(encoding="utf-8-sig"))
    motifs = {}
    for key,item in data["assets"].items():
        path = (catalog.parent/item["file"]).resolve()
        if not path.is_relative_to(ASSETS) or not path.is_file():
            continue
        topic = item.get("topic",key)
        legacy = _LEGACY.get(key,("topic",(),()))
        rule = item.get("match",{})
        if not isinstance(rule,dict):
            rule = {}
        words = (_words(rule.get("keywords")) or _words(item.get("keywords")) or
                 _words(item.get("terms")) or _words(item.get("tags")) or legacy[1])
        if not words:
            words = _words(item.get("topicZh")) or _words(item.get("label")) or (topic,)
        fallback_mode = "emotion" if "faces" in path.relative_to(ASSETS).parts else legacy[0]
        mode = rule.get("mode",item.get("evidenceMode",fallback_mode))
        sources = _words(rule.get("sources")) or _words(item.get("sources")) or legacy[2]
        basis=set(_words(item.get("basis"))) or {_KINDS.get(mode,"conversation_topic")}
        motifs[key] = dict(id=key,topic=topic,alt=item.get("alt",item.get("label",topic)),
                           file=path.relative_to(ROOT).as_posix(),words=words,mode=mode,
                           basis=basis,constraints=item.get("constraints",{}),sources=sources,
                           source=catalog.relative_to(ROOT).as_posix()+"#assets/"+key)
    return motifs


def _sentences(text):
    for match in _SENTENCES.finditer(text):
        yield match.start(),match.group()


_MEDIA = re.compile(r"海报|屏幕|书(?:上|中|里|内)(?:的|印|画|插|图)?|插图|照片|图鉴|"
                   r"图片|图像|画像|画册|漫画|画报|海报|电视|视频|投影|相册|电影|广告牌|绘本|展板|截图|游戏里")
_PIVOT = r"后来|随后|现实里|现实中|不过|但是|但今天|但我们|可今天|事实上|转过身|转身后|可到了|等我到了"
_TALK = re.compile(r"我们聊|聊天|聊到|聊起|谈到|谈起|讨论|提到|提起|说起|告诉我|问我|"
                   r"我问|他问|她问|他说|她说|你说|用户说|听说|对方说|对方问|聊了|谈了|"
                   r"见闻写给我|转述|[聊谈]过")
_IMAGINE=re.compile(r"想象|想着想着|想出来|想出.{0,6}样子|幻想|梦(?:里|中|见|到)|到时候|"
                    r"如果|假如|假设|要是|明天|下次|但愿|愿望|希望将来|这样.{0,8}一(?:照|来)")
_WEATHER=re.compile(r"吹过|吹来|吹进|刮起|洒在|照进|照在|落在|打在|拍岸|拍在|"
                    r"亮起来|响起|下起|飘下|飘起|天放晴|云散了|窗外正")
_OLD_OBJECT=re.compile(r"(?:想起|记起|记得|忆起)(?:了)?(?:上次|以前|之前|当时|那[枚只艘张本些次条个件])")
_PERFORMED=re.compile(r"我(?:们)?.{0,12}(?:拿|戴|穿|写|读|翻|折|量|拴|按|敲|弹|吃|喝|咀嚼|嚼|咬|尝|品尝|夹|"
                      r"画|用|裁|拾|剪|贴|戴|摘|放|做|打开|冲|看了|查看|看着)|"
                      r"读了几页|翻开书|翻书|读书|翻了|裁开|折了|拿起|翻到")


_DIRECT_HAND=re.compile(r"(?:拾起|捡起|拾到|捡到|拿起|拿着|拿过|捡|拾|捧起|摸到|握住|翻开|翻看|打开)"
                        r"(?:了|来|过)?(?:这|那|一|两)?(?:本|张|枚|只|个|块|片|件|把|对)?(?:旧|新|小|大)?$")


def _hit(sentence,art,*,speaker=None):
    """Locate the particular mention that an illustration can represent."""
    # A turn such as “后来在岸边” starts a new event even within a long sentence.
    split = re.compile(r"[，,；;。！？!?]|(?="+_PIVOT+r")")
    start = 0
    chat_context = speaker is not None
    media_context = conditional = False
    hits = []
    for boundary in [*split.finditer(sentence),None]:
        end = boundary.start() if boundary else len(sentence)
        clause = sentence[start:end]
        begin = start
        start = boundary.end() if boundary else end
        if not clause.strip():
            continue
        if re.match(_PIVOT,clause):
            media_context = conditional = False
            chat_context = speaker is not None
        chat_context |= bool(_TALK.search(clause))
        if _IMAGINE.search(clause):
            conditional = True
        media = bool(_MEDIA.search(clause))
        media_ref = media or (media_context and bool(re.search(r"里面|上面|画里|图上|其中|那上头",clause)))
        media_context |= media
        mentions=[]
        for word in art["words"]:
            if word.isascii() and word[0].isalnum():
                match=re.search(r"(?<![A-Za-z0-9_])"+re.escape(word)+r"(?![A-Za-z0-9_])",clause,re.I)
                offset=match.start() if match else -1
            else:
                offset=clause.find(word)
            if offset>=0:
                mentions.append((offset,word))
        if not mentions:
            continue
        offset,term = min(mentions,key=lambda n:(n[0],-len(n[1])))
        before = clause[:offset]
        prefix=sentence[:begin+offset]
        quoted=(prefix.count("「")>prefix.count("」") or prefix.count("“")>prefix.count("”")
                or prefix.count("『")>prefix.count("』") or prefix.count('"')%2==1)
        discussion = chat_context or quoted
        hypothetical = conditional or bool(_UNREAL.search(clause))
        allowed=art["basis"]
        if hypothetical:
            continue
        if discussion:
            if "conversation_topic" not in allowed:
                continue
            kind="conversation"
        else:
            if (_OTHER_EVENT.search(clause) or _THIRD_PARTY.search(clause) or
                    re.search(r"(?:你|您|他|她|用户|对方|同学|朋友|老师).{0,7}$",before) or
                    re.search(r"吗|[？?]",clause)):
                continue
            # Distinguish the object held in the hand from something printed on it.
            direct_hand=bool(_DIRECT_HAND.search(before))
            after=clause[offset+len(term):]
            represented=bool(re.match(r"(?:的)?(?:图案|图画|图片|照片|插画|贴画|图标|模型|玩具)",after))
            holder=(art["id"]=="photo_album" and direct_hand and not _MEDIA.search(before)
                    and not represented)
            material=not media_ref and not represented
            lived = material and (_SAW.search(clause) or direct_hand or
                                  ("/coast/" in art["file"] and _WEATHER.search(clause)))
            performed = _PERFORMED.search(clause) or ("performed" in allowed and
                         art["constraints"].get("subject")=="atri_self" and _ACTION.search(clause))
            if "self_reported_feeling" in allowed:
                kind="emotion"
            elif "performed" in allowed and direct_hand and (material or holder):
                kind="activity"
            elif "observed" in allowed and lived:
                kind="observed"
            elif "performed" in allowed and ((performed and (material or art["id"]=="reading")) or holder):
                kind="activity"
            else:
                continue
        hits.append((begin+offset,term,kind))
    return hits[0] if hits else None

def _asset_row(art):
    return {k:art[k] for k in ("id","topic","file","alt","source")}


def choose(pages,facts,previous=(),citations=None):
    """Keep one illustration per supported passage and motif, with no daily quota."""
    citations = citations or {}
    pages = [dict(p) for p in pages]
    facts = [dict(f) for f in facts]
    fact_by_id = {f["fact"]:f for f in facts}
    positions = {p["id"]:n for n,p in enumerate(pages)}
    page_by_fact = {p.get("fact"):p["id"] for p in pages}
    fact_pages = {}
    for page in pages:
        for fact in citations.get(page["id"],()):
            fact_pages.setdefault(fact,[]).append(page["id"])
    # Early editions sometimes attached an observed gesture to a date without
    # writing an entry ID. Verify it against the original immutable evidence.
    for old in previous:
        basis=old.get("basis",{})
        linked=[p for p in basis.get("entry_ids",()) if p in positions]
        for fact in basis.get("facts",()):
            if fact in fact_by_id and fact not in fact_pages and fact not in page_by_fact:
                fact_pages.setdefault(fact,[]).extend(linked)
    arts = _catalog()
    proposals = []
    for art in arts.values():
        for page in pages:
            page_art=art
            month=art["constraints"].get("depictedMonth")
            if month and page.get("day","")[5:7]==f"{month:02d}":
                page_art={**art,"words":(*art["words"],"台历","日历","月历","挂历")}
            cites = [fact_by_id[key] for key in citations.get(page["id"],()) if key in fact_by_id]
            solely_reported = bool(cites) and all(f["source"].startswith("chat/") for f in cites)
            for start,sentence in _sentences(page["text"]):
                hit = _hit(sentence,page_art)
                if not hit:
                    continue
                if solely_reported and hit[2] in ("observed","activity"):
                    # A user's real-world observation is still the user's. A
                    # paragraph based only on messages cannot certify Atri saw it.
                    continue
                offset,term,kind = hit
                strength = 1 if kind=="text" else 3
                proposals.append((strength,page["id"],start,start+offset,art,kind,term,sentence.strip(),[]))
        for f in facts:
            linked = list(fact_pages.get(f["fact"],()))
            if f["fact"] in page_by_fact:
                linked.append(page_by_fact[f["fact"]])
            if not linked:
                continue
            source = f["source"]
            observed = source.startswith("activity/") and any(
                source==s or (s.endswith("/*") and source.startswith(s[:-1])) for s in art["sources"])
            is_chat = source.startswith("chat/")
            hit = _hit(f["wording"],art,speaker=source if is_chat else None) if is_chat else None
            if not observed and not hit:
                continue
            for pid in set(linked):
                page = pages[positions[pid]]
                # A recorded action cannot turn the explicit denial on its page into an encounter.
                passages = list(_sentences(page["text"]))
                relevant = [(s,t) for s,t in passages if any(w in t for w in art["words"])]
                if observed and relevant and all(not _hit(t,art) for _,t in relevant):
                    continue
                if is_chat:
                    actual_talk=[(s,t) for s,t in relevant if (h:=_hit(t,art)) and h[2]=="conversation"]
                    if not actual_talk:
                        actual_talk=[(s,t) for s,t in relevant if _OLD_OBJECT.search(t)
                                     and _hit(t,art,speaker="recalled in this dialogue")]
                    # If the manuscript explicitly imagines or denies the
                    # motif, an incidental word in a cited chat cannot undo it.
                    talk=actual_talk if relevant else []
                    if not talk:
                        continue
                    start,sentence=talk[0]
                elif relevant:
                    start,sentence=next((p for p in relevant if _hit(p[1],art)),relevant[0])
                else:
                    action = f["source"].rsplit("/",1)[-1]
                    cue = {"read":r"细读|读了读|仔细读|读完|复习|逐段读",
                           "turn_page":r"翻.{0,4}页","proud":r"得意|挺起胸|骄傲"}.get(action)
                    matched = next(((s,t) for s,t in passages if cue and re.search(cue,t)),None)
                    if not matched:
                        continue
                    start,sentence=matched
                text_hit = _hit(sentence,art,speaker="cited conversation" if is_chat else None)
                offset = start+(text_hit[0] if text_hit else 0)
                term = text_hit[1] if text_hit else hit[1] if hit else ""
                kind = "conversation" if is_chat else ("observed" if art["basis"]=={"observed"} else
                       "activity" if "performed" in art["basis"] else "emotion")
                score = 5 if text_hit else 2
                proposals.append((score,pid,start,offset,art,kind,term,f["wording"].strip(),[f["fact"]]))
    proposals.sort(key=lambda p:(-p[0],p[1],p[2],-len(p[6]),p[4]["id"]))
    result, used, occupied = [], set(), set()
    for binding in previous:
        binding = dict(binding)
        ids=binding.get("basis",{}).get("entry_ids",())
        matches=[p for p in proposals if p[4]["id"]==binding["id"] and (not ids or p[1] in ids)]
        if not matches or binding["id"] in used:
            continue
        match=next((p for p in matches if p[3]==binding.get("anchorOffset")),matches[0])
        _,pid,beat,offset,_,kind,term,excerpt,source_facts=match
        binding.update(anchorOffset=offset,passageStart=beat,
                       basis={"kind":kind,"catalogKind":_KINDS[kind],"entry_ids":[pid],"facts":source_facts,
                              "term":term,"excerpt":excerpt})
        binding["anchorParagraph"] = positions[pid]
        result.append(binding)
        used.add(binding["id"])
        occupied.add((pid,binding.get("passageStart",0)))
    for score,pid,beat,offset,art,kind,term,excerpt,source_facts in proposals:
        if art["id"] in used or (pid,beat) in occupied:
            continue
        row = _asset_row(art)
        row.update(anchorParagraph=positions[pid],anchorOffset=offset,passageStart=beat,
                   basis={"kind":kind,"catalogKind":_KINDS[kind],"entry_ids":[pid],"facts":source_facts,
                          "term":term,"excerpt":excerpt})
        result.append(row)
        used.add(art["id"])
        occupied.add((pid,beat))
    block_starts=[]
    block=0
    for page in pages:
        block_starts.append(block)
        block+=len(re.split(r"\r\n|\r|\n",page["text"]))
    for row in result:
        index=positions[row["basis"]["entry_ids"][0]]
        before=pages[index]["text"][:row["anchorOffset"]]
        parts=re.split(r"\r\n|\r|\n",before)
        row["anchorEntryIndex"]=index
        row["anchorParagraph"]=block_starts[index]+len(parts)-1
        row["paragraphOffset"]=len(parts[-1])
    return sorted(result,key=lambda r:(r["anchorEntryIndex"],r["anchorOffset"],r["id"]))


def for_display(rows):
    result = []
    for item in rows:
        path = (ROOT/item["file"]).resolve()
        if path.is_relative_to(ASSETS) and path.is_file():
            result.append({**item,"file":str(path)})
    return result


@lru_cache(maxsize=96)
def _image(path):
    return QImage(path)


def paint_stickers(painter,rows,available_rect,*,anchors=None,drawn_ids=None):
    """Place the currently visible passage marks in the left margin."""
    if not rows or available_rect.isEmpty():
        return []
    edge = min(available_rect.width(),45.,max(26.,available_rect.width()*.34))
    gap = max(4.,edge*.20)
    top,bottom = available_rect.top()+edge/2, available_rect.bottom()-edge/2
    if bottom<top:
        return []
    if anchors is None:
        anchors = [top+(i+.5)*(bottom-top)/len(rows) for i in range(len(rows))]
    visible = sorted(((max(top,min(bottom,y)),i) for i,y in enumerate(anchors) if y is not None),
                     key=lambda item:(item[0],item[1]))
    if not visible:
        return []
    room = max(1,int((bottom-top)/(edge+gap))+1)
    if len(visible)>room:
        # Only physical space limits one viewport. Scrolling recomputes its candidates.
        visible = [visible[round(i*(len(visible)-1)/max(1,room-1))] for i in range(room)]
    centers=[]
    for y,i in visible:
        centers.append(max(y,centers[-1]+edge+gap) if centers else y)
    centers[-1]=min(centers[-1],bottom)
    for i in range(len(centers)-2,-1,-1):
        centers[i]=min(centers[i],centers[i+1]-edge-gap)
    painter.save()
    try:
        painter.setClipRect(available_rect,Qt.ClipOperation.IntersectClip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        drawn=[]
        for (_,i),y in zip(visible,centers):
            row=rows[i]
            image=_image(row["file"])
            if image.isNull():
                continue
            factor=min(edge/image.width(),edge/image.height())
            w,h=image.width()*factor,image.height()*factor
            rect=QRectF(available_rect.center().x()-w/2,y-h/2,w,h)
            painter.drawImage(rect,image)
            if drawn_ids is not None:
                drawn_ids.append(row["id"])
            drawn.append(rect)
        return drawn
    finally:
        painter.restore()
