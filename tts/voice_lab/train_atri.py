"""Adapt the official RVC-v2 40 kHz generator and discriminator to local recordings."""
import argparse
import json
import os
import random
import time
from pathlib import Path
import numpy as np
import torch
import torch.nn.functional as F
from features import ROOT, SR, HOP, SEGMENT, coarse, generator
from infer.module import commons
from infer.module.models import MultiPeriodDiscriminatorV2
from train.losses import discriminator_loss, feature_loss, generator_loss, kl_loss
from train.mel_processing import mel_spectrogram_torch, spectrogram_torch, spec_to_mel_torch

def safe_save(value,path):
    tmp=path.with_suffix('.partial');torch.save(value,tmp);tmp.replace(path)

def sample_batch(examples, batch, device, rng):
    # A context window around randomly located training segments caps input lengths on 12 GB GPUs.
    fixed=128
    chosen=rng.sample(examples,batch)
    ys=[];cs=[];ps=[];ds=[]
    for ex in chosen:
        wave,content,f0=ex
        n=min(len(wave)//HOP,len(content)*2,len(f0))
        start=rng.randrange(max(n-fixed+1,1)); use=min(n,fixed)
        fs=np.arange(start,start+use)
        d=f0[fs]
        c=content[fs//2].astype(np.float32)
        w=wave[start*HOP:(start+use)*HOP].astype(np.float32)/32767.
        ys.append(np.pad(w,(0,(fixed-use)*HOP)))
        cs.append(np.pad(c,((0,fixed-use),(0,0))))
        ps.append(np.pad(d,(0,fixed-use)))
        ds.append(use)
    wav=torch.as_tensor(np.stack(ys),device=device)
    c=torch.as_tensor(np.stack(cs),device=device)
    f0=np.stack(ps)
    f=torch.as_tensor(f0,device=device)
    q=torch.as_tensor(coarse(f0),device=device)
    lengths=torch.tensor(ds,device=device,dtype=torch.long)
    with torch.no_grad():
        spec=spectrogram_torch(wav,2048,SR,HOP,2048,center=False)
    return wav.unsqueeze(1),c,q,f,lengths,spec

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--steps',type=int,default=450)
    parser.add_argument('--batch',type=int,default=4)
    parser.add_argument('--save-every',type=int,default=100)
    parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    work=ROOT/'checkpoints';work.mkdir(exist_ok=True)
    manifest=json.loads((ROOT/'data/split.json').read_text(encoding='utf8'))
    data=[]
    for r in manifest['clips']:
        with np.load(ROOT/'data/features'/(r['id']+'.npz')) as p:
            data.append((p['wave'],p['content'],p['f0']))
    rng=random.Random(213);torch.manual_seed(213);torch.set_num_threads(3)
    device=torch.device('cuda')
    torch.backends.cuda.matmul.allow_tf32=True
    torch.backends.cudnn.allow_tf32=True
    g=generator()
    d=MultiPeriodDiscriminatorV2(False)
    for name,net in [('G',g),('D',d)]:
        ckpt=torch.load(ROOT/f'assets/pretrained_v2/f0{name}40k.pth',map_location='cpu',weights_only=True)
        net.load_state_dict(ckpt['model'],strict=True)
        del ckpt
    g.to(device).train();d.to(device).train()
    og=torch.optim.AdamW(g.parameters(),lr=.0001,betas=(.8,.99),eps=1e-9)
    od=torch.optim.AdamW(d.parameters(),lr=.0001,betas=(.8,.99),eps=1e-9)
    beg=0
    if args.resume:
        ck=torch.load(work/'resume.pt',map_location=device,weights_only=False)
        g.load_state_dict(ck['generator']);d.load_state_dict(ck['discriminator'])
        og.load_state_dict(ck['opt_g']);od.load_state_dict(ck['opt_d'])
        beg=ck['step'];rng.setstate(ck['rng']);torch.cuda.set_rng_state(ck['cuda_rng'].cpu())
        del ck
    started=time.monotonic()
    for step in range(beg+1,args.steps+1):
        wav,c,pc,pf,n,spec=sample_batch(data,args.batch,device,rng)
        sid=torch.zeros(len(n),dtype=torch.long,device=device)
        with torch.autocast('cuda',dtype=torch.bfloat16):
            y,ind,_,mask,(z,zp,mp,lp,mq,lq)=g(c,n,pc,pf,spec,n,sid)
        y=y.float()
        target=commons.slice_segments(wav,ind*HOP,SEGMENT*HOP)
        mel=spec_to_mel_torch(spec,2048,125,SR,0,None)
        truth=commons.slice_segments(mel,ind,SEGMENT)
        pred=mel_spectrogram_torch(y.squeeze(1),2048,125,SR,HOP,2048,0,None)
        with torch.autocast('cuda',dtype=torch.bfloat16):
            real,fake,_,_=d(target,y.detach())
        dl=discriminator_loss(real,fake)[0]
        od.zero_grad(set_to_none=True);dl.backward()
        torch.nn.utils.clip_grad_norm_(d.parameters(),1000);od.step()
        d.requires_grad_(False)
        with torch.autocast('cuda',dtype=torch.bfloat16):
            _,fake,fr,fg=d(target,y)
        lm=F.l1_loss(truth,pred)*45.
        lk=kl_loss(zp.float(),lq.float(),mp.float(),lp.float(),mask.float())
        gl=lm+lk+feature_loss(fr,fg)+generator_loss(fake)[0]
        if not torch.isfinite(gl) or not torch.isfinite(dl): raise RuntimeError(('nonfinite',step))
        og.zero_grad(set_to_none=True);gl.backward()
        torch.nn.utils.clip_grad_norm_(g.parameters(),1000);og.step()
        d.requires_grad_(True)
        if step==1 or step%10==0:
            print(f"step={step} mel={lm.item():.3f} kl={lk.item():.3f} gen={gl.item():.3f} disc={dl.item():.3f} elapsed={time.monotonic()-started:.1f}",flush=True)
        if step%args.save_every==0 or step==args.steps:
            # The posterior encoder is only used for training. The original parameterization is retained.
            state={k:v.detach().to('cpu',torch.float16) for k,v in g.state_dict().items() if not k.startswith('enc_q.')}
            safe_save({'model':state,'step':step,'sr':SR,'version':'v2','train_clips':len(data)},work/f'atri-rvc-{step}.pt')
            safe_save({'step':step,'generator':g.state_dict(),'discriminator':d.state_dict(),'opt_g':og.state_dict(),
                       'opt_d':od.state_dict(),'rng':rng.getstate(),'cuda_rng':torch.cuda.get_rng_state()},work/'resume.pt')
            print('checkpoint',step,flush=True)

if __name__=='__main__': main()

