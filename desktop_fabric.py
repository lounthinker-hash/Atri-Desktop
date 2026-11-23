"""Pinned secondary motion for the original d1 hair, necktie and sailor dress."""
import math

import numpy as np


# Each hair lock and the necktie follow their painted centreline. Coordinates
# are fractions of that uncropped pose, from the fastening to the free end.
# The last value is the transverse radius, as a fraction of the pose width.
_LOCKS = {
    "p1": (
        ((.36,.22),(.29,.33),(.20,.441),(.150,.502),.054),
        ((.39,.23),(.33,.35),(.262,.405),(.226,.445),.048),
        ((.66,.21),(.755,.341),(.914,.445),(.958,.495),.044),
        ((.64,.23),(.726,.35),(.852,.445),(.917,.494),.035),
        ((.503,.335),(.500,.35),(.492,.380),(.498,.405),.048)),
    "p2": (
        ((.36,.22),(.31,.34),(.215,.45),(.168,.515),.050),
        ((.39,.23),(.34,.35),(.274,.46),(.246,.545),.046),
        ((.65,.22),(.735,.345),(.837,.455),(.854,.535),.056),
        ((.625,.22),(.684,.342),(.745,.44),(.756,.552),.052),
        ((.468,.328),(.457,.35),(.445,.38),(.448,.405),.037)),
    "p3": (
        ((.493,.138),(.462,.258),(.365,.374),(.224,.472),.040),
        ((.51,.145),(.472,.264),(.402,.353),(.300,.443),.032),
        ((.622,.105),(.718,.253),(.821,.399),(.793,.501),.037),
        ((.603,.116),(.684,.247),(.764,.38),(.746,.492),.036),
        ((.573,.257),(.558,.276),(.530,.300),(.536,.321),.028)),
    "p4": (
        ((.379,.14),(.316,.258),(.20,.384),(.115,.466),.067),
        ((.412,.146),(.36,.283),(.302,.427),(.266,.51),.070),
        ((.665,.133),(.728,.26),(.86,.388),(.914,.477),.067),
        ((.615,.143),(.65,.279),(.728,.42),(.714,.524),.072),
        None),
}
# Roots and free corners visible in the crop. The back of the collar is
# largely occluded by hair in p4. Its small exposed side edges can still bend.
_COLLAR = {"p1": (.512,.299,.624,.329), "p2": (.49,.299,.598,.326),
           "p3": (.587,.222,.756,.324), "p4": (.503,.462,.707,.535)}
_HEM = {"p1": (.53,.445,.65,.682,.38), "p2": (.502,.442,.644,.680,.353),
        "p3": (.54,.352,.522,.665,.349), "p4": (.50,.427,.632,.676,.395)}
_HANDS = {"p1": ((.054,.561,.116,.036),(.948,.561,.111,.040)),
          "p2": ((.070,.063,.096,.061),(.924,.063,.094,.061)),
          "p3": ((.354,.292,.064,.053),(.966,.224,.064,.066)),
          "p4": ((.49,.56,.142,.042),)}


def _smooth(value):
    f = np.clip(value, 0., 1.)
    return f*f*(3.-2.*f)


def material_fields(u, v, pose):
    """Midpoint and tip influence per free piece. Roots have zero weight."""
    mid, tips = [], []
    hands_free = np.ones_like(u)
    for x,y,rx,ry in _HANDS[pose]:
        radius = ((u-x)/rx)**2 + ((v-y)/ry)**2
        hands_free *= _smooth((radius-.78)/.85)
    for line in _LOCKS[pose]:
        if line is None:
            mid.append(np.zeros_like(u))
            tips.append(np.zeros_like(u))
            continue
        points, width = line[:-1], line[-1]
        xs, ys = zip(*points)
        axis = np.interp(v, ys, xs)
        length = np.clip((v-ys[0])/(ys[-1]-ys[0]), 0., 1.)
        mask = np.exp(-((u-axis)/width)**2) * _smooth((ys[-1]+.026-v)/.029) * hands_free
        # Two independently integrated sections bend along a common tangent.
        mid.append(mask * 2.*length*(1.-length))
        tips.append(mask * length**2)
    # Adjacent hair strands share pixels where they pass the edge of a sleeve.
    # Do not add two full tip displacements there: that reverses a mesh cell
    # and turns one continuous dark contour into little zigzags.
    overlap = np.maximum(1.,np.sum(tips[:4],axis=0))
    for i in range(4):
        mid[i] = mid[i]/overlap
        tips[i] = tips[i]/overlap
    x,y,ex,ey = _COLLAR[pose]
    length = np.clip((u-x)/(ex-x),0.,1.)
    edge = y+(ey-y)*length
    mask = np.exp(-((v-edge)/.023)**2) * _smooth((ex+.058-u)/.060) * hands_free
    mid.append(mask * 2.*length*(1.-length))
    tips.append(mask * length**2)
    center,waist,left_hem,right_hem,spread = _HEM[pose]
    across = (u-center)/spread
    floor = (left_hem+right_hem)/2 + (right_hem-left_hem)*np.clip(across,-1.,1.)/2
    depth = _smooth((v-waist)/(floor-waist)) * _smooth((floor+.022-v)/.026)
    for lobe in (-.76,0.,.76):
        weight = np.exp(-((across-lobe)/.56)**2) * depth * hands_free
        mid.append(weight*(1.-depth))
        tips.append(weight*depth)
    return np.asarray(mid,dtype=np.float32), np.asarray(tips,dtype=np.float32)


class FabricMotion:
    """Two damped masses per strand or fabric panel, driven by wind and root motion."""
    frequency = np.array([6.4,7.4,5.9,7.,10.,8.1,5.7,6.7,6.0])
    phase = np.array([0.,.74,.36,1.02,.48,.25,.15,.58,1.04])
    amplitude = np.array([.014,.0085,.0162,.0104,.010,.008,.010,.0045,.012])

    def __init__(self, now, seed):
        self.last_time = now
        self.phase_origin = seed
        self.root = None
        self.mid = np.zeros(9)
        self.tip = np.zeros(9)
        self.mid_velocity = np.zeros(9)
        self.tip_velocity = np.zeros(9)

    def sample(self, now, body, wind=True):
        elapsed = max(0., now-self.last_time)
        if elapsed:
            duration = min(.15,elapsed)
            sway,tilt,_,_,_,gait,lift,_,lag = body
            root = .65*sway+.57*tilt+.40*gait+.16*lift
            speed = 0. if self.root is None else np.clip((root-self.root)/elapsed,-5.,5.)
            self.root = root
            steps = max(1,math.ceil(duration*90))
            dt = duration/steps
            for step in range(steps):
                t = now-self.phase_origin-duration+(step+1)*dt
                air = (.73*np.sin(t*1.78+self.phase)+.28*np.sin(t*.95+self.phase*1.6)
                       +.16*np.sin(t*2.82-self.phase*.7)) if wind else 0.
                drive = air-.42*speed+.12*lag+.09*gait
                k = self.frequency
                self.mid_velocity += (k*k*(drive-self.mid)-1.24*k*self.mid_velocity)*dt
                self.mid += self.mid_velocity*dt
                k = self.frequency*.72
                self.tip_velocity += (k*k*(self.mid-self.tip)-.86*k*self.tip_velocity)*dt
                self.tip += self.tip_velocity*dt
            self.last_time = now
        # A sharply reversed head shake can otherwise pull a lock through its
        # neighbouring contour. Preserve the ordinary breeze and ease just the
        # largest excursions into the available strip of painted hair.
        tip = self.tip.copy()
        distance = np.abs(tip[:5])
        excess = np.maximum(0.,distance-.82)
        tip[:5] = np.sign(tip[:5])*(np.minimum(distance,.82)+.46*np.tanh(excess/.46))
        return self.mid*self.amplitude*.68, tip*self.amplitude


_VERTICAL = np.array([.22,-.18,-.23,.15,.09,.50,-.42,.23,.50])


def material_offset(fields, state):
    middle, tips = fields
    a, b = state
    dx = np.einsum("i,ijk->jk",a,middle)+np.einsum("i,ijk->jk",b,tips)
    dy = np.einsum("i,ijk->jk",a*_VERTICAL,middle)+np.einsum("i,ijk->jk",b*_VERTICAL,tips)
    return dx,dy
