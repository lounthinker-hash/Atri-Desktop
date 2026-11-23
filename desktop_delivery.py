"""Select a speaking gesture and a fallback facial performance for actual game lines."""
import re

# Wording takes precedence over the surrounding activity. A greeting delivered
# during study is still a greeting, and a printed musical note alone isn't a song.
_LINES = {
    "ATR_b122_004": ("listen","attentive"),
    "ATR_b124_001": ("hum","humming"),
    "ATR_b405_160": ("hum","humming"),
    "ATR_b121_025": ("cheer","happy"),
    "ATR_b121_026": ("sway","humming"),
    "ATR_b121_014": ("point","curious"),
    "ATR_b407_100": ("think","thoughtful"),
    "ATR_b301_017": ("lean","reassuring"),
    "ATR_b404_025": ("wait","reassuring"),
    "ATR_b303_045": ("lean","worried"),
    "ATR_b303_046": ("nod","reassuring"),
}
_MOODS = {
    "问候": ("greet","happy"), "晚安": ("rest","goodnight"), "关心": ("lean","reassuring"),
    "感谢": ("bow","content"), "好奇": ("think","curious"), "自信": ("proud","proud"),
    "学习": ("nod","focused"), "开心": ("sway","happy"),
}
_ROUTINES = {
    "hum": ("hum","humming"), "rest": ("rest","goodnight"), "retry": ("lean","reassuring"),
    "watch": ("wait","relaxed"), "passed": ("proud","happy"), "testing": ("think","focused"),
    "science": ("nod","focused"), "class": ("nod","attentive"), "morning": ("greet","happy"),
    "notes": ("look_down","thoughtful"), "evening_log": ("look_down","thoughtful"),
    "cook": ("point","curious"), "lamps": ("think","thoughtful"), "crab": ("point","curious"),
}


_EXAMPLES = {
    "greeting": ("greet","happy"), "comfort": ("lean","reassuring"),
    "excitement": ("cheer","excited"), "confidence": ("proud","proud"),
}


def delivery(clip, routine, example=None):
    line = clip.get("zh") or ""
    if example and example["category"] in _EXAMPLES:
        return _EXAMPLES[example["category"]]
    if clip["id"] in _LINES:
        return _LINES[clip["id"]]
    if "晚安" in line:
        return "rest","goodnight"
    if "没事吧" in line:
        return "lean","worried"
    if "为什么笑" in line:
        return "think","curious"
    if any(word in line for word in ("早上好","欢迎回来","早安")):
        return "greet","happy"
    if "哼哼" in line or "哼了一段" in line:
        return "hum","humming"
    if "打了个盹" in line:
        return "doze","goodnight"
    if "复习" in line or "读了一会儿" in line:
        return "read","focused"
    if any(word in line for word in ("别怕","没事了","陪着","放心","不要难过","请安心")):
        return "lean","reassuring"
    return _MOODS.get(clip.get("mood"),_ROUTINES.get(routine,("nod","attentive")))


_RECOVERED=re.compile(r"没事了|没事就好|没事啊|松了(?:一)?口气|平安就好|吓了一跳.{0,4}还好|ご無事|よかった|もう大丈夫")
_RELIEF=re.compile(r"不用担心|别担心|不必担心|别怕|请?放心(?!不下)|安心して|心配(?:ご無用|ない|いらない)|大丈夫ですよ")
_WORRY=re.compile(r"没事吧|还好吗|还好[吗么]|没受伤吧|有没有受伤|担心|放心不下|害怕|吓到了|好怕|心配|(?:大丈夫|だいじょうぶ|だいじょぶ)ですか")
_TURN=re.compile(r"^(?:[“「『（(]\s*)?(?:不过|可是|但是|所幸|好在|幸好|还好|现在|还好你|你没事就好|请放心|不用担心|别担心|松了|でも|だけど|けれど|もう大丈夫|よかった)")
_CLAUSE=re.compile(r"(?:[。！？!?；;]+|(?<!\d)\.(?=\s|$)|[，,]|…{2,})[”」』’）)]*")
_UNPUNCTUATED_TURN=re.compile(r"不过|可是|但是|好在|幸好|所幸|还好|でも|だけど|けれど")


def reply_script(text):
    """Insert line breaks where a separate synthesis segment can carry a new delivery."""
    lines=[]
    for paragraph in text.split("\n"):
        start=0
        for mark in _CLAUSE.finditer(paragraph):
            rest=paragraph[mark.end():].lstrip()
            if not rest:
                continue
            soft=mark.group()[0] in ",，…"
            if soft and not (_TURN.match(rest) or
                    (_WORRY.search(paragraph[start:mark.start()]) and
                     (_RELIEF.search(rest[:18]) or _RECOVERED.search(rest[:18])))):
                continue
            lines.append(paragraph[start:mark.end()])
            start=mark.end()
        lines.append(paragraph[start:])
    parts=[]
    for line in lines:
        start=0
        for transition in _UNPUNCTUATED_TURN.finditer(line):
            pos=transition.start()
            tail=line[transition.end():]
            if _WORRY.search(line[start:pos]) and (_RELIEF.search(tail) or _RECOVERED.search(tail)):
                parts.append(line[start:pos])
                start=pos
        parts.append(line[start:])
    return "\n".join(parts)


def segment_delivery(segment, examples):
    text = segment["text"]
    # Some recordings supply an explicit performance choice per rendered chunk.
    # The same words are used both to reassure someone and to assert competence.
    context={"confident":("proud","proud"),"worried":("lean","worried"),
             "comfort":("lean","reassuring")}.get(segment.get("style"))
    if context:
        return context
    spoken=segment.get("spoken_ja",text).strip().strip("「」『』。！!、，. ")
    if spoken in {"うん","はい","了解です","了解しました","わかりました","分かりました"}:
        return "nod","attentive"
    if any(word in text for word in ("晚安","おやすみ")):
        return "rest","goodnight"
    if _RECOVERED.search(text):
        return "nod","reassuring"
    if _RELIEF.search(text):
        return "lean","reassuring"
    if _WORRY.search(text):
        return "lean","worried"
    style = segment.get("emotion")
    styles = {"gentle":("lean","reassuring"), "proud":("proud","proud"), "cheerful":("sway","happy")}
    if style in styles:
        return styles[style]
    if any(word in text for word in ("ふふん","高性能ですから")):
        return "proud","proud"
    reference = examples.get(segment.get("reference_clip_id")) if style != "natural" else None
    return delivery({"id":"","zh":text},"notes",reference)
