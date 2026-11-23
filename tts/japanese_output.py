"""Validate Japanese reading scripts at the desktop and local speech service boundaries."""
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata


@lru_cache(maxsize=4)
def original_catalog(root):
    rows=json.loads((root/"assets/voice/all.json").read_text(encoding="utf-8-sig"))
    return {row["id"]:row for row in rows}


@lru_cache(maxsize=1)
def _original_japanese_phrases():
    # Common script labels are absent as stand-alone compounds in the game's
    # dialogue. They also occur in short replies ending in the Japanese copula.
    known={"日本語","英語","中国語","日記","新作",*_COPULA_REPLIES}
    for row in original_catalog(Path(__file__).resolve().parents[1]).values():
        known.update(re.findall(r"[㐀-鿿々]+",row.get("ja") or ""))
    return known


_HAN = re.compile(r"[㐀-鿿々]+")
_KANA = re.compile(r"[ぁ-ゖァ-ヺ]")
# These response nouns take the Japanese copula. Open JTalk's dictionary
# analyses them as サ変接続 with readings リョウカイ, ショウチ and サンセイ.
# They can appear in a written Japanese response even if the game's scripts
# contain no example of that exact inflected form.
_COPULA_REPLIES = {"了解","承知","賛成"}
# These dialogue replies can also form a complete acoustic segment with no
# kana (e.g. 大丈夫？ before or after a sentence). Their stems and readings
# occur in Atri's Japanese recordings: b114_016, b111_055, b123_022,
# b114_054 and b304_033 respectively.
_SHORT_REPLIES = {"大丈夫","平気","結構","無理","本当"}
_QUOTED = re.compile(r'「([^「」]*)」|『([^『』]*)』|“([^“”]*)”|"([^"]*)"|［([^［］]*)］|\(([^()]*)\)')
# A name, title or copula can be pasted into a Chinese sentence. Count
# the kana elsewhere before treating it as evidence of Japanese syntax.
_WEAK_KANA = re.compile(r"(?:なん)?(?:ですから|ですよね|です(?:よ|ね|か)?|でした|だよ|だね|だ|ます|ました|ございます)|"
                        r"さん|さま|ちゃん|くん|[ァ-ヺー]+")
_MANDARIN = re.compile(r"你|您|妳|我们|我們|他们|他們|她们|她們|什么|什麼|哪儿|哪兒|怎么|怎麼|"
                       r"没事|沒事|有没有|有沒有|这是|這是|这个|這個|正在|今天|昨天|明天|"
                       r"我(?:想|在|要|把|和)|想(?:听|聽)|一起(?:走|去|來|来)|可以(?:听|聽|看)|"
                       r"写完了|寫完了|[ぁ-ヿ]的[㐀-鿿]")


def _japanese_clause(clause):
    if not any(c.isalnum() for c in clause):
        return True
    if not _KANA.search(clause):
        return re.sub(r"[^\w々]","",clause) in _original_japanese_phrases()
    imports_only = not re.search(r"[ぁ-ゖ]", _WEAK_KANA.sub("",clause))
    if imports_only:
        return all(term in _original_japanese_phrases() for term in _HAN.findall(clause) if term!="〇")
    return True


def is_japanese_reading(text):
    """Conservative script check for synthesis. Trusted original recordings use clip IDs."""
    if not isinstance(text,str):return False
    value=unicodedata.normalize("NFKC",text)
    # Written acronyms such as USB are common in a Japanese sentence. They
    # cannot qualify a stand-alone English phrase.
    value=re.sub(r"(?<![a-zA-Z])[A-Z]{1,6}(?![a-zA-Z])","〇",value)
    for c in value:
        if c.isalpha() and not re.fullmatch(r"[ぁ-ヿ㐀-鿿々〆〇]",c):
            return False
        if _HAN.fullmatch(c):
            try:c.encode("cp932")
            except UnicodeEncodeError:return False
    if _MANDARIN.search(value):return False
    # Judge the quoted words and the surrounding narrator separately. A
    # Japanese quotation does not establish the language of the outer text.
    while match:=_QUOTED.search(value):
        quoted=next(s for s in match.groups() if s is not None)
        if any(c.isalnum() for c in quoted):
            term=quoted.strip(" \t\n。!?！？…")
            if not (_HAN.fullmatch(term) and term in _original_japanese_phrases()) and not is_japanese_reading(quoted):
                return False
        rest=value[:match.start()]+" "+value[match.end():]
        if not any(c.isalnum() for c in rest):
            return bool(_KANA.search(quoted)) or quoted.strip(" \t\n。!?！？…") in _SHORT_REPLIES
        value=rest
    sentences=[s for s in re.split(r"[。！？!?;；:：\n]+|\.(?!\d)",value) if any(c.isalnum() for c in s)]
    return bool(sentences) and all(
        sentence.strip(" \t\n…") in _SHORT_REPLIES or (
            _KANA.search(sentence) and all(_japanese_clause(c) for c in re.split(r"[,、]",sentence))
        ) for sentence in sentences
    )
