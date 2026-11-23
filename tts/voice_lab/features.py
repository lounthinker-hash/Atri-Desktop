"""Offline RVC feature extraction shared by training and conversion."""
import json
import sys
from pathlib import Path
import numpy as np
import parselmouth
import soxr
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "rvc"))
from infer.hubert import HubertModelWithFinalProj
from infer.module.models import SynthesizerTrnMs768NSFsid
from transformers import AutoFeatureExtractor

CONFIG = json.loads((ROOT / "rvc/configs/v1/40k.json").read_text(encoding="utf-8"))
SR = 40000
HOP = 400
SEGMENT = 32
def generator():
    return SynthesizerTrnMs768NSFsid(1025, SEGMENT, **CONFIG["model"], sr=SR, is_half=False)

class Features:
    def __init__(self, device):
        self.device = torch.device(device)
        path = ROOT / "assets/hubert_base"
        self.hubert = HubertModelWithFinalProj.from_pretrained(str(path), local_files_only=True,
                               dtype=torch.float32).eval().to(self.device)
        self.normalize = AutoFeatureExtractor.from_pretrained(str(path), local_files_only=True).do_normalize

    @torch.inference_mode()
    def extract(self, wave, rate):
        wave16 = np.ascontiguousarray(soxr.resample(wave, rate, 16000, quality="HQ"),dtype=np.float32)
        x = torch.from_numpy(wave16).to(self.device)
        if self.normalize:
            x = F.layer_norm(x, x.shape)
        content = self.hubert(x.unsqueeze(0)).last_hidden_state[0].cpu().float().numpy()
        sound = parselmouth.Sound(wave16,16000)
        pitch = sound.to_pitch_ac(time_step=.01,pitch_floor=65,pitch_ceiling=1100,voicing_threshold=.55)
        pi = np.rint(pitch.xs()*100).astype(int)
        f0 = np.zeros(len(wave16)//160,dtype=np.float32)
        valid = (pi>=0)&(pi<len(f0))
        f0[pi[valid]]=pitch.selected_array["frequency"][valid]
        return content.astype(np.float32),f0

def coarse(f0):
    low,high=1127*np.log1p(np.array([50.,1100.])/700.)
    mel=1127*np.log1p(f0/700.)
    return np.rint(np.clip((mel-low)*254/(high-low)+1,1,255)).astype(np.int64)

