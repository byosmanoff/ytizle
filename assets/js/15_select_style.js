
/* Bütün select seçimlərini eyni, saytın bənövşəyi-çəhrayı fonuna uyğun pəncərədə göstər */
(function(){
  const nativeToUI = new WeakMap();
  let active = null;
  const layer = document.createElement('div');
  layer.id = 'siteSelectLayer';
  layer.innerHTML = '<div class="site-select-sheet" role="dialog" aria-modal="true" aria-label="Seçim"><div class="site-select-heading">SEÇİM ET</div><div class="site-select-options"></div></div>';
  document.body.appendChild(layer);
  const sheet = layer.querySelector('.site-select-sheet');
  const optionsBox = layer.querySelector('.site-select-options');
  function selectedText(sel){
    const opt = sel.options[sel.selectedIndex];
    return opt ? (window.wtT ? window.wtT(opt.textContent.trim()) : opt.textContent.trim()) : (window.wtT ? window.wtT('Seç') : 'Seç');
  }
  function sync(sel){
    const ui = nativeToUI.get(sel); if(!ui) return;
    ui.value.textContent = selectedText(sel);
    ui.button.disabled = sel.disabled;
  }
  function close(){
    if(!active) return;
    active.button.setAttribute('aria-expanded','false');
    layer.classList.remove('open');
    layer.setAttribute('aria-hidden','true');
    const previous = active.button; active = null;
    setTimeout(()=>{if(previous && previous.isConnected) previous.focus({preventScroll:true});},0);
  }
  function open(sel){
    const ui = nativeToUI.get(sel); if(!ui || sel.disabled) return;
    if(active) close();
    active = ui; ui.button.setAttribute('aria-expanded','true');
    optionsBox.textContent = '';
    const label = sel.getAttribute('aria-label') || (sel.labels && sel.labels[0] && sel.labels[0].textContent.trim()) || 'Seçim et';
    layer.querySelector('.site-select-heading').textContent = (window.wtT ? window.wtT(label) : label).toLocaleUpperCase(document.documentElement.lang || 'az');
    Array.from(sel.options).forEach((opt)=>{
      if(opt.disabled && !opt.value) return;
      const b = document.createElement('button'); b.type='button'; b.className='site-select-option'+(opt.value===sel.value?' selected':'');
      b.setAttribute('role','option'); b.setAttribute('aria-selected',opt.value===sel.value?'true':'false');
      const text = document.createElement('span'); text.textContent=window.wtT ? window.wtT(opt.textContent.trim()) : opt.textContent.trim();
      const check = document.createElement('span'); check.className='ss-check'; check.innerHTML='<i class="fa-solid fa-check" aria-hidden="true"></i>';
      b.append(text,check);
      b.addEventListener('click',()=>{
        if(sel.value!==opt.value){sel.value=opt.value; sel.dispatchEvent(new Event('change',{bubbles:true}));}
        sync(sel); close();
      });
      optionsBox.appendChild(b);
    });
    layer.classList.add('open'); layer.setAttribute('aria-hidden','false');
    const current = optionsBox.querySelector('.selected'); if(current) current.scrollIntoView({block:'nearest'});
  }
  function enhance(sel){
    if(nativeToUI.has(sel)) return;
    const wrap=document.createElement('div'); wrap.className='site-select-wrap';
    sel.parentNode.insertBefore(wrap,sel); wrap.appendChild(sel); sel.classList.add('site-select-native');
    sel.setAttribute('tabindex','-1');
    const button=document.createElement('button'); button.type='button'; button.className='site-select-trigger';
    button.setAttribute('aria-haspopup','listbox'); button.setAttribute('aria-expanded','false');
    const value=document.createElement('span'); value.className='sst-value';
    const chev=document.createElement('i'); chev.className='fa-solid fa-chevron-down sst-chevron'; chev.setAttribute('aria-hidden','true');
    button.append(value,chev); wrap.appendChild(button);
    const ui={select:sel,wrap,button,value}; nativeToUI.set(sel,ui);
    button.addEventListener('click',()=>open(sel));
    sel.addEventListener('change',()=>sync(sel));
    new MutationObserver(()=>sync(sel)).observe(sel,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['disabled']});
    sync(sel);
  }
  function scan(root){
    if(root.matches && root.matches('select')) enhance(root);
    if(root.querySelectorAll) root.querySelectorAll('select').forEach(enhance);
  }
  scan(document);
  new MutationObserver(records=>records.forEach(r=>r.addedNodes.forEach(n=>{if(n.nodeType===1 && n!==layer) scan(n);}))).observe(document.body,{childList:true,subtree:true});
  window.addEventListener('wt:languagechange',()=>{ document.querySelectorAll('select').forEach(sync); if(active) open(active.select); });
  layer.addEventListener('click',e=>{if(e.target===layer) close();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape' && active){e.preventDefault();close();}});
  document.addEventListener('click',e=>{if(active && !layer.contains(e.target) && !active.wrap.contains(e.target) && !e.target.closest('.site-select-wrap')) close();},true);
})();
