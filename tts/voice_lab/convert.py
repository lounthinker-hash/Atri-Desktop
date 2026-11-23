"""Convert local speech to the trained Atri RVC voice. All models are read from disk."""
import argparse
import json
from pathlib import Path
import tempfile
import numpy as np
import soundfile as sf
import soxr
import torch
from features import ROOT, SR, Features, coarse, generator

def build_index(path):
    rows=json.loads((ROOT/'data/split.json').read_text(encoding='utf8'))['clips']
    vectors=[]
    for r in rows:
        with np.load(ROOT/'data/features'/(r['id']+'.npz')) as data:
            vectors.append(data['content'][::4])
    tmp=path.with_suffix('.tmp.npy')
    np.save(tmp,np.concatenate(vectors))
    tmp.replace(path)

def level_match(source, converted):
    # Apply a smooth RMS envelope from the guide so breaths and silent gaps stay quiet.
    if len(source)<2 or len(converted)<2:
        return converted
    hop=400
    centers=np.unique(np.r_[np.arange(0,len(source),hop),len(source)-1])
    def level(x):
        out=[]
        for c in centers:
            w=x[max(0,c-1000):min(len(x),c+1000)]
            out.append(np.sqrt(np.mean(w*w)+1e-10))
        return np.asarray(out)
    source_level=level(source); gen_level=level(converted)
    gains=np.clip((source_level/gen_level)**.35,.12,2.)
    gains=np.where(source_level<.00012,0.,gains)
    return converted*np.interp(np.arange(len(converted)),centers,gains)

class Converter:
    def __init__(self, checkpoint, device=None, retrieval=.3):
        if not 0<=retrieval<=1: raise ValueError('retrieval must be in [0, 1]')
        self.device=torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
        if self.device.type=='cpu': torch.set_num_threads(min(torch.get_num_threads(),5))
        data=torch.load(checkpoint,map_location='cpu',weights_only=True)
        self.step=data['step']
        self.features=Features(self.device)
        self.g=generator()
        del self.g.enc_q
        self.g.load_state_dict(data['model'],strict=True)
        self.g.eval().to(self.device)
        self.g.remove_weight_norm()
        self.ratio=retrieval
        self.bank=None
        if retrieval:
            bank_path=ROOT/'checkpoints/content-index.npy'
            if not bank_path.is_file(): raise FileNotFoundError('Build the local trained-speaker index with --build-index first')
            self.bank=torch.from_numpy(np.load(bank_path).astype(np.float32)).to(self.device)
            self.square=self.bank.square().sum(1)

    @torch.inference_mode()
    def segment(self,wave,semitones):
        vec,pitch=self.features.extract(wave,SR)
        pitch=pitch*2**(semitones/12)
        base=torch.from_numpy(vec).to(self.device)
        if self.bank is not None:
            result=[]
            for part in base.split(128):
                dist=(part.square().sum(1,keepdim=True)+self.square.unsqueeze(0)-2*part@self.bank.T).clamp_min_(1e-4)
                vals,idx=dist.topk(8,largest=False)
                weight=vals.reciprocal().square();weight/=weight.sum(1,keepdim=True)
                result.append((self.bank[idx]*weight.unsqueeze(-1)).sum(1))
            retrieved=torch.cat(result)
            voiced=torch.from_numpy((pitch[::2][:len(base)]>0).astype('float32')).to(self.device)
            if len(voiced)<len(base):voiced=torch.nn.functional.pad(voiced,(0,len(base)-len(voiced)))
            ratio=(.20+.80*voiced[:,None])*self.ratio
            base=base*(1-ratio)+retrieved*ratio
        phone=base.repeat_interleave(2,dim=0)
        n=min(len(pitch),len(phone))
        q=torch.as_tensor(coarse(pitch[:n]),device=self.device).unsqueeze(0)
        f=torch.as_tensor(pitch[:n],device=self.device).unsqueeze(0)
        lens=torch.tensor([n],device=self.device)
        sid=torch.zeros(1,dtype=torch.long,device=self.device)
        wav=self.g.infer(phone[:n].unsqueeze(0),lens,q,f,sid)[0]
        return wav[0,0].float().cpu().numpy()

    def convert(self,wave,rate,semitones=0):
        src=soxr.resample(np.asarray(wave,dtype=np.float32),rate,SR,quality='HQ') if rate!=SR else np.asarray(wave,dtype=np.float32)
        if not len(src) or not np.isfinite(src).all(): raise ValueError('Empty or nonfinite audio')
        if np.max(np.abs(src))<1e-5:return np.zeros_like(src)
        context=20000
        padded=np.pad(src,context,mode='reflect' if len(src)>1 else 'edge')
        parts=[]
        torch.manual_seed(213)
        for start in range(0,len(src),SR*12):
            size=min(SR*12,len(src)-start)
            wav=self.segment(padded[start:start+size+2*context],semitones)[context:context+size]
            if len(wav)!=size:raise RuntimeError('Insufficient decoder context')
            parts.append(wav)
        result=level_match(src,np.concatenate(parts))
        if not np.isfinite(result).all():raise ValueError('Invalid decoder output')
        return (result/max(1.,float(np.max(np.abs(result)))/.997)).astype('float32')

    def convert_file(self,source,target,semitones=0):
        wave,rate=sf.read(source,dtype='float32',always_2d=True)
        out=self.convert(wave.mean(1),rate,semitones)
        target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent,suffix='.wav',delete=False) as tmp:
            temp=Path(tmp.name)
        try:
            sf.write(temp,out,SR,subtype='PCM_16');temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)

def latest_checkpoint():
    available=list((ROOT/'checkpoints').glob('atri-rvc-*.pt'))
    if not available:raise FileNotFoundError('No trained Atri RVC checkpoint')
    return max(available,key=lambda p:int(p.stem.rsplit('-',1)[-1]))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input',type=Path,nargs='?')
    parser.add_argument('output',type=Path,nargs='?')
    parser.add_argument('--checkpoint',type=Path)
    parser.add_argument('--device',choices=['cpu','cuda'])
    parser.add_argument('--retrieval',type=float,default=.3)
    parser.add_argument('--transpose',type=float,default=0,help='Pitch shift in semitones before conversion')
    parser.add_argument('--build-index',action='store_true')
    args=parser.parse_args()
    if args.build_index:
        folder=ROOT/'checkpoints';folder.mkdir(exist_ok=True)
        build_index(folder/'content-index.npy')
        if not args.input:return
    if args.input is None or args.output is None:
        parser.error('supply input and output WAV paths')
    vc=Converter(args.checkpoint or latest_checkpoint(),args.device,args.retrieval)
    vc.convert_file(args.input,args.output,args.transpose)
    print(args.output,vc.step,SR)
if __name__=='__main__': main()

