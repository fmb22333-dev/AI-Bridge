const $=(selector)=>document.querySelector(selector);
let allCommands=[];

async function api(path){
  const response=await fetch(path,{headers:{"Content-Type":"application/json"}});
  const text=await response.text();let data={};
  try{data=text?JSON.parse(text):{};}catch{data={detail:text};}
  if(!response.ok)throw new Error(data.detail||text||`HTTP ${response.status}`);
  return data;
}
function esc(value){return String(value??"").replace(/[&<>"']/g,(char)=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));}
function timeText(value){if(!value)return "—";const date=new Date(String(value).replace(" ","T")+"Z");return Number.isNaN(date.getTime())?String(value):date.toLocaleString();}
function render(){
  const query=$("#historyFilter").value.trim().toLowerCase();
  const items=query?allCommands.filter((item)=>[item.command_id,item.workspace_id,item.adapter,item.session_id,item.operation,item.status].some((value)=>String(value??"").toLowerCase().includes(query))):allCommands;
  $("#historyStats").textContent=`显示 ${items.length} / ${allCommands.length}`;
  $("#commandHistory").innerHTML=items.length?items.map((c)=>`<tr>
    <td>${esc(timeText(c.created_at))}</td>
    <td class="mono">${esc(c.command_id)}</td>
    <td>${esc(c.workspace_id||"—")}</td>
    <td><b>${esc(c.adapter)}</b><small>${esc(c.session_id||"")}</small></td>
    <td>${esc(c.operation)}</td>
    <td><span class="statusPill ${esc(c.status||"")}">${esc(c.status||"unknown")}</span></td>
    <td>${c.evidence_id?`<a href="/control/evidence/${encodeURIComponent(c.evidence_id)}" target="_blank">${esc(c.evidence_id)}</a>`:"—"}</td>
    <td><a href="/control/commands/${encodeURIComponent(c.command_id)}" target="_blank">JSON</a></td>
  </tr>`).join(""):'<tr><td colspan="8" class="muted">没有匹配命令</td></tr>';
}
async function refresh(){
  try{
    $("#historyError").textContent="";
    const limit=Number($("#historyLimit").value||100);
    const data=await api("/control/commands?limit="+encodeURIComponent(limit));
    allCommands=data.commands||[];
    render();
  }catch(error){$("#historyError").textContent=error.message;}
}
$("#historyFilter").oninput=render;
$("#historyLimit").onchange=refresh;
$("#refreshHistory").onclick=refresh;
refresh();
setInterval(refresh,5000);
