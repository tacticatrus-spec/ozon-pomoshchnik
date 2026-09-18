let data={};const $=x=>document.getElementById(x);function money(x){return Number(x||0).toLocaleString('ru-RU',{maximumFractionDigits:2})+' ₽'}
function toast(s,bad=false){let x=$('toast');x.textContent=s;x.style.background=bad?'#b82740':'#172033';x.style.display='block';setTimeout(()=>x.style.display='none',4500)}
async function api(url,opt={}){opt.headers={'Content-Type':'application/json'};let r=await fetch(url,opt),j=await r.json();if(!r.ok)throw Error(j.message||'Ошибка');return j}
async function load(){data=await api('/api/dashboard');$('nProducts').textContent=data.products.length;$('nStock').textContent=data.products.reduce((a,x)=>a+x.stock,0);$('nOrders').textContent=data.order_count;$('nProposals').textContent=data.proposals.length;$('appVersion').textContent='Версия '+data.version;render()}
function render(){
 $('events').innerHTML=data.events.map(x=>`<div class="event"><small>${x.created_at}</small><div>${esc(x.message)}</div></div>`).join('')||'<p class="hint">Пока событий нет</p>';
 $('productsBody').innerHTML=data.products.map(p=>`<tr><td class="name"><b>${esc(p.name)}</b><br><small>${esc(p.offer_id)}</small></td><td>${money(p.price)}</td><td>${p.competitor_price?money(p.competitor_price):'—'}</td><td>${p.stock}</td><td><input id="c${p.product_id}" value="${p.cost}"></td><td><input id="f${p.product_id}" value="${p.commission_pct}"></td><td><input id="l${p.product_id}" value="${p.logistics}"></td><td class="${p.profit>=0?'good':'bad'}">${money(p.profit)}</td><td><div class="actions"><button onclick="saveCost(${p.product_id})">Сохранить</button><button onclick="competitor(${p.product_id})">Конкурент</button><button class="primary" onclick="propose(${p.product_id})">Предложить</button></div></td></tr>`).join('');
 $('proposals').innerHTML=data.proposals.map(p=>`<div class="proposal"><b>${money(p.current_price)} → ${money(p.proposed_price)}</b><div class="hint">${esc(p.reason)}</div><div class="actions"><button class="primary" onclick="proposal(${p.id},'approve')">Подтвердить цену</button><button onclick="proposal(${p.id},'reject')">Отклонить</button></div></div>`).join('')||'<p class="hint">Нет предложений на рассмотрении</p>';
 $('ordersBody').innerHTML=data.orders.map(o=>`<tr><td>${esc(o.posting_number)}</td><td><span class="pill">${o.scheme}</span></td><td>${esc(o.status)}</td><td>${esc(o.created_at)}</td><td>${money(o.amount)}</td></tr>`).join('');
 $('messagesList').innerHTML=data.messages.map(m=>`<div class="message"><small>${m.kind==='review'?'Отзыв':'Вопрос'} • ${esc(m.created_at)}</small><p>${esc(m.text)}</p>${m.status!=='PROCESSED'?`<textarea class="answer" id="a${m.id}" placeholder="Введите ответ"></textarea><button class="primary" onclick="answer('${m.id}')">Отправить ответ</button>`:'<span class="good">Обработано</span>'}</div>`).join('')||'<p class="hint">Новых отзывов и вопросов нет</p>';
}
function esc(x){return String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>{document.querySelectorAll('nav button,.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');$(b.dataset.tab).classList.add('active');$('title').textContent=b.textContent});
async function saveSettings(){try{await api('/api/settings',{method:'POST',body:JSON.stringify({client_id:$('clientId').value,api_key:$('apiKey').value,telegram_token:$('tgToken').value,telegram_chat_id:$('chatId').value})});toast('Настройки сохранены');await load()}catch(e){toast(e.message,true)}}
async function testOzon(){try{toast((await api('/api/test',{method:'POST'})).message)}catch(e){toast(e.message,true)}}
async function findChat(){try{let x=await api('/api/telegram/chat');$('chatId').value=x.chat_id;toast('Telegram подключён')}catch(e){toast(e.message,true)}}
async function syncAll(){try{toast('Синхронизация началась…');let x=await api('/api/sync',{method:'POST'});toast(x.message);await load()}catch(e){toast(e.message,true)}}
async function demo(){await api('/api/demo',{method:'POST'});toast('Демо-данные загружены');load()}
let attributeMatches=[];
async function findAttributes(){
 const search=$('attrSearch').value.trim(),replacement=$('attrReplacement').value.trim();
 if(!search||!replacement){toast('Заполните оба поля',true);return}
 try{
  $('attributeMatches').innerHTML='<p class="hint">Идёт поиск во всех карточках Ozon…</p>';
  $('replaceAttributesButton').style.display='none';
  let x=await api('/api/attributes/find',{method:'POST',body:JSON.stringify({search,replacement})});
  attributeMatches=x.matches||[];
  $('attributeMatches').innerHTML=attributeMatches.length?`<p><b>Найдено значений: ${attributeMatches.length}</b></p><div class="tablewrap"><table><thead><tr><th>Товар</th><th>Артикул</th><th>Характеристика</th><th>Замена</th></tr></thead><tbody>${attributeMatches.map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.offer_id)}</td><td>ID ${m.attribute_id}${m.complex_id?' / блок '+m.complex_id:''}</td><td>${esc(m.old_value)} → <b>${esc(m.new_value)}</b></td></tr>`).join('')}</tbody></table></div>`:'<p class="hint">Точное значение не найдено.</p>';
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
async function competitor(id){try{let x=await api(`/api/product/${id}/competitor`,{method:'POST'});toast(x.price?'Цена конкурента: '+money(x.price):'Ozon не вернул цену конкурента');load()}catch(e){toast(e.message,true)}}
async function propose(id){let margin=prompt('Минимальная желаемая прибыль, %','15');if(margin===null)return;try{let x=await api(`/api/product/${id}/propose`,{method:'POST',body:JSON.stringify({margin_pct:margin})});toast('Предложенная цена: '+money(x.price));load()}catch(e){toast(e.message,true)}}
async function proposal(id,action){if(action==='approve'&&!confirm('Отправить новую цену в Ozon?'))return;try{await api(`/api/proposal/${id}/${action}`,{method:'POST'});toast(action==='approve'?'Цена отправлена в Ozon':'Предложение отклонено');load()}catch(e){toast(e.message,true)}}
async function answer(id){if(!confirm('Отправить этот ответ покупателю?'))return;try{await api(`/api/message/${encodeURIComponent(id)}/answer`,{method:'POST',body:JSON.stringify({text:$('a'+id).value})});toast('Ответ отправлен');load()}catch(e){toast(e.message,true)}}
async function checkUpdate(show=true){try{let x=await api('/api/update/check');$('updateStatus').textContent=x.available?`Доступна версия ${x.latest}`:`Установлена последняя версия ${x.current}`;$('installUpdate').style.display=x.available?'inline-block':'none';$('updateNotes').textContent=x.notes||'';if(show)toast(x.available?'Доступно обновление '+x.latest:'Обновлений нет')}catch(e){$('updateStatus').textContent=e.message;if(show)toast(e.message,true)}}
async function installUpdate(){if(!confirm('Установить обновление и перезапустить программу?'))return;try{let x=await api('/api/update/install',{method:'POST'});toast(x.message);$('updateStatus').textContent='Установка обновления…'}catch(e){toast(e.message,true)}}
load().then(()=>checkUpdate(false)).catch(e=>toast(e.message,true));
setInterval(()=>checkUpdate(false),300000);
