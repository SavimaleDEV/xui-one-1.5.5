/* XUI mass queue integration v1. Native POSTs keep the original PHP entry point. */
(function () {
    'use strict';
    if (window.XuiMassQueue) { window.XuiMassQueue.install(); return; }
    const api = {version:'1',csrf:null,job:null,running:false,original:null,install,refresh};
    window.XuiMassQueue = api;
    let card, description, progress, detail, action, wake, timer;
    const observedJobs=new Set();
    const terminal = job => !job || ['complete','stopped'].includes(job.phase);
    const endpoint = () => new URL('xui_mass_queue.php',location.href).href;
    function showError(message) {
        if (typeof window.showError === 'function') window.showError(message);
        else window.alert(message);
        document.querySelectorAll('input[type=submit],button[type=submit]').forEach(node=>{node.disabled=false;});
    }
    async function request(op, input) {
        const url=new URL(endpoint());url.searchParams.set('op',op);
        if (op==='status') url.searchParams.set('id',api.job.id);
        const options={credentials:'same-origin',cache:'no-store',headers:{'Accept':'application/json'}};
        if (input) {options.method='POST';options.headers['Content-Type']='application/json';options.headers['X-XUI-CSRF']=api.csrf;options.body=JSON.stringify(input);}
        const response=await fetch(url,options);let data;
        try{data=await response.json();}catch(_){throw Error('A fila não respondeu. Atualize o painel para acompanhar o estado salvo.');}
        if(!response.ok||!data.ok)throw Error(data.message||'Não foi possível consultar a fila.');
        return data;
    }
    function attach() {
        if(window.rCurrentPage!=='stream_mass'||!document.querySelector('#streams'))return;
        if(document.querySelector('#xui-mass-queue-card'))return;
        card=document.createElement('section');card.id='xui-mass-queue-card';card.className='card';
        card.style.cssText='padding:16px;margin:0 0 16px;border-left:4px solid #3b82f6';
        const title=document.createElement('strong');title.textContent='Troca de servidor em fila';
        description=document.createElement('p');description.style.margin='8px 0';
        progress=document.createElement('progress');progress.style.cssText='width:100%;height:18px';progress.max=1;progress.value=0;
        detail=document.createElement('div');detail.style.cssText='margin:8px 0;white-space:pre-wrap';
        action=document.createElement('button');action.type='button';action.className='btn btn-warning btn-sm';action.textContent='Parar novos lotes';
        wake=document.createElement('button');wake.type='button';wake.className='btn btn-secondary btn-sm';wake.style.marginLeft='8px';wake.textContent='Retomar acompanhamento';
        action.onclick=async()=>{try{const r=await request('cancel',{id:api.job.id});api.job=r.job;render();}catch(e){showError(e.message);}};
        wake.onclick=()=>run();
        card.append(title,description,progress,detail,action,wake);
        const form=document.querySelector('#streams').form;form.parentNode.insertBefore(card,form);
        render();
    }
    function render() {
        attach();if(!card||!document.contains(card))return;
        const job=api.job;
        if(!job){description.textContent='Você pode selecionar todos os canais e enviar uma vez. Com Restart on Edit, a fila aguarda o monitor do destino antes de liberar o próximo grupo.';progress.hidden=true;detail.textContent='Disponível para SET SERVERS com um destino. Marque somente Servidores nesta troca.';action.hidden=true;wake.hidden=true;return;}
        if(!terminal(job))observedJobs.add(job.id);
        if(job.phase==='complete'&&observedJobs.has(job.id)&&window.rCurrentPage==='stream_mass'){
            observedJobs.delete(job.id);window.rSelected=[];
            const count=document.querySelector('#selected_count');if(count)count.textContent=' - 0 selected';
            document.querySelectorAll('#datatable-mass tr.selected').forEach(row=>row.classList.remove('selected','selectedfilter','ui-selected'));
        }
        progress.hidden=false;progress.max=job.total;progress.value=job.done;
        const labels={queued:'Na fila',reserved:'Preparando envio',dispatched:'Aplicando lote',waiting:'Confirmando atividade no destino',blocked:'Pausada para verificação',uncertain:'Conferindo lote sem confirmação',complete:'Concluída',stopped:'Parada'};
        description.textContent=(labels[job.phase]||job.phase)+' — '+job.done+' de '+job.total+' '+(job.mode==='transfer'?'confirmados':'alterados')+'.';
        const parts=[];
        if(job.mode==='metadata')parts.push('Sem Restart on Edit: altera a associação; não inicia a transferência dos streams ativos.');
        if(job.current.length)parts.push('Lote atual: '+job.current.join(', '));
        if(job.message)parts.push(job.message);
        if(job.stopRequested&&!terminal(job))parts.push('Parada solicitada. O lote já enviado precisa ser confirmado; novos lotes não serão enviados.');
        detail.textContent=parts.join('\n');action.hidden=terminal(job)||job.stopRequested;wake.hidden=terminal(job)||api.running;
        if(window.rCurrentPage==='stream_mass'){
            const form=document.querySelector('#streams')?.form;if(form)form.inert=!terminal(job);
            document.querySelectorAll('input[type=submit],button[type=submit]').forEach(node=>{node.disabled=!terminal(job);});
        }
    }
    async function refresh() {
        if(window.rCurrentPage!=='stream_mass')return;
        try{const info=await request('info');api.csrf=info.csrf;api.job=info.job;attach();render();if(!terminal(api.job))run();}
        catch(e){attach();if(detail)detail.textContent=e.message;}
    }
    async function run() {
        if(api.running||terminal(api.job))return;
        api.running=true;render();
        try{
            while(!terminal(api.job)){
                const response=await request('next',{id:api.job.id});api.job=response.job;render();
                if(response.dispatch){
                    const body=new FormData();for(const [key,value] of Object.entries(response.dispatch.post)){
                        if(Array.isArray(value))for(const item of value)body.append(key+'[]',String(item));
                        else body.append(key,String(value));
                    }
                    const url=new URL('post.php',location.href);url.searchParams.set('action','stream_mass');url.searchParams.set('referer','');
                    // Never retry this request. The server records its result before replying.
                    try{await fetch(url,{method:'POST',credentials:'same-origin',headers:{'X-XUI-Mass-Token':response.dispatch.token},body});}
                    catch(_){/* Poll the persisted state; never send the token twice. */}
                }
                if(!terminal(api.job))await new Promise(resolve=>setTimeout(resolve,1200));
            }
        }catch(e){if(detail)detail.textContent=e.message;}
        finally{api.running=false;render();}
    }
    async function enqueue(data) {
        if(!terminal(api.job)){showError('Já existe uma troca sua em andamento. Acompanhe ou pare a fila atual.');render();return;}
        try{
            if(!api.csrf){const info=await request('info');api.csrf=info.csrf;api.job=info.job;if(!terminal(api.job)){render();run();return;}}
            const entries=[];for(const [key,value] of data.entries()){if(typeof value!=='string')throw Error('Arquivo não suportado nesta troca.');entries.push([key,value]);}
            const response=await request('create',{entries});api.job=response.job;render();run();
        }catch(e){showError(e.message);}
    }
    function install() {
        if(typeof window.submitForm==='function'&&!window.submitForm.xuiQueue){
            const original=window.submitForm;api.original=original;
            const wrapped=function(type,data,referer){
                if(type==='stream_mass'&&data instanceof FormData&&data.has('c_server_tree')&&data.get('server_type')==='SET'){enqueue(data);return;}
                return original.call(this,type,data,referer);
            };wrapped.xuiQueue=true;window.submitForm=wrapped;
        }
        attach();clearTimeout(timer);timer=setTimeout(refresh,150);
    }
    if(window.jQuery)window.jQuery(document).ajaxComplete(()=>{if(window.rCurrentPage==='stream_mass')install();});
    window.addEventListener('beforeunload',event=>{if(api.running&&!terminal(api.job)){event.preventDefault();event.returnValue='';}});
    install();
})();
