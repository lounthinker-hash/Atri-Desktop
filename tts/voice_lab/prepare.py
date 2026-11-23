"""Build a split with transcript disjointness, then encode paired content, F0 and target wave."""
import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy import signal
import soundfile as sf
import soxr
import torch
from features import Features, ROOT, SR

RESERVED = """b102_081 b111_029 b112_072 b114_037 b122_014 b201_020 b204_074 b206_100 b207_057 b303_053 b405_071 b407_057 b111_034 b405_026 b101_013 b302_045 b302_047 b401_076 b205_012""".split()
RESERVED = {'ATR_'+s for s in RESERVED}
PROJECT=ROOT.parent.parent
def normalized(text):
    return ''.join(c for c in unicodedata.normalize("NFKC",text or '') if c.isalnum())
def stable(id):
    return hashlib.sha256(('atri-v2-split-213/'+id).encode()).hexdigest()
def select(catalog, reserved):
    rows=json.loads(catalog.read_text(encoding='utf-8-sig'))
    missing=reserved-{r['id'] for r in rows}
    if missing:
        raise ValueError("Unknown held-out recording IDs: "+", ".join(sorted(missing)))
    held={normalized(r['ja']) for r in rows if r['id'] in reserved}
    by=defaultdict(list)
    for row in rows:
        text=normalized(row.get('ja'))
        if row['id'] in reserved or text in held or not 1.3<=row['duration']<=8.6 or len(text)<4 or not row.get('row',True):
            continue
        if not re.fullmatch(r'[\w-]+',row['id']):
            raise ValueError("Use letters, digits, underscores or hyphens in recording IDs.")
        by[row['scene']].append(row)
    chosen=[];seen=set()
    def add(r):
        norm=normalized(r['ja'])
        if norm in seen:return False
        seen.add(norm);chosen.append(r);return True
    style=re.compile(r'高性能|エッヘン|えっへん|当然|おまかせ|お任せ|まかせて|任せて|得意|さすが|ばっちり|でしょ[う？!！]|自信|もちろん')
    for scene,group in sorted(by.items()):
        ranked=sorted(group,key=lambda r:stable(r['id']))
        priority=sorted([r for r in ranked if style.search(r['ja']) or r.get('mood') in ('自信','开心')],key=lambda r:('高性能' not in r['ja'],stable(r['id'])))
        for r in priority[:5]:add(r)
        for r in [r for r in ranked if '！' in r['ja']][:2]:add(r)
        count=0
        for row in ranked:
            if add(row):count+=1
            if count==5:break
    return sorted(chosen,key=lambda r:r['id']),held
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--device',default='cpu')
    p.add_argument('--select-only',action='store_true')
    p.add_argument('--catalog',type=Path,help='JSON list of recordings and Japanese transcripts')
    p.add_argument('--audio-root',type=Path,help='base directory for relative audio paths')
    p.add_argument('--holdout-id',action='append',help='recording ID reserved for evaluation (repeatable)')
    args=p.parse_args()
    catalog=args.catalog or PROJECT/'assets/voice/all.json'
    audio_root=args.audio_root or (catalog.parent if args.catalog else PROJECT)
    reserved=set(args.holdout_id) if args.holdout_id is not None else (set() if args.catalog else RESERVED)
    rows,hold=select(catalog,reserved)
    if not rows:
        p.error('No eligible training recordings in the catalog.')
    path=ROOT/'data';path.mkdir(exist_ok=True)
    manifest={'seed':213,'excluded_ids':sorted(reserved),'excluded_texts_normalized':sorted(hold),
              'clips':[{k:r.get(k,'') for k in ('id','file','ja','scene','duration','mood')} for r in rows]}
    (path/'split.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    print('paired',len(rows),'chapters',len({r['scene'] for r in rows}),'minutes',round(sum(r['duration'] for r in rows)/60,2),
          'high-performance',sum('高性能' in r['ja'] for r in rows),'held',len(reserved),flush=True)
    if args.select_only:return
    torch.set_num_threads(5)
    encoder=Features(args.device)
    coeff=signal.butter(5,48.,btype='highpass',fs=SR,output='sos')
    packed=path/'features';packed.mkdir(exist_ok=True)
    for i,r in enumerate(rows,1):
        target=packed/(r['id']+'.npz')
        if not target.exists():
            wav,rate=sf.read(audio_root/r['file'],dtype='float32',always_2d=True)
            wav=wav.mean(axis=1)
            wav=soxr.resample(wav,rate,SR,quality='HQ')
            wav=signal.sosfilt(coeff,wav).astype('float32')
            wav=wav*(.675/max(float(np.abs(wav).max()),1e-5)+.25)
            content,f0=encoder.extract(wav,SR)
            dest=target.with_suffix('.tmp.npz')
            np.savez(dest,content=content.astype(np.float16),f0=f0,wave=np.rint(np.clip(wav,-.999,.999)*32767).astype('int16'))
            dest.replace(target)
        if i%40==0:print('prepared',i,len(rows),flush=True)
if __name__=='__main__':main()

