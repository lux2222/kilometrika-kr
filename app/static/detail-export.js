/* Local PNG export; capture-only changes are made to the cloned document. */
(() => {
async function snapshotMapForExport() {
  const el=document.querySelector('#map');
  if (!map || el.hidden) return null;
  const r=el.getBoundingClientRect(), size=map.getSize(), center=map.getCenter(), zoom=map.getZoom();
  if(Math.abs(size.x-r.width)>1 || Math.abs(size.y-r.height)>1) throw new Error('지도 크기가 안정되지 않았습니다.');
  const canvas=document.createElement('canvas');canvas.width=Math.round(r.width*2);canvas.height=Math.round(r.height*2);
  const ctx=canvas.getContext('2d');ctx.scale(2,2);ctx.fillStyle=getComputedStyle(el).backgroundColor;ctx.fillRect(0,0,r.width,r.height);
  const load=src=>new Promise((resolve,reject)=>{const i=new Image();i.crossOrigin='anonymous';i.onload=()=>resolve(i);i.onerror=()=>reject(new Error('지도 합성 이미지 로딩 실패'));i.src=src;});
  const tileCanvas=document.createElement('canvas');tileCanvas.width=canvas.width;tileCanvas.height=canvas.height;
  const tc=tileCanvas.getContext('2d');tc.scale(2,2);
  for(const tile of el.querySelectorAll('.leaflet-tile')) {
    const b=tile.getBoundingClientRect();if(!b.width || !b.height)continue;
    const image=await load(tile.currentSrc||tile.src);
    tc.drawImage(image,b.left-r.left,b.top-r.top,b.width,b.height);
  }
  ctx.save();ctx.filter=getComputedStyle(el.querySelector('.leaflet-tile-pane')).filter;ctx.drawImage(tileCanvas,0,0,r.width,r.height);ctx.restore();
  for(const svg of el.querySelectorAll('.leaflet-overlay-pane svg,.leaflet-marker-pane svg')) {
    const b=svg.getBoundingClientRect(), copy=svg.cloneNode(true);
    copy.setAttribute('xmlns','http://www.w3.org/2000/svg');copy.setAttribute('width',b.width);copy.setAttribute('height',b.height);copy.style.transform='none';copy.style.margin='0';
    const image=await load('data:image/svg+xml;charset=utf-8,'+encodeURIComponent(new XMLSerializer().serializeToString(copy)));
    ctx.drawImage(image,b.left-r.left,b.top-r.top,b.width,b.height);
  }
  if(map.getZoom()!==zoom || !map.getCenter().equals(center) || el.getBoundingClientRect().width!==r.width || el.getBoundingClientRect().height!==r.height)throw new Error('캡처 중 지도가 변경되었습니다.');
  return {src:canvas.toDataURL('image/png'),width:r.width,height:r.height};
}
  const timeout = (promise, ms = 15000) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('이미지 준비 시간이 초과되었습니다. 잠시 후 다시 시도하세요.')), ms);
    Promise.resolve(promise).then(v => { clearTimeout(timer); resolve(v); }, e => { clearTimeout(timer); reject(e); });
  });
  const filename = act => {
    let stamp = '날짜없음';
    if (act.start_time) {
      const date = new Date(asUTC(act.start_time));
      if (Number.isFinite(date.getTime())) {
        const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone:'Asia/Seoul', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hourCycle:'h23' }).formatToParts(date).map(p => [p.type,p.value]));
        stamp = `${p.year}-${p.month}-${p.day}_${p.hour}${p.minute}`;
      }
    }
    return `Kilometrika_${sportLabel(act).replace(/[\\/:*?"<>|]/g,'_')}_${stamp}.png`;
  };
  async function prepareImages(card) {
    const images = [...card.querySelectorAll('img')].filter(img => img.getClientRects().length);
    await Promise.all(images.map(async img => {
      if (!img.complete) await new Promise((resolve,reject) => {
        const done = () => { img.removeEventListener('load',ok); img.removeEventListener('error',bad); };
        const ok = () => { done(); resolve(); }, bad = () => { done(); reject(new Error('지도 또는 이미지 로딩에 실패했습니다.')); };
        img.addEventListener('load',ok,{once:true}); img.addEventListener('error',bad,{once:true});
      });
      if (!img.naturalWidth) throw new Error('지도 또는 이미지가 로딩되지 않았습니다.');
      if (img.decode) await img.decode();
      // Validate CORS before capture instead of silently dropping map tiles.
      const url = new URL(img.currentSrc || img.src,location.href);
      if (url.origin !== location.origin && /^https?:$/.test(url.protocol)) {
        await new Promise((resolve,reject) => {
          const probe = new Image(); probe.crossOrigin='anonymous';
          probe.onload=resolve; probe.onerror=()=>reject(new Error('지도 이미지의 CORS 접근이 허용되지 않아 저장할 수 없습니다.'));
          probe.src=url.href;
        });
      }
    }));
  }
  async function save() {
    if (detailExportBusy || !detailExportRecord || document.querySelector('#detail').hidden) return;
    if ([...document.querySelectorAll('#catSeg button')].some(b=>b.disabled)) return;
    detailExportBusy=true;
    const button=document.querySelector('#shareDisabled'), card=document.querySelector('#detail');
    const record={...detailExportRecord}, version=activityRequestVersion;
    const old={disabled:button.disabled,busy:button.getAttribute('aria-busy'),inert:card.inert};
    const block=e=>{e.preventDefault();e.stopImmediatePropagation();};
    const events=['click','dblclick','pointerdown','keydown','wheel','drop','change'];
    const previousClones=new Set(document.querySelectorAll('.html2canvas-container'));
    let url=null;
    try {
      button.disabled=true;button.setAttribute('aria-busy','true');card.inert=true;
      events.forEach(type=>document.addEventListener(type,block,{capture:true,passive:false}));
      await timeout(detailExportReady);
      if (document.fonts) await timeout(document.fonts.ready);
      await timeout(new Promise(resolve=>{
        const deadline=Date.now()+15000;
        const check=()=>{if(Date.now()>deadline)return; if(map && !document.querySelector('#map').hidden && (map._animatingZoom || map._panAnim?._inProgress || [...Object.values(map._layers)].some(layer=>layer.isLoading?.()))) requestAnimationFrame(check);else resolve();};check();
      }));
      await timeout(prepareImages(card));
      await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
      if(version!==activityRequestVersion || selectedId!==record.id) throw new Error('선택한 기록이 변경되었습니다. 다시 저장하세요.');
      if(typeof html2canvas!=='function')throw new Error('로컬 이미지 저장 라이브러리를 불러오지 못했습니다.');
      const mapSnapshot=await timeout(snapshotMapForExport());
      const background=getComputedStyle(card).backgroundColor;
      const canvas=await html2canvas(card,{
        scale:2,backgroundColor:background==='rgba(0, 0, 0, 0)'?'#ffffff':background,
        useCORS:true,allowTaint:false,logging:false,imageTimeout:15000,
        width:card.offsetWidth,height:Math.max(card.scrollHeight,card.offsetHeight),
        onclone:doc=>{
          const copy=doc.querySelector('#detail');copy.inert=false;
          if(mapSnapshot){
            const el=copy.querySelector('#map');
            el.style.width=mapSnapshot.width+'px';el.style.height=mapSnapshot.height+'px';
            el.querySelector('.leaflet-map-pane').remove();
            const image=doc.createElement('img');image.src=mapSnapshot.src;
            image.style.cssText='position:absolute;inset:0;width:100%;height:100%;';
            el.prepend(image);
          }
          copy.querySelectorAll('details:not([open])').forEach(el=>{
            const summary=el.querySelector('summary');if(summary)el.replaceChildren(summary);
          });
          copy.querySelector('#shareDisabled')?.style.setProperty('visibility','hidden');
          copy.querySelectorAll('.leaflet-control:not(.leaflet-control-attribution),.weather-help').forEach(el=>el.style.setProperty('display','none','important'));
        }
      });
      if(version!==activityRequestVersion || selectedId!==record.id) throw new Error('선택한 기록이 변경되었습니다. 다시 저장하세요.');
      const blob=await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error('PNG 생성에 실패했습니다.')),'image/png'));
      url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=filename(record);
      document.body.appendChild(link);
      events.forEach(type=>document.removeEventListener(type,block,true));
      link.click();link.remove();
    } catch(error) { alert('이미지 저장 실패: '+error.message+'\n이미지 저장 버튼을 눌러 다시 시도하세요.'); }
    finally {
      events.forEach(type=>document.removeEventListener(type,block,true));
      card.inert=old.inert;button.disabled=old.disabled;
      if(old.busy===null)button.removeAttribute('aria-busy');else button.setAttribute('aria-busy',old.busy);
      detailExportBusy=false;
      document.querySelectorAll('.html2canvas-container').forEach(el=>{if(!previousClones.has(el))el.remove();});
      if(url)setTimeout(()=>URL.revokeObjectURL(url),1000);
    }
  }
  window.KilometrikaExport={filename,save};
  document.querySelector('#shareDisabled').addEventListener('click',save);
})();
