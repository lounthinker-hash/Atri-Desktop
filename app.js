'use strict';
const byId = id => document.getElementById(id);
const game = window.ATRI_GAME;
const media = window.ATRI_MEDIA;
const audio = byId('audio');
const recordings = new Map(media.voices.map(v => [v.id, v]));
const chapters = new Map(game.chapters);
const cleaned = s => (s || '').replaceAll('\\n', '\n');
const option = (select, value, label) => select.add(new Option(label, value));
const art = new Map([['official', {description: '官网全身像', path: 'assets/stage/official-atri.png', transparent:true}]]); 
const whiteSprites=media.sprites.filter(s=>s.dress==='d1');
for (const x of whiteSprites) art.set('s/' + x.id, {...x, path:'assets/sprites/' + x.file, transparent:true});
const reviewedWhiteEvents=new Set(media.gallery.filter(x=>['everyday','lively','laugh','quiet','sunset','awakening'].includes(x.id))
  .map(x=>x.source.split('/').pop().replace(/\.png$/,'')));
for (const x of media.gallery) art.set('g/' + x.id, {...x, path:'assets/portrait/' + x.file, transparent:false});
const sameFace = (a,b) => a.dress === b.dress && a.pose === b.pose && a.face === b.face;
const bgChoices = {garden:'assets/stage/garden.webp',classroom:'assets/stage/classroom.webp',sunset:'assets/stage/sunset.webp'};
let current = null, page = 0, filtered = [], marked = null;
let visibleArt = null, lastArtPath = '', blinkUntil = 0, nextBlink = 0;
const pageSize = 35;
function tab(name) {
  for (const k of ['stage','archive','profile']) byId(k).hidden = k !== name;
  for (const b of document.querySelectorAll('[data-tab]')) b.classList.toggle('active', b.dataset.tab===name);
}
function sceneArt(id, overrideBackground) {
  const item = art.get(id) || art.get('official');
  visibleArt = item;
  lastArtPath = '';
  const backgrounds=byId('bg'), selected=backgrounds.value;
  backgrounds.querySelector('[data-frame]')?.remove();
  if(overrideBackground){
    const label=overrideBackground.split('/').pop().split('.')[0];
    const sourceOption=new Option('原作背景 · '+label,'frame');
    sourceOption.dataset.frame='1';backgrounds.add(sourceOption);backgrounds.value='frame';
  }else{backgrounds.value=Object.hasOwn(bgChoices,selected)?selected:'garden';}
  byId('backdrop').src = item.transparent ? (overrideBackground || bgChoices[byId('bg').value]) : item.path;
  byId('sprite').hidden = !item.transparent;
  if (item.transparent) byId('sprite').src = item.path;
  byId('bgLabel').hidden = !item.transparent;
  byId('look').value = art.has(id) ? id : 'official';
}
function setFrame(item) {
  const frame = media.cues[item.id];
  if (!byId('match').checked || !frame) return '';
  const [dress,pose,face,bg,event] = frame;
  if (event && reviewedWhiteEvents.has(event.replace(/l+$/,'')) && media.scenery[event]) {
    visibleArt = null;
    byId('backdrop').src=media.scenery[event]; byId('sprite').hidden=true;
    byId('bgLabel').hidden=true; byId('look').value='auto';
    return '原作事件画面 ' + event;
  }
  const exact = whiteSprites.find(s=>sameFace(s,{dress:'d1',pose,face}));
  const base = bg && media.scenery[bg];
  if (exact) {
    sceneArt('s/'+exact.id,base);
    return (dress==='d1'?'原作立绘参数 ':'白色日常服 · 同姿势表情 ') + ['d1',pose,face].join(' / ');
  }
  sceneArt('official',base);
  return '官网参考立绘' + (base?' · 游戏背景 '+bg:'');
}
function selectClip(id, play=false) {
  const item=recordings.get(id);
  if (!item) return;
  audio.pause(); current=item;
  audio.src=item.file;
  const sel=byId('clips');
  if (![...sel.options].some(o=>o.value===id)) {
    const old=sel.querySelector('[data-temporary]'); if(old) old.remove();
    const extra=new Option(item.zh || id,id); extra.dataset.temporary='1';sel.add(extra);
  }
  sel.value=id;
  byId('line').textContent=cleaned(item.zh)|| (id.includes('_sys_')?'系统语音':'原声片段');
  const cue=setFrame(item);
  byId('source').textContent=[item.title,item.label && item.row ? item.label+' #'+item.row:'',id,cue].filter(Boolean).join(' · ');
  byId('playStatus').textContent='';
  if (play) audio.play().catch(()=>{byId('playStatus').textContent='播放未开始，可按「播放原声」重试。';});
}
function nextClip() {
  const clipList=media.picks;
  const i=clipList.indexOf(current?.id);
  if(i<0 && current?.scene) {
    const same=media.voices.filter(v=>v.scene===current.scene && v.row);
    const j=same.findIndex(v=>v.id===current.id);
    if(j>=0 && j+1<same.length) return selectClip(same[j+1].id,true);
  }
  selectClip(clipList[(i+1)%clipList.length],true);
}
function makeButton(label,fn) {const b=document.createElement('button');b.textContent=label;b.onclick=fn;return b;}
function localize(r,lang) {
  const i={zh:0,ja:1,tw:2,en:3}[lang];
  return {speaker:r.names[i]||r.speaker||'旁白',text:cleaned(r.lines[i])};
}
function filterRows() {
  const mode=byId('scope').value, chapter=byId('chapter').value, q=byId('query').value.trim().toLowerCase();
  const set=mode==='extra'?media.extra:game.rows;
  return set.filter(r=>{
    if(chapter && r.chapter!==chapter) return false;
    if(mode==='own' && !r.own) return false;
    if(mode==='mention' && (r.own || !/亚托莉|亚托铃|萝卜子/.test(r.lines[0]||''))) return false;
    if(!q) return true;
    return [...r.lines,...r.names,r.speaker,r.voice,r.chapter,r.label,chapters.get(r.chapter)].filter(Boolean).join(' ').toLowerCase().includes(q);
  });
}
function context(row) {
  tab('archive'); byId('scope').value='all';byId('chapter').value=row.chapter;byId('query').value='';
  marked=row;filtered=filterRows();page=Math.floor(Math.max(0,filtered.indexOf(row))/pageSize);draw();
  document.querySelector('.result.selected')?.scrollIntoView({block:'center'});
}
function draw() {
  const pages=Math.max(1,Math.ceil(filtered.length/pageSize));page=Math.max(0,Math.min(page,pages-1));
  byId('count').textContent=filtered.length.toLocaleString()+' 条 · '+(page+1)+' / '+pages+' 页';
  byId('prevPage').disabled=page===0;byId('nextPage').disabled=page+1>=pages;
  const box=byId('results');box.replaceChildren();
  for (const r of filtered.slice(page*pageSize,(page+1)*pageSize)) {
    const el=document.createElement('div');el.className='result'+(marked===r?' selected':'');
    const text=document.createElement('div'), info=document.createElement('small'), p=document.createElement('p');
    const {speaker,text:sentence}=localize(r,byId('lang').value);
    info.textContent=[chapters.get(r.chapter)||r.chapter,r.label,r.row?'#'+r.row:'',speaker,r.voice].filter(Boolean).join(' · ');
    p.textContent=sentence||'（无对应文本）';text.append(info,p);const actions=document.createElement('div');actions.className='buttons';
    if(recordings.has(r.voice)) actions.append(makeButton('听原声',()=>{tab('stage');selectClip(r.voice,true);window.scrollTo(0,0);}));
    if(r.own && !recordings.has(r.voice) && r.voice){const tip=document.createElement('small');tip.textContent='缺同名录音';actions.append(tip);}
    if(r.row && byId('scope').value!=='all')actions.append(makeButton('前后文',()=>context(r)));
    el.append(text,actions);box.append(el);
  }
}
function refresh() {marked=null;page=0;filtered=filterRows();draw();}
function animatePortrait() {
  if (document.hidden || !visibleArt?.transparent || !visibleArt.base) return;
  const now=performance.now();
  if(now>nextBlink){blinkUntil=now+145;nextBlink=now+2900+Math.random()*3200;}
  let variant=null;
  const envelope=media.envelopes?.[current?.id];
  if(!audio.paused && envelope && visibleArt.openMouthId) {
    const loud=envelope[Math.floor(audio.currentTime*20)]==='1';
    variant=loud?visibleArt.openMouthId:visibleArt.closedMouthId;
  }
  if(now<blinkUntil && visibleArt.blinkId) variant=visibleArt.blinkId;
  const path=art.get('s/'+variant)?.path || visibleArt.path;
  if(path!==lastArtPath){byId('sprite').src=path;lastArtPath=path;}
}
function init() {
  for(const b of document.querySelectorAll('[data-tab]')) b.onclick=()=>tab(b.dataset.tab);
  option(byId('look'),'auto','原作演出');
  const groups=new Map(), outfitNames={d1:'日常服',d2:'泳衣',d3:'换装场景',d4:'睡衣'};
  for(const [k,v] of art) {
    const group=v.dress ? outfitNames[v.dress]||v.dress : v.transparent?'角色介绍':'事件原画';
    if(!groups.has(group)){const node=document.createElement('optgroup');node.label=group;byId('look').add(node);groups.set(group,node);}
    groups.get(group).append(new Option(v.face ? [v.pose,v.face].join(' · '):v.description||v.id, k));
  }
  for(const id of media.picks){const v=recordings.get(id);if(v)option(byId('clips'),id,v.mood+' · '+cleaned(v.zh||id).replace(/\s+/g,' ').slice(0,34));}
  option(byId('chapter'),'','全部章节');
  for(const [k,v] of chapters)option(byId('chapter'),k,k+' · '+v);
  option(byId('chapter'),'system','系统原声');
  byId('look').onchange=()=>{const v=byId('look').value;byId('match').checked=v==='auto';if(v==='auto')selectClip(current.id);else{sceneArt(v);byId('source').textContent=[current?.id,art.get(v)?.description].filter(Boolean).join(' · ');}};
  byId('bg').onchange=()=>{byId('match').checked=false;sceneArt(byId('look').value);byId('source').textContent=[current?.id,'自选背景'].filter(Boolean).join(' · ');};
  byId('match').onchange=()=>{if(current)selectClip(current.id);};
  byId('clips').onchange=()=>selectClip(byId('clips').value,true);
  byId('play').onclick=()=>{
    if(!audio.paused){audio.pause();return;}
    if(audio.ended)audio.currentTime=0;
    audio.play().catch(()=>{byId('playStatus').textContent='这段原声暂时无法播放。';});
  };
  byId('next').onclick=nextClip;byId('volume').oninput=()=>audio.volume=Number(byId('volume').value);audio.volume=.75;
  audio.onplay=()=>{byId('play').textContent='暂停';};audio.onpause=()=>{byId('play').textContent='播放原声';};
  audio.onended=()=>{byId('play').textContent='播放原声';if(byId('autoplay').checked) nextClip();};
  audio.onerror=()=>{byId('playStatus').textContent='这段录音加载失败。';};
  for(const id of ['scope','chapter','lang'])byId(id).onchange=refresh;byId('query').oninput=refresh;
  byId('prevPage').onclick=()=>{page--;draw();byId('count').scrollIntoView({block:'center'});};
  byId('nextPage').onclick=()=>{page++;draw();byId('count').scrollIntoView({block:'center'});};
  byId('bio').innerHTML=game.bio;
  byId('sources').innerHTML=media.sourceNote;
  sceneArt('official');selectClip(media.picks[0]);refresh();setInterval(animatePortrait,60);
  if(location.hash==='#archive'||location.hash==='#profile')tab(location.hash.slice(1));
}
init();
