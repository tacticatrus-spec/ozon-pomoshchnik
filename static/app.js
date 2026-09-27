let data={};const $=x=>document.getElementById(x);function money(x){return Number(x||0).toLocaleString('ru-RU',{maximumFractionDigits:2})+' ₽'}
function toast(s,bad=false){let x=$('toast');x.textContent=s;x.style.background=bad?'#b82740':'#172033';x.style.display='block';setTimeout(()=>x.style.display='none',4500)}
async function api(url,opt={}){opt.headers={'Content-Type':'application/json'};let r=await fetch(url,opt),j=await r.json();if(!r.ok)throw Error(j.message||'Ошибка');return j}
async function load(){data=await api('/api/dashboard');let r=data.risk_summary||{};$('nProducts').textContent=data.products.length;$('nStock').textContent=data.products.reduce((a,x)=>a+x.stock,0);$('nOrders').textContent=data.order_count;$('nRisks').textContent=(r.loss||0)+(r.below_margin||0);$('riskCaption').textContent=(r.loss||0)+' в убытке, '+(r.below_margin||0)+' ниже маржи';$('appVersion').textContent='Версия '+data.version;render()}
function render(){
 $('events').innerHTML=data.events.map(x=>`<div class="event"><small>${x.created_at}</small><div>${esc(x.message)}</div></div>`).join('')||'<p class="hint">Пока событий нет</p>';
 const labels={loss:'Убыток',below_margin:'Ниже маржи',missing_cost:'Нет себестоимости',safe:'Безопасно'};
 $('productsBody').innerHTML=data.products.map(p=>`<tr class="risk-row ${p.profit_status}"><td class="name"><div class="product-card">${productPhoto(p)}<div><b>${esc(p.name)}</b><br><small>${esc(p.offer_id)}</small></div></div></td><td>${money(p.buyer_price)}</td><td>${money(p.seller_price)}</td><td>${p.stock}</td><td><input id="c${p.product_id}" value="${p.cost}"></td><td><input id="f${p.product_id}" value="${p.commission_pct}"></td><td><input id="l${p.product_id}" value="${p.logistics}"></td><td class="${p.profit>=0?'good':'bad'}">${money(p.profit)}</td><td class="${p.margin_pct>=data.target_margin_pct?'good':p.profit_status==='missing_cost'?'':'bad'}">${Number(p.margin_pct||0).toLocaleString('ru-RU',{maximumFractionDigits:1})}%</td><td>${p.cost>0?money(p.safe_price):'—'}<br><small class="hint">ноль: ${p.cost>0?money(p.break_even_price):'—'}</small></td><td><span class="status ${p.profit_status}">${labels[p.profit_status]||p.profit_status}</span></td><td><div class="actions"><button onclick="saveCost(${p.product_id})">Сохранить</button><button onclick="competitor(${p.product_id})">Конкурент</button><button class="primary" onclick="propose(${p.product_id})">Предложить</button></div></td></tr>`).join('');
 $('proposals').innerHTML=data.proposals.map(p=>`<div class="proposal"><b>${money(p.current_price)} → ${money(p.proposed_price)}</b><div class="hint">${esc(p.reason)}</div><div class="actions"><button class="primary" onclick="proposal(${p.id},'approve')">Подтвердить цену</button><button onclick="proposal(${p.id},'reject')">Отклонить</button></div></div>`).join('')||'<p class="hint">Нет предложений на рассмотрении</p>';
 $('ordersBody').innerHTML=data.orders.map(o=>`<tr><td>${esc(o.posting_number)}</td><td><span class="pill">${o.scheme}</span></td><td>${esc(o.status)}</td><td>${esc(o.created_at)}</td><td>${money(o.amount)}</td></tr>`).join('');
 $('messagesList').innerHTML=data.messages.map(m=>`<div class="message"><small>${m.kind==='review'?'Отзыв':'Вопрос'} • ${esc(m.created_at)}</small><p>${esc(m.text)}</p>${m.status!=='PROCESSED'?`<textarea class="answer" id="a${m.id}" placeholder="Введите ответ"></textarea><button class="primary" onclick="answer('${m.id}')">Отправить ответ</button>`:'<span class="good">Обработано</span>'}</div>`).join('')||'<p class="hint">Новых отзывов и вопросов нет</p>';
 $('costRules').innerHTML=(data.cost_rules||[]).map(r=>`<div class="event"><b>${esc(r.prefix)}</b> — ${money(r.cost)}</div>`).join('')||'<p class="hint">Правила пока не заданы.</p>';
 let r=data.risk_summary||{};$('targetMargin').value=data.target_margin_pct;$('riskLoss').textContent=r.loss||0;$('riskLow').textContent=r.below_margin||0;$('riskMissing').textContent=r.missing_cost||0;$('riskSafe').textContent=r.safe||0;
}
function esc(x){return String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button,.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');$(b.dataset.tab).classList.add('active');$('title').textContent=b.textContent});
async function saveSettings(){try{await api('/api/settings',{method:'POST',body:JSON.stringify({client_id:$('clientId').value,api_key:$('apiKey').value,telegram_token:$('tgToken').value,telegram_chat_id:$('chatId').value})});toast('Настройки сохранены');await load()}catch(e){toast(e.message,true)}}
async function testOzon(){try{toast((await api('/api/test',{method:'POST'})).message)}catch(e){toast(e.message,true)}}
async function findChat(){try{let x=await api('/api/telegram/chat');$('chatId').value=x.chat_id;toast('Telegram подключён')}catch(e){toast(e.message,true)}}
async function syncAll(){try{toast('Синхронизация началась…');let x=await api('/api/sync',{method:'POST'});toast(x.message);await load()}catch(e){toast(e.message,true)}}
async function demo(){await api('/api/demo',{method:'POST'});toast('Демо-данные загружены');load()}
async function exportCards(){
 const prefix=$('exportPrefix').value.trim();
 if(!prefix){toast('Укажите начало артикула',true);return}
 try{
  toast('Собираю полные данные карточек…');
  let r=await fetch('/api/products/export?prefix='+encodeURIComponent(prefix));
  if(!r.ok){let j=await r.json();throw Error(j.message||'Ошибка выгрузки')}
  let blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');
  let disposition=r.headers.get('Content-Disposition')||'';
  let match=disposition.match(/filename="([^"]+)"/);
  a.href=url;a.download=match?match[1]:'ozon_cards.json';document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
  toast('Файл карточек скачан');
 }catch(e){toast(e.message,true)}
}
let ttOptimizationReady=false;
async function previewTTOptimization(){
 try{
  $('ttOptimizationPreview').innerHTML='<p class="hint">Проверяю карточки TT в Ozon…</p>';
  $('applyTTOptimization').style.display='none';
  let x=await api('/api/optimization/tt/preview');ttOptimizationReady=x.found>0;
  $('ttOptimizationPreview').innerHTML=`<p><b>Найдено карточек: ${x.found} из ${x.count}</b></p><div class="tablewrap"><table><thead><tr><th>Артикул</th><th>Сейчас</th><th>Будет</th></tr></thead><tbody>${x.items.map(i=>`<tr><td><b>${esc(i.offer_id)}</b>${i.found?'':'<br><span class="bad">Не найден</span>'}</td><td>${esc(i.old_name)}</td><td><b>${esc(i.new_name)}</b></td></tr>`).join('')}</tbody></table></div><p class="hint">Будут заменены только название, описание и поисковые фразы. Цена, остаток, фотографии и отзывы сохранятся.</p>`;
  $('applyTTOptimization').style.display='none';
 }catch(e){$('ttOptimizationPreview').innerHTML='';toast(e.message,true)}
}
async function applyTTOptimization(){
 if(!ttOptimizationReady)return;
 if(!confirm('Отправить в Ozon новые названия, описания и поисковые фразы для найденных карточек TT?'))return;
 try{
  $('applyTTOptimization').disabled=true;
  let x=await api('/api/optimization/tt/apply',{method:'POST',body:JSON.stringify({confirmed:true})});
  toast(x.message);$('ttOptimizationPreview').innerHTML=`<p class="good">${esc(x.message)}</p><p class="hint">Ozon обработает изменения в очереди. Это может занять несколько минут.</p>`;$('applyTTOptimization').style.display='none';ttOptimizationReady=false;
 }catch(e){toast(e.message,true)}finally{$('applyTTOptimization').disabled=false}
}
let seoItems=[];
let seoStats={};
async function loadSeoAudit(){
 const group=$('seoGroup').value;
 try{
  $('seoSummary').innerHTML='<p class="hint">Анализирую карточки TT и ZV…</p>';$('seoAudit').innerHTML='';
  let x=await api('/api/seo/audit?group='+encodeURIComponent(group));seoItems=x.items||[];
  seoStats=x.summary||{};
  if(x.profile){$('seoRegion').value=x.profile.region||'Москва';$('seoDepth').value=x.profile.search_depth||300;$('seoBrowserMode').value=x.profile.browser_mode||'Чистый браузер'}
  renderSeoSummary();renderSeoAudit();await loadSellerRankHistory();
 }catch(e){$('seoSummary').innerHTML='';toast(e.message,true)}
}
function renderSeoSummary(){
 let counts=seoItems.reduce((a,i)=>(a[i.group]=(a[i.group]||0)+1,a),{}),s=seoStats;
 $('seoSummary').innerHTML=`<div class="seo-metrics"><span>Карточек <b>${seoItems.length}</b></span><span>ТОП-10 <b>${s.top10||0}</b></span><span>ТОП-20 <b>${s.top20||0}</b></span><span>ТОП-50 <b>${s.top50||0}</b></span><span class="good">Выросли <b>${s.improved||0}</b></span><span class="bad">Упали <b>${s.declined||0}</b></span><span>Не найдены <b>${s.not_found||0}</b></span></div><p class="hint">${Object.entries(counts).map(([k,v])=>esc(k)+': '+v).join(' · ')}. «Не найден» означает: карточки нет в пределах указанной глубины проверки.</p>`;
}
function seoWorst(i){let values=(i.positions||[]).map(p=>Number(p.position||0));return values.length?Math.max(...values.map(v=>v||100000)):100000}
function seoChange(i){return Math.max(0,...(i.positions||[]).map(p=>Math.abs(Number(p.delta||0))))}
function renderSeoAudit(){
 let needle=($('seoFilter').value||'').trim().toLowerCase(),sort=$('seoSort').value;
 let items=seoItems.map((item,index)=>({item,index})).filter(x=>!needle||(`${x.item.offer_id} ${x.item.name}`).toLowerCase().includes(needle));
 if(sort==='worst')items.sort((a,b)=>seoWorst(b.item)-seoWorst(a.item));else if(sort==='change')items.sort((a,b)=>seoChange(b.item)-seoChange(a.item));else items.sort((a,b)=>a.item.offer_id.localeCompare(b.item.offer_id,'ru'));
 $('seoAudit').innerHTML=items.map(({item:i,index:idx})=>{
  let productLink=i.sku?`<button onclick="openProductCard(${idx})">Открыть карточку</button>`:'';
  let queries=(i.queries||[]).map((q,qi)=>{let p=i.positions[qi]||{},delta=p.delta,trend=delta==null?'':delta>0?`<span class="good">▲ +${delta}</span>`:delta<0?`<span class="bad">▼ ${delta}</span>`:'<span class="hint">без изменений</span>',where=p.region?` · ${esc(p.region)} · до ${p.search_depth}`:'';return `<div class="seo-query"><div><b>${esc(q)}</b><small>${p.checked_at?'Место: '+(p.position||'не найдено')+(p.previous_position!=null?' · было: '+(p.previous_position||'не найдено'):'')+' '+trend+' · '+esc(p.checked_at)+where:'Исходное место ещё не сохранено'}</small></div><button onclick="openSeoSearch(${idx},${qi})">Открыть поиск</button><input id="seoPos${idx}_${qi}" type="number" min="0" max="10000" placeholder="Место"><button class="primary" onclick="saveSeoPosition(${idx},${qi})">Сохранить</button></div>`}).join('');
  return `<div class="seo-card"><div class="seo-head">${productPhoto(i)}<div><span class="pill">${esc(i.group)}</span><h3>${esc(i.offer_id)}</h3><p>${esc(i.name)}</p><small class="hint">Остаток: ${Number(i.stock||0)}</small></div></div><div class="seo-suggestion"><small>Рекомендуемое название — только для ручной проверки</small><b>${esc(i.suggested_name)}</b><div class="actions"><button onclick="copySeoTitle(${idx})">Копировать</button>${productLink}</div></div><details class="seo-query-editor"><summary>Редактировать поисковые запросы</summary><textarea id="seoQueries${idx}" rows="4">${esc((i.queries||[]).join('\n'))}</textarea><button onclick="saveSeoQueries(${idx})">Сохранить запросы локально</button></details><div class="seo-queries">${queries}</div></div>`;
 }).join('')||'<div class="panel"><p class="hint">Карточек не найдено. Измените фильтр или выполните синхронизацию.</p></div>';
}
function openSeoSearch(itemIndex,queryIndex){
 const query=seoItems[itemIndex].queries[queryIndex];
 window.open('https://www.ozon.ru/search/?from_global=true&text='+encodeURIComponent(query),'_blank','noopener');
}
function openProductCard(itemIndex){let sku=seoItems[itemIndex].sku;if(sku)window.open('https://www.ozon.ru/product/'+encodeURIComponent(sku)+'/','_blank','noopener')}
async function copySeoTitle(itemIndex){try{await navigator.clipboard.writeText(seoItems[itemIndex].suggested_name);toast('Название скопировано')}catch(e){toast('Не удалось скопировать',true)}}
async function saveSeoPosition(itemIndex,queryIndex){
 const item=seoItems[itemIndex],query=item.queries[queryIndex],input=$(`seoPos${itemIndex}_${queryIndex}`);
 if(input.value===''){toast('Введите место; 0 — товар не найден',true);return}
 try{let x=await api('/api/seo/position',{method:'POST',body:JSON.stringify({offer_id:item.offer_id,query,position:input.value,region:$('seoRegion').value,search_depth:$('seoDepth').value,browser_mode:$('seoBrowserMode').value})});toast(x.message);await loadSeoAudit()}catch(e){toast(e.message,true)}
}
async function saveSeoQueries(itemIndex){
 const item=seoItems[itemIndex],queries=$(`seoQueries${itemIndex}`).value.split(/\r?\n/).map(x=>x.trim()).filter(Boolean);
 try{let x=await api('/api/seo/queries',{method:'POST',body:JSON.stringify({offer_id:item.offer_id,queries})});toast(x.message);await loadSeoAudit()}catch(e){toast(e.message,true)}
}
async function saveSeoProfile(){
 try{let x=await api('/api/seo/profile',{method:'POST',body:JSON.stringify({region:$('seoRegion').value,search_depth:$('seoDepth').value,browser_mode:$('seoBrowserMode').value})});toast(x.message)}catch(e){toast(e.message,true)}
}
async function saveSellerRank(){
 try{let x=await api('/api/seo/seller-rank',{method:'POST',body:JSON.stringify({category:$('sellerRankCategory').value,rank:$('sellerRankValue').value,top_percent:$('sellerTopPercent').value})});toast(x.message);$('sellerRankValue').value='';$('sellerTopPercent').value='';await loadSellerRankHistory()}catch(e){toast(e.message,true)}
}
async function loadSellerRankHistory(){
 try{let x=await api('/api/seo/seller-rank?category='+encodeURIComponent($('sellerRankCategory').value||'')),items=x.items||[];$('sellerRankHistory').innerHTML=items.length?`<div class="seller-rank-list">${items.slice(0,10).map((i,index)=>{let next=items[index+1],delta=next?next.rank-i.rank:null;return `<span><b>№${i.rank}</b>${i.top_percent!=null?' · топ '+i.top_percent+'%':''}${delta==null?'':delta>0?' · <i class="good">▲ +'+delta+'</i>':delta<0?' · <i class="bad">▼ '+delta+'</i>':''}<small>${esc(i.checked_at)}</small></span>`}).join('')}</div>`:'<p class="hint">Рейтинг продавца ещё не сохранён.</p>'}catch(e){$('sellerRankHistory').innerHTML='<p class="hint">История рейтинга недоступна.</p>'}
}
let attributeMatches=[];
async function findAttributes(){
 const search=$('attrSearch').value.trim(),replacement=$('attrReplacement').value.trim();
 if(!search||!replacement){toast('Заполните оба поля',true);return}
 try{
  $('attributeMatches').innerHTML='<p class="hint">Идёт поиск во всех карточках Ozon…</p>';
  $('replaceAttributesButton').style.display='none';
  let x=await api('/api/attributes/find',{method:'POST',body:JSON.stringify({search,replacement})});
  attributeMatches=x.matches||[];
  let skippedItems=x.skipped_video_items||[];
  let skipped=skippedItems.length?`<p><b>Видео, которые нужно переименовать вручную: ${skippedItems.length}</b></p><div class="tablewrap"><table><thead><tr><th>Товар</th><th>Артикул</th><th>Старое имя видео</th></tr></thead><tbody>${skippedItems.map(m=>`<tr><td>${esc(m.name)}</td><td><b>${esc(m.offer_id)}</b></td><td>${esc(m.file_name)}</td></tr>`).join('')}</tbody></table></div><p class="hint">Ozon не разрешает переименовывать уже загруженные ролики через API. Откройте товар по артикулу, удалите старое видео и загрузите файл с новым именем.</p>`:'';
  $('attributeMatches').innerHTML=(attributeMatches.length?`<p><b>Можно изменить значений: ${attributeMatches.length}</b></p><div class="tablewrap"><table><thead><tr><th>Товар</th><th>Артикул</th><th>Характеристика</th><th>Замена</th></tr></thead><tbody>${attributeMatches.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.offer_id)}</td><td>ID ${m.attribute_id}${m.complex_id?' / блок '+m.complex_id:''}</td><td>${esc(m.old_value)} → <b>${esc(m.new_value)}</b></td></tr>`).join('')}</tbody></table></div>`:'<p class="hint">Совпадений в изменяемых характеристиках нет.</p>')+skipped;
  $('replaceAttributesButton').style.display=attributeMatches.length?'inline-block':'none';
  toast(attributeMatches.length?`Найдено: ${attributeMatches.length}`:'Совпадений нет');
 }catch(e){$('attributeMatches').innerHTML='';toast(e.message,true)}
}
async function replaceAttributes(){
 const search=$('attrSearch').value.trim(),replacement=$('attrReplacement').value.trim();
 if(!attributeMatches.length)return;
 if(!confirm(`Заменить «${search}» на «${replacement}» во всех ${attributeMatches.length} найденных значениях?`))return;
 try{
  $('replaceAttributesButton').disabled=true;
  let x=await api('/api/attributes/replace',{method:'POST',body:JSON.stringify({search,replacement,confirmed:true})});
  toast(x.message);attributeMatches=[];$('replaceAttributesButton').style.display='none';
  $('attributeMatches').innerHTML=`<p class="good">${esc(x.message)}</p><p class="hint">Ozon обрабатывает изменения в очереди. Обновление карточек может занять несколько минут.</p>`;
  await load();
 }catch(e){toast(e.message,true)}finally{$('replaceAttributesButton').disabled=false}
}
async function saveCost(id){try{await api(`/api/product/${id}/cost`,{method:'POST',body:JSON.stringify({cost:$('c'+id).value,commission_pct:$('f'+id).value,logistics:$('l'+id).value})});toast('Расходы сохранены');load()}catch(e){toast(e.message,true)}}
async function saveProfitSettings(){try{let x=await api('/api/profit-protection/settings',{method:'POST',body:JSON.stringify({target_margin_pct:$('targetMargin').value})});toast(x.message);await load()}catch(e){toast(e.message,true)}}
async function applyCostRule(){
 const prefix=$('costPrefix').value.trim(),cost=$('costRuleValue').value;
 if(!prefix||cost===''){toast('Укажите начало артикула и себестоимость',true);return}
 try{let x=await api('/api/cost-rules/apply',{method:'POST',body:JSON.stringify({prefix,cost})});toast(x.message);$('costPrefix').value='';$('costRuleValue').value='';await load()}catch(e){toast(e.message,true)}
}
async function competitor(id){try{let x=await api(`/api/product/${id}/competitor`,{method:'POST'});toast(x.price?'Цена конкурента: '+money(x.price):'Ozon не вернул цену конкурента');load()}catch(e){toast(e.message,true)}}
async function propose(id){let margin=prompt('Минимальная желаемая прибыль, %',String(data.target_margin_pct||15));if(margin===null)return;try{let x=await api(`/api/product/${id}/propose`,{method:'POST',body:JSON.stringify({margin_pct:margin})});toast('Предложенная цена: '+money(x.price));load()}catch(e){toast(e.message,true)}}
async function proposal(id,action){if(action==='approve'&&!confirm('Отправить новую цену в Ozon?'))return;try{await api(`/api/proposal/${id}/${action}`,{method:'POST'});toast(action==='approve'?'Цена отправлена в Ozon':'Предложение отклонено');load()}catch(e){toast(e.message,true)}}
async function answer(id){if(!confirm('Отправить этот ответ покупателю?'))return;try{await api(`/api/message/${encodeURIComponent(id)}/answer`,{method:'POST',body:JSON.stringify({text:$('a'+id).value})});toast('Ответ отправлен');load()}catch(e){toast(e.message,true)}}
async function checkUpdate(show=true){try{let x=await api('/api/update/check');$('updateStatus').textContent=x.available?`Доступна версия ${x.latest}`:`Установлена последняя версия ${x.current}`;$('installUpdate').style.display=x.available?'inline-block':'none';$('updateNotes').textContent=x.notes||'';if(show)toast(x.available?'Доступно обновление '+x.latest:'Обновлений нет')}catch(e){$('updateStatus').textContent=e.message;if(show)toast(e.message,true)}}
async function installUpdate(){if(!confirm('Установить обновление и перезапустить программу?'))return;try{let x=await api('/api/update/install',{method:'POST'});toast(x.message);$('updateStatus').textContent='Установка обновления…'}catch(e){toast(e.message,true)}}
load().then(()=>checkUpdate(false)).catch(e=>toast(e.message,true));
setInterval(()=>checkUpdate(false),300000);

function productPhoto(p){
 let url=String(p.image_url||'');
 if(!url.startsWith('https://'))return '<span class="product-photo empty">Нет фото</span>';
 return `<a class="product-photo" href="${esc(url)}" target="_blank" rel="noopener noreferrer" title="Открыть фото крупнее"><img src="${esc(url)}" alt="${esc(p.name)}" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentElement.replaceWith(Object.assign(document.createElement('span'),{className:'product-photo empty',textContent:'Фото недоступно'}))"></a>`;
}
