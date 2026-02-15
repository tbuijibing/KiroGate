// KiroGate 前端页面渲染 - 嵌入式 HTML + Tailwind CSS + ECharts
// 保持原 KiroGate 风格，新增账号管理、API Key 管理等页面

const PROXY_BASE = "https://proxy.jhun.edu.kg"

export function getPageConfig(version: string) {
  return { version, proxyBase: PROXY_BASE }
}

// ============================================================================
// 公共 HTML 片段
// ============================================================================
export function commonHead(version: string): string {
  return `
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>KiroGate - OpenAI & Anthropic 兼容的 Kiro API 代理网关</title>
  <meta name="description" content="KiroGate 是一个开源的 Kiro IDE API 代理网关，支持 OpenAI 和 Anthropic API 格式。">
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚀</text></svg>">
  <script src="${PROXY_BASE}/proxy/cdn.tailwindcss.com"></script>
  <script src="${PROXY_BASE}/proxy/cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <script src="${PROXY_BASE}/proxy/cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
  <style>
    :root { --primary: #6366f1; --primary-dark: #4f46e5; }
    [data-theme="light"] { --bg-main:#fff;--bg-card:#f8fafc;--bg-nav:#fff;--bg-input:#fff;--text:#0f172a;--text-muted:#64748b;--border:#e2e8f0;--border-dark:#cbd5e1; }
    [data-theme="dark"] { --bg-main:#0f172a;--bg-card:#1e293b;--bg-nav:#1e293b;--bg-input:#334155;--text:#e2e8f0;--text-muted:#94a3b8;--border:#334155;--border-dark:#475569; }
    body { background:var(--bg-main);color:var(--text);font-family:system-ui,-apple-system,sans-serif;transition:background-color .3s,color .3s; }
    .card { background:var(--bg-card);border-radius:.75rem;padding:1.5rem;border:1px solid var(--border);transition:background-color .3s,border-color .3s; }
    .btn-primary { background:var(--primary);color:#fff;padding:.5rem 1rem;border-radius:.5rem;transition:all .2s; }
    .btn-primary:hover { background:var(--primary-dark); }
    .nav-link { color:var(--text-muted);transition:color .2s; }
    .nav-link:hover,.nav-link.active { color:var(--primary); }
    .theme-toggle { cursor:pointer;padding:.5rem;border-radius:.5rem;transition:background-color .2s; }
    .theme-toggle:hover { background:var(--bg-card); }
    pre { max-width:100%;overflow-x:auto; }
    .loading-spinner { display:inline-block;width:20px;height:20px;border:2px solid var(--border);border-radius:50%;border-top-color:var(--primary);animation:spin .8s linear infinite; }
    @keyframes spin { to { transform:rotate(360deg); } }
    .loading-pulse { animation:pulse 1.5s ease-in-out infinite; }
    @keyframes pulse { 0%,100%{opacity:1}50%{opacity:.5} }
    .table-responsive { overflow-x:auto; }
    .mc{background:var(--bg-card);border:1px solid var(--border);border-radius:.75rem;padding:1rem;text-align:center}.mc:hover{border-color:var(--primary)}.mi{font-size:1.5rem;margin-bottom:.5rem}
  </style>
  <script>(function(){const t=localStorage.getItem('theme')||'light';document.documentElement.setAttribute('data-theme',t)})();</script>`
}

export function commonNav(version: string): string {
  return `
  <nav style="background:var(--bg-nav);border-bottom:1px solid var(--border);" class="sticky top-0 z-50">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
      <div class="flex justify-between h-16">
        <div class="flex items-center space-x-8">
          <a href="/" class="text-2xl font-bold text-indigo-500">⚡ KiroGate</a>
          <div class="hidden md:flex space-x-6">
            <a href="/" class="nav-link">首页</a>
            <a href="/docs" class="nav-link">文档</a>
            <a href="/swagger" class="nav-link">Swagger</a>
            <a href="/playground" class="nav-link">Playground</a>
            <a href="/deploy" class="nav-link">部署</a>
            <a href="/dashboard" class="nav-link">Dashboard</a>
            <a href="/admin/accounts" class="nav-link">账号</a>
            <a href="/admin/keys" class="nav-link">Key</a>
          </div>
        </div>
        <div class="flex items-center space-x-4">
          <button onclick="toggleTheme()" class="theme-toggle" title="切换主题">
            <svg id="theme-icon-sun" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="display:none;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"/></svg>
            <svg id="theme-icon-moon" class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="display:none;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>
          </button>
          <span class="hidden sm:inline text-sm" style="color:var(--text-muted);">v${version}</span>
          <button onclick="toggleMobileMenu()" class="md:hidden theme-toggle"><svg id="menu-icon-open" class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/></svg><svg id="menu-icon-close" class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24" style="display:none;"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"/></svg></button>
        </div>
      </div>
    </div>
    <div id="mobile-menu" class="md:hidden hidden" style="background:var(--bg-nav);border-top:1px solid var(--border);">
      <div class="px-4 py-3 space-y-2">
        <a href="/" class="block nav-link py-2 px-3 rounded hover:bg-indigo-500/10">首页</a>
        <a href="/docs" class="block nav-link py-2 px-3 rounded hover:bg-indigo-500/10">文档</a>
        <a href="/playground" class="block nav-link py-2 px-3 rounded hover:bg-indigo-500/10">Playground</a>
        <a href="/dashboard" class="block nav-link py-2 px-3 rounded hover:bg-indigo-500/10">Dashboard</a>
        <a href="/admin/accounts" class="block nav-link py-2 px-3 rounded hover:bg-indigo-500/10">账号管理</a>
        <a href="/admin/keys" class="block nav-link py-2 px-3 rounded hover:bg-indigo-500/10">Key 管理</a>
      </div>
    </div>
  </nav>
  <script>
    function toggleTheme(){const h=document.documentElement,c=h.getAttribute('data-theme'),n=c==='dark'?'light':'dark';h.setAttribute('data-theme',n);localStorage.setItem('theme',n);updateThemeIcon();}
    function updateThemeIcon(){const t=document.documentElement.getAttribute('data-theme'),s=document.getElementById('theme-icon-sun'),m=document.getElementById('theme-icon-moon');if(t==='dark'){s.style.display='block';m.style.display='none';}else{s.style.display='none';m.style.display='block';}}
    function toggleMobileMenu(){const m=document.getElementById('mobile-menu'),o=document.getElementById('menu-icon-open'),c=document.getElementById('menu-icon-close');if(m.classList.contains('hidden')){m.classList.remove('hidden');o.style.display='none';c.style.display='block';}else{m.classList.add('hidden');o.style.display='block';c.style.display='none';}}
    document.addEventListener('DOMContentLoaded',updateThemeIcon);
  </script>`
}

export function commonFooter(): string {
  return `
  <footer style="background:var(--bg-nav);border-top:1px solid var(--border);" class="py-6 sm:py-8 mt-12 sm:mt-16">
    <div class="max-w-7xl mx-auto px-4 text-center" style="color:var(--text-muted);">
      <p class="text-sm sm:text-base">KiroGate - OpenAI & Anthropic 兼容的 Kiro API 网关</p>
      <div class="mt-3 flex flex-wrap justify-center gap-x-4 gap-y-2 text-xs sm:text-sm">
        <a href="https://github.com/dext7r/KiroGate" class="text-indigo-400 hover:underline" target="_blank">GitHub</a>
      </div>
      <p class="mt-3 text-xs sm:text-sm opacity-75">欲买桂花同载酒 终不似少年游</p>
    </div>
  </footer>`
}

// ============================================================================
// 可用模型列表
// ============================================================================
const AVAILABLE_MODELS = [
  'claude-opus-4-5', 'claude-sonnet-4-5', 'claude-sonnet-4',
  'claude-haiku-4-5', 'claude-3-7-sonnet-20250219'
]

// ============================================================================
// 首页
// ============================================================================
export function renderHomePage(version: string): string {
  return `<!DOCTYPE html><html lang="zh"><head>${commonHead(version)}</head><body>
${commonNav(version)}
<main class="max-w-7xl mx-auto px-4 py-8 sm:py-12">
  <section class="text-center py-8 sm:py-16">
    <h1 class="text-3xl sm:text-5xl font-bold mb-4 sm:mb-6 bg-gradient-to-r from-indigo-400 to-purple-500 bg-clip-text text-transparent">KiroGate API 网关</h1>
    <p class="text-base sm:text-xl mb-6 sm:mb-8 max-w-2xl mx-auto px-4" style="color:var(--text-muted)">将 OpenAI 和 Anthropic API 请求无缝代理到 Kiro，支持完整的流式传输、工具调用和多模型切换。</p>
    <div class="flex flex-col sm:flex-row justify-center gap-3 sm:gap-4 px-4">
      <a href="/docs" class="btn-primary text-base sm:text-lg px-6 py-3">📖 查看文档</a>
      <a href="/playground" class="btn-primary text-base sm:text-lg px-6 py-3" style="background:#334155">🎮 在线试用</a>
    </div>
  </section>
  <section class="grid md:grid-cols-3 gap-6 py-12">
    <div class="card"><div class="text-3xl mb-4">🔄</div><h3 class="text-xl font-semibold mb-2">双 API 兼容</h3><p style="color:var(--text-muted)">同时支持 OpenAI 和 Anthropic API 格式，无需修改现有代码。</p></div>
    <div class="card"><div class="text-3xl mb-4">⚡</div><h3 class="text-xl font-semibold mb-2">流式传输</h3><p style="color:var(--text-muted)">完整的 SSE 流式支持，实时获取模型响应。</p></div>
    <div class="card"><div class="text-3xl mb-4">🔧</div><h3 class="text-xl font-semibold mb-2">工具调用</h3><p style="color:var(--text-muted)">支持 Function Calling，构建强大的 AI Agent。</p></div>
    <div class="card"><div class="text-3xl mb-4">👥</div><h3 class="text-xl font-semibold mb-2">多账号调度</h3><p style="color:var(--text-muted)">智能账号池，自动负载均衡、故障转移和配额管理。</p></div>
    <div class="card"><div class="text-3xl mb-4">📊</div><h3 class="text-xl font-semibold mb-2">监控面板</h3><p style="color:var(--text-muted)">实时查看请求统计、响应时间和模型使用情况。</p></div>
    <div class="card"><div class="text-3xl mb-4">🦕</div><h3 class="text-xl font-semibold mb-2">Deno 原生</h3><p style="color:var(--text-muted)">模块化部署，内置 KV 存储，零外部依赖。</p></div>
  </section>
  <section class="py-12">
    <h2 class="text-2xl font-bold mb-6 text-center">📈 支持的模型</h2>
    <div class="card"><div id="modelsChart" style="height:300px"></div></div>
  </section>
</main>
${commonFooter()}
<script>
const mc=echarts.init(document.getElementById('modelsChart'));
mc.setOption({tooltip:{trigger:'axis'},xAxis:{type:'category',data:${JSON.stringify(AVAILABLE_MODELS)},axisLabel:{rotate:45,color:'#94a3b8'},axisLine:{lineStyle:{color:'#334155'}}},yAxis:{type:'value',name:'性能指数',axisLabel:{color:'#94a3b8'},splitLine:{lineStyle:{color:'#1e293b'}}},series:[{name:'模型能力',type:'bar',data:[100,90,85,70,80],itemStyle:{color:new echarts.graphic.LinearGradient(0,0,0,1,[{offset:0,color:'#6366f1'},{offset:1,color:'#4f46e5'}])}}]});
window.addEventListener('resize',()=>mc.resize());
</script></body></html>`
}

// ============================================================================
// 文档页
// ============================================================================
export function renderDocsPage(version: string): string {
  const modelsList = AVAILABLE_MODELS.map(m => `<li style="background:var(--bg-input);border:1px solid var(--border)" class="px-4 py-2 rounded text-sm"><code>${m}</code></li>`).join('')
  return `<!DOCTYPE html><html lang="zh"><head>${commonHead(version)}</head><body>
${commonNav(version)}
<main class="max-w-7xl mx-auto px-4 py-12">
  <h1 class="text-4xl font-bold mb-8">📖 API 文档</h1>
  <div class="space-y-8">
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">🔑 认证</h2>
      <p style="color:var(--text-muted)" class="mb-4">所有 API 请求需要在 Header 中携带 API Key。支持两种认证模式：</p>
      <h3 class="text-lg font-medium mb-2 text-indigo-400">模式 1: 简单模式</h3>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm mb-4">Authorization: Bearer YOUR_PROXY_API_KEY
x-api-key: YOUR_PROXY_API_KEY</pre>
      <h3 class="text-lg font-medium mb-2 text-indigo-400">模式 2: 组合模式（多租户）✨ 推荐</h3>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm">Authorization: Bearer YOUR_PROXY_API_KEY:YOUR_REFRESH_TOKEN</pre>
      <h3 class="text-lg font-medium mb-2 mt-4 text-indigo-400">模式 3: 托管 API Key</h3>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm">Authorization: Bearer kg-xxxxxxxxxxxxxxxx</pre>
      <p class="text-sm mt-2" style="color:var(--text-muted)">通过管理面板创建的 API Key，支持额度限制和模型限制。</p>
    </section>
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">📡 端点列表</h2>
      <div class="space-y-3">
        <div style="background:var(--bg-input);border:1px solid var(--border)" class="p-3 rounded-lg"><span class="bg-green-600 text-white text-xs px-2 py-1 rounded">GET</span> <code class="text-indigo-400 ml-2">/v1/models</code> <span class="text-sm ml-2" style="color:var(--text-muted)">模型列表</span></div>
        <div style="background:var(--bg-input);border:1px solid var(--border)" class="p-3 rounded-lg"><span class="bg-blue-600 text-white text-xs px-2 py-1 rounded">POST</span> <code class="text-indigo-400 ml-2">/v1/chat/completions</code> <span class="text-sm ml-2" style="color:var(--text-muted)">OpenAI 聊天补全</span></div>
        <div style="background:var(--bg-input);border:1px solid var(--border)" class="p-3 rounded-lg"><span class="bg-blue-600 text-white text-xs px-2 py-1 rounded">POST</span> <code class="text-indigo-400 ml-2">/v1/messages</code> <span class="text-sm ml-2" style="color:var(--text-muted)">Anthropic Messages API</span></div>
        <div style="background:var(--bg-input);border:1px solid var(--border)" class="p-3 rounded-lg"><span class="bg-green-600 text-white text-xs px-2 py-1 rounded">GET</span> <code class="text-indigo-400 ml-2">/health</code> <span class="text-sm ml-2" style="color:var(--text-muted)">健康检查</span></div>
      </div>
    </section>
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">💡 使用示例</h2>
      <h3 class="text-lg font-medium mb-2 text-indigo-400">OpenAI SDK (Python)</h3>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm mb-4">from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="YOUR_PROXY_API_KEY"
)

response = client.chat.completions.create(
    model="claude-sonnet-4-5",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
)

for chunk in response:
    print(chunk.choices[0].delta.content, end="")</pre>
      <h3 class="text-lg font-medium mb-2 text-indigo-400">cURL</h3>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm">curl http://localhost:8000/v1/chat/completions \\
  -H "Authorization: Bearer YOUR_PROXY_API_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"model":"claude-sonnet-4-5","messages":[{"role":"user","content":"Hello!"}]}'</pre>
    </section>
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">🤖 可用模型</h2>
      <ul class="grid md:grid-cols-2 gap-2">${modelsList}</ul>
    </section>
  </div>
</main>
${commonFooter()}
</body></html>`
}

// ============================================================================
// Playground 页
// ============================================================================
export function renderPlaygroundPage(version: string): string {
  const modelOptions = AVAILABLE_MODELS.map(m => `<option value="${m}">${m}</option>`).join('')
  return `<!DOCTYPE html><html lang="zh"><head>${commonHead(version)}</head><body>
${commonNav(version)}
<main class="max-w-7xl mx-auto px-4 py-12">
  <h1 class="text-4xl font-bold mb-8">🎮 API Playground</h1>
  <div class="grid md:grid-cols-2 gap-6">
    <div class="card">
      <h2 class="text-xl font-semibold mb-4">请求配置</h2>
      <div class="space-y-4">
        <div><label class="block text-sm mb-1" style="color:var(--text-muted)">API Key</label><input type="password" id="apiKey" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2" placeholder="PROXY_API_KEY 或 kg-xxx"></div>
        <div><label class="block text-sm mb-1" style="color:var(--text-muted)">模型</label><select id="model" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2">${modelOptions}</select></div>
        <div><label class="block text-sm mb-1" style="color:var(--text-muted)">消息内容</label><textarea id="message" rows="4" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2">Hello! Please introduce yourself briefly.</textarea></div>
        <div class="flex items-center gap-4">
          <label class="flex items-center gap-2"><input type="checkbox" id="stream" checked><span class="text-sm">流式</span></label>
          <label class="flex items-center gap-2"><input type="radio" name="fmt" value="openai" checked><span class="text-sm">OpenAI</span></label>
          <label class="flex items-center gap-2"><input type="radio" name="fmt" value="anthropic"><span class="text-sm">Anthropic</span></label>
        </div>
        <button id="sendBtn" onclick="sendReq()" class="btn-primary w-full py-3">🚀 发送请求</button>
      </div>
    </div>
    <div class="card">
      <h2 class="text-xl font-semibold mb-4">响应结果</h2>
      <div id="resp" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="rounded p-4 min-h-[300px] whitespace-pre-wrap text-sm font-mono overflow-auto"><span style="color:var(--text-muted)">响应将显示在这里...</span></div>
      <div id="stats" class="mt-3 text-sm" style="color:var(--text-muted)"></div>
    </div>
  </div>
</main>
${commonFooter()}
<script>
async function sendReq(){const k=document.getElementById('apiKey').value,m=document.getElementById('model').value,msg=document.getElementById('message').value,s=document.getElementById('stream').checked,f=document.querySelector('input[name=fmt]:checked').value,r=document.getElementById('resp'),st=document.getElementById('stats'),b=document.getElementById('sendBtn');b.disabled=true;r.innerHTML='<span class="loading-pulse" style="color:var(--text-muted)">请求中...</span>';st.textContent='';const t0=Date.now();try{const ep=f==='openai'?'/v1/chat/completions':'/v1/messages',h={'Content-Type':'application/json'};if(f==='openai')h['Authorization']='Bearer '+k;else h['x-api-key']=k;const bd=f==='openai'?{model:m,messages:[{role:'user',content:msg}],stream:s}:{model:m,max_tokens:1024,messages:[{role:'user',content:msg}],stream:s};const res=await fetch(ep,{method:'POST',headers:h,body:JSON.stringify(bd)});if(!res.ok)throw new Error(await res.text());if(s){r.textContent='';const rd=res.body.getReader(),dc=new TextDecoder();let fc='',buf='';while(true){const{done,value}=await rd.read();if(done)break;buf+=dc.decode(value,{stream:true});const lines=buf.split('\\n');buf=lines.pop()||'';for(const line of lines){const l=line.trim();if(f==='openai'){if(l.startsWith('data: ')&&!l.includes('[DONE]')){try{const d=JSON.parse(l.slice(6));fc+=d.choices?.[0]?.delta?.content||''}catch{}}}else{if(l.startsWith('data: ')&&l.includes('text_delta')){try{const d=JSON.parse(l.slice(6));fc+=d.delta?.text||''}catch{}}}}}r.textContent=fc}else{const d=await res.json();r.textContent=f==='openai'?d.choices?.[0]?.message?.content||JSON.stringify(d,null,2):d.content?.find(c=>c.type==='text')?.text||JSON.stringify(d,null,2)}st.textContent='耗时: '+((Date.now()-t0)/1000).toFixed(2)+'s'}catch(e){r.textContent='错误: '+e.message}finally{b.disabled=false}}
</script></body></html>`
}

// ============================================================================
// 部署页
// ============================================================================
export function renderDeployPage(version: string): string {
  return `<!DOCTYPE html><html lang="zh"><head>${commonHead(version)}</head><body>
${commonNav(version)}
<main class="max-w-7xl mx-auto px-4 py-12">
  <h1 class="text-4xl font-bold mb-8">🚀 部署指南</h1>
  <div class="space-y-8">
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">⚙️ 环境变量</h2>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm">
# 必填
PROXY_API_KEY="your-secret-api-key"
ADMIN_PASSWORD="your-admin-password"

# 可选
PORT="8000"
LOG_LEVEL="INFO"
RATE_LIMIT_PER_MINUTE="0"
ENABLE_COMPRESSION="true"</pre>
    </section>
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">🦕 Deno 本地运行</h2>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm">
# 设置环境变量
export PROXY_API_KEY="your-secret-key"
export ADMIN_PASSWORD="admin123"

# 运行
deno run --allow-net --allow-env --unstable-kv main.ts</pre>
    </section>
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">🐳 Docker 部署</h2>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm">
FROM denoland/deno:latest
WORKDIR /app
COPY . .
EXPOSE 8000
CMD ["run", "--allow-net", "--allow-env", "--unstable-kv", "main.ts"]</pre>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm mt-4">
docker build -t kirogate .
docker run -d -p 8000:8000 \\
  -e PROXY_API_KEY="your-key" \\
  -e ADMIN_PASSWORD="admin123" \\
  kirogate</pre>
    </section>
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">☁️ Deno Deploy</h2>
      <pre style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="p-4 rounded-lg overflow-x-auto text-sm">
deno install -A jsr:@deno/deployctl
deployctl deploy --project=your-project main.ts</pre>
    </section>
    <section class="card">
      <h2 class="text-2xl font-semibold mb-4">📋 使用流程</h2>
      <ol class="list-decimal list-inside space-y-2" style="color:var(--text-muted)">
        <li>部署服务并设置 PROXY_API_KEY 和 ADMIN_PASSWORD</li>
        <li>访问 <code>/admin/accounts</code> 添加 Kiro 账号（需要 refreshToken）</li>
        <li>可选：访问 <code>/admin/keys</code> 创建 API Key 分发给用户</li>
        <li>用户使用 API Key 调用 <code>/v1/chat/completions</code> 或 <code>/v1/messages</code></li>
      </ol>
    </section>
  </div>
</main>
${commonFooter()}
</body></html>`
}

// ============================================================================
// Dashboard 页
// ============================================================================
export function renderDashboardPage(version: string): string {
  return `<!DOCTYPE html><html lang="zh"><head>${commonHead(version)}</head><body>
${commonNav(version)}
<main class="max-w-7xl mx-auto px-4 py-8">
  <div class="flex justify-between items-center mb-6">
    <h1 class="text-3xl font-bold">📊 Dashboard</h1>
    <button onclick="refreshData()" class="btn-primary">🔄 刷新</button>
  </div>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
    <div class="mc"><div class="mi">📈</div><div class="text-2xl font-bold text-indigo-400" id="totalReq">-</div><div class="text-xs" style="color:var(--text-muted)">总请求</div></div>
    <div class="mc"><div class="mi">✅</div><div class="text-2xl font-bold text-green-400" id="successRate">-</div><div class="text-xs" style="color:var(--text-muted)">成功率</div></div>
    <div class="mc"><div class="mi">👥</div><div class="text-2xl font-bold text-blue-400" id="accountCount">-</div><div class="text-xs" style="color:var(--text-muted)">账号数</div></div>
    <div class="mc"><div class="mi">🕐</div><div class="text-2xl font-bold text-purple-400" id="uptime">-</div><div class="text-xs" style="color:var(--text-muted)">运行时长</div></div>
  </div>
  <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
    <div class="mc"><div class="mi">⚡</div><div class="text-xl font-bold text-blue-400" id="streamReq">-</div><div class="text-xs" style="color:var(--text-muted)">流式请求</div></div>
    <div class="mc"><div class="mi">💾</div><div class="text-xl font-bold text-cyan-400" id="nonStreamReq">-</div><div class="text-xs" style="color:var(--text-muted)">非流式</div></div>
    <div class="mc"><div class="mi">❌</div><div class="text-xl font-bold text-red-400" id="errorReq">-</div><div class="text-xs" style="color:var(--text-muted)">失败</div></div>
    <div class="mc"><div class="mi">🔑</div><div class="text-xl font-bold text-yellow-400" id="totalTokens">-</div><div class="text-xs" style="color:var(--text-muted)">总 Tokens</div></div>
  </div>
  <div class="grid lg:grid-cols-2 gap-4 mb-6">
    <div class="card"><h2 class="text-lg font-semibold mb-3">📈 请求趋势</h2><div id="trendChart" style="height:250px"></div></div>
    <div class="card"><h2 class="text-lg font-semibold mb-3">📊 状态分布</h2><div style="height:250px;position:relative"><canvas id="statusChart"></canvas></div></div>
  </div>
  <div class="card">
    <h2 class="text-lg font-semibold mb-3">📋 最近请求</h2>
    <div class="table-responsive"><table class="w-full text-xs"><thead><tr class="text-left" style="color:var(--text-muted);border-bottom:1px solid var(--border)"><th class="py-2 px-2">时间</th><th class="py-2 px-2">路径</th><th class="py-2 px-2">状态</th><th class="py-2 px-2">耗时</th><th class="py-2 px-2">模型</th><th class="py-2 px-2">账号</th></tr></thead><tbody id="reqTable"><tr><td colspan="6" class="py-4 text-center" style="color:var(--text-muted)">加载中...</td></tr></tbody></table></div>
  </div>
</main>
${commonFooter()}
<script>
let tc,sc;
async function refreshData(){try{const r=await fetch('/api/metrics'),d=await r.json();document.getElementById('totalReq').textContent=d.totalRequests||0;const sr=d.totalRequests>0?((d.successRequests/d.totalRequests)*100).toFixed(1)+'%':'0%';document.getElementById('successRate').textContent=sr;document.getElementById('accountCount').textContent=d.accounts||0;const u=Math.floor((Date.now()-d.startTime)/1000);document.getElementById('uptime').textContent=Math.floor(u/3600)+'h '+Math.floor((u%3600)/60)+'m';document.getElementById('streamReq').textContent=d.streamRequests||0;document.getElementById('nonStreamReq').textContent=d.nonStreamRequests||0;document.getElementById('errorReq').textContent=d.errorRequests||0;document.getElementById('totalTokens').textContent=d.totalTokens>1000?(d.totalTokens/1000).toFixed(1)+'k':d.totalTokens||0;const logs=d.requestLog||[];tc.setOption({xAxis:{data:logs.slice(-30).map((_,i)=>i+1)},series:[{data:logs.slice(-30).map(l=>l.duration||0)}]});sc.data.datasets[0].data=[d.successRequests||0,d.errorRequests||0];sc.update();const tb=document.getElementById('reqTable');tb.innerHTML=logs.length?logs.slice(-15).reverse().map(q=>'<tr style="border-bottom:1px solid var(--border)"><td class="py-2 px-2">'+new Date(q.timestamp).toLocaleTimeString()+'</td><td class="py-2 px-2 font-mono">'+q.path+'</td><td class="py-2 px-2 '+(q.status<400?'text-green-400':'text-red-400')+'">'+q.status+'</td><td class="py-2 px-2">'+q.duration+'ms</td><td class="py-2 px-2">'+(q.model||'-')+'</td><td class="py-2 px-2">'+(q.accountId||'-')+'</td></tr>').join(''):'<tr><td colspan="6" class="py-4 text-center" style="color:var(--text-muted)">暂无</td></tr>'}catch(e){console.error(e)}}
tc=echarts.init(document.getElementById('trendChart'));tc.setOption({tooltip:{trigger:'axis'},xAxis:{type:'category',data:[],axisLabel:{color:'#94a3b8'}},yAxis:{type:'value',name:'ms',axisLabel:{color:'#94a3b8'}},series:[{type:'line',smooth:true,data:[],areaStyle:{color:'rgba(99,102,241,0.2)'},lineStyle:{color:'#6366f1'}}]});
sc=new Chart(document.getElementById('statusChart'),{type:'doughnut',data:{labels:['成功','失败'],datasets:[{data:[0,0],backgroundColor:['#22c55e','#ef4444'],borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom',labels:{color:'#94a3b8'}}}}});
refreshData();setInterval(refreshData,5000);window.addEventListener('resize',()=>tc.resize());
</script></body></html>`
}

// ============================================================================
// Swagger 页
// ============================================================================
export function generateOpenAPISpec(version: string): Record<string, unknown> {
  const msgSchema = { type: 'object', required: ['role', 'content'], properties: { role: { type: 'string' }, content: { oneOf: [{ type: 'string' }, { type: 'array' }] } } }
  return {
    openapi: '3.1.0',
    info: { title: 'KiroGate', description: 'OpenAI & Anthropic 兼容的 Kiro API 代理网关', version },
    servers: [{ url: '/', description: '当前服务器' }],
    tags: [{ name: 'Health' }, { name: 'Models' }, { name: 'Chat' }, { name: 'Messages' }, { name: 'Admin' }],
    paths: {
      '/health': { get: { tags: ['Health'], summary: '健康检查', responses: { '200': { description: 'OK' } } } },
      '/v1/models': { get: { tags: ['Models'], summary: '模型列表', security: [{ BearerAuth: [] }], responses: { '200': { description: '模型列表' }, '401': { description: '未授权' } } } },
      '/v1/chat/completions': {
        post: {
          tags: ['Chat'], summary: 'OpenAI 聊天补全', security: [{ BearerAuth: [] }],
          requestBody: { required: true, content: { 'application/json': { schema: { type: 'object', required: ['model', 'messages'], properties: { model: { type: 'string', enum: AVAILABLE_MODELS }, messages: { type: 'array', items: msgSchema }, stream: { type: 'boolean' }, temperature: { type: 'number' }, max_tokens: { type: 'integer' }, tools: { type: 'array' } } } } } },
          responses: { '200': { description: '成功' }, '400': { description: '请求无效' }, '401': { description: '未授权' } }
        }
      },
      '/v1/messages': {
        post: {
          tags: ['Messages'], summary: 'Anthropic Messages API', security: [{ ApiKeyAuth: [] }, { BearerAuth: [] }],
          requestBody: { required: true, content: { 'application/json': { schema: { type: 'object', required: ['model', 'messages', 'max_tokens'], properties: { model: { type: 'string', enum: AVAILABLE_MODELS }, messages: { type: 'array', items: msgSchema }, max_tokens: { type: 'integer' }, system: { type: 'string' }, stream: { type: 'boolean' }, temperature: { type: 'number' }, tools: { type: 'array' } } } } } },
          responses: { '200': { description: '成功' }, '400': { description: '请求无效' }, '401': { description: '未授权' } }
        }
      },
      '/api/accounts': { get: { tags: ['Admin'], summary: '账号列表', security: [{ BearerAuth: [] }] }, post: { tags: ['Admin'], summary: '添加账号', security: [{ BearerAuth: [] }] } },
      '/api/keys': { get: { tags: ['Admin'], summary: 'API Key 列表', security: [{ BearerAuth: [] }] }, post: { tags: ['Admin'], summary: '创建 API Key', security: [{ BearerAuth: [] }] } },
    },
    components: { securitySchemes: { BearerAuth: { type: 'http', scheme: 'bearer', description: 'Bearer ADMIN_PASSWORD 或 Bearer PROXY_API_KEY' }, ApiKeyAuth: { type: 'apiKey', in: 'header', name: 'x-api-key' } } }
  }
}

export function renderSwaggerPage(version: string): string {
  return `<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>KiroGate - Swagger</title><link rel="icon" type="image/svg+xml" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🚀</text></svg>"><link rel="stylesheet" href="${PROXY_BASE}/proxy/unpkg.com/swagger-ui-dist@5/swagger-ui.css"><style>body{margin:0;background:#fafafa}.swagger-ui .topbar{display:none}.hdr{background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;padding:1rem 2rem;display:flex;align-items:center;justify-content:space-between}.hdr h1{margin:0;font-size:1.5rem}.hdr a{color:#fff;text-decoration:none;opacity:.8;margin-left:1.5rem}.hdr a:hover{opacity:1}.badge{background:rgba(255,255,255,.2);padding:.25rem .5rem;border-radius:.25rem;font-size:.8rem;margin-left:1rem}</style></head><body><div class="hdr"><div style="display:flex;align-items:center"><h1>⚡ KiroGate API</h1><span class="badge">v${version}</span></div><nav><a href="/">首页</a><a href="/docs">文档</a><a href="/playground">Playground</a><a href="https://github.com/dext7r/KiroGate" target="_blank">GitHub</a></nav></div><div id="swagger-ui"></div><script src="${PROXY_BASE}/proxy/unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script><script>window.onload=function(){SwaggerUIBundle({url:"/openapi.json",dom_id:"#swagger-ui",deepLinking:true,presets:[SwaggerUIBundle.presets.apis,SwaggerUIBundle.SwaggerUIStandalonePreset],layout:"BaseLayout",docExpansion:"list",filter:true})}</script></body></html>`
}

// ============================================================================
// 账号管理页
// ============================================================================
export function renderAccountsPage(version: string): string {
  return `<!DOCTYPE html><html lang="zh"><head>${commonHead(version)}</head><body>
${commonNav(version)}
<main class="max-w-7xl mx-auto px-4 py-8">
  <div class="flex justify-between items-center mb-6">
    <h1 class="text-3xl font-bold">👥 账号管理</h1>
    <button onclick="showAddForm()" class="btn-primary">➕ 添加账号</button>
  </div>
  <div id="addForm" class="card mb-6 hidden">
    <h2 class="text-xl font-semibold mb-4">添加新账号</h2>
    <div class="grid md:grid-cols-2 gap-4">
      <div><label class="block text-sm mb-1" style="color:var(--text-muted)">Email（可选）</label><input id="accEmail" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2" placeholder="user@example.com"></div>
      <div><label class="block text-sm mb-1" style="color:var(--text-muted)">区域</label><select id="accRegion" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2"><option value="us-east-1">us-east-1</option><option value="eu-west-1">eu-west-1</option><option value="ap-southeast-1">ap-southeast-1</option></select></div>
      <div class="md:col-span-2"><label class="block text-sm mb-1" style="color:var(--text-muted)">Refresh Token <span class="text-red-400">*</span></label><textarea id="accToken" rows="3" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2 font-mono text-sm" placeholder="粘贴 Kiro Refresh Token..."></textarea></div>
      <div><label class="block text-sm mb-1" style="color:var(--text-muted)">Machine ID（可选）</label><input id="accMachine" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2 font-mono text-sm" placeholder="自动生成"></div>
    </div>
    <div class="flex gap-3 mt-4">
      <button onclick="addAccount()" class="btn-primary">✅ 确认添加</button>
      <button onclick="hideAddForm()" class="px-4 py-2 rounded" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)">取消</button>
    </div>
  </div>
  <div id="accountList" class="space-y-3"><div class="text-center py-8" style="color:var(--text-muted)">加载中...</div></div>
</main>
${commonFooter()}
<script>
const AP=localStorage.getItem('adminPwd')||prompt('请输入管理密码:');if(AP)localStorage.setItem('adminPwd',AP);
const AH={'Authorization':'Bearer '+AP,'Content-Type':'application/json'};
function showAddForm(){document.getElementById('addForm').classList.remove('hidden')}
function hideAddForm(){document.getElementById('addForm').classList.add('hidden')}
async function loadAccounts(){try{const r=await fetch('/api/accounts',{headers:AH}),d=await r.json();if(!r.ok){document.getElementById('accountList').innerHTML='<div class="card text-center text-red-400">'+d.error+'</div>';return}const list=document.getElementById('accountList');list.innerHTML=d.accounts.length?d.accounts.map(a=>'<div class="card"><div class="flex flex-wrap justify-between items-start gap-2"><div><div class="font-semibold">'+(a.email||a.id)+'</div><div class="text-xs mt-1" style="color:var(--text-muted)">ID: '+a.id+' · 区域: '+a.region+'</div></div><div class="flex flex-wrap gap-2 items-center"><span class="text-xs px-2 py-1 rounded '+(a.disabled?'bg-red-600':'a.quotaExhausted'?'bg-yellow-600':'bg-green-600')+' text-white">'+(a.disabled?'已禁用':a.quotaExhausted?'配额耗尽':'正常')+'</span><span class="text-xs px-2 py-1 rounded bg-indigo-600 text-white">'+(a.subscriptionType||'unknown')+'</span></div></div><div class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3"><div class="text-center"><div class="text-lg font-bold text-indigo-400">'+a.requestCount+'</div><div class="text-xs" style="color:var(--text-muted)">请求数</div></div><div class="text-center"><div class="text-lg font-bold text-red-400">'+a.errorCount+'</div><div class="text-xs" style="color:var(--text-muted)">错误数</div></div><div class="text-center"><div class="text-lg font-bold text-green-400">'+(a.hasAccessToken?"✓":"✗")+'</div><div class="text-xs" style="color:var(--text-muted)">Token</div></div><div class="text-center"><div class="text-lg font-bold text-purple-400">'+(a.lastUsed?new Date(a.lastUsed).toLocaleTimeString():"-")+'</div><div class="text-xs" style="color:var(--text-muted)">最后使用</div></div></div><div class="flex flex-wrap gap-2 mt-3"><button onclick="refreshAccount(\\''+a.id+'\\')\" class="text-xs px-3 py-1 rounded bg-blue-600 text-white">🔄 刷新Token</button><button onclick="toggleAccount(\\''+a.id+'\\','+!a.disabled+')" class="text-xs px-3 py-1 rounded '+(a.disabled?'bg-green-600':'bg-yellow-600')+' text-white">'+(a.disabled?'启用':'禁用')+'</button><button onclick="deleteAccount(\\''+a.id+'\\')\" class="text-xs px-3 py-1 rounded bg-red-600 text-white">🗑️ 删除</button></div></div>').join(''):'<div class="card text-center" style="color:var(--text-muted)">暂无账号，点击上方按钮添加</div>'}catch(e){console.error(e)}}
async function addAccount(){const t=document.getElementById('accToken').value.trim();if(!t){alert('请输入 Refresh Token');return}const b={refreshToken:t,email:document.getElementById('accEmail').value.trim(),region:document.getElementById('accRegion').value};const m=document.getElementById('accMachine').value.trim();if(m)b.machineId=m;try{const r=await fetch('/api/accounts',{method:'POST',headers:AH,body:JSON.stringify(b)}),d=await r.json();if(r.ok){hideAddForm();document.getElementById('accToken').value='';document.getElementById('accEmail').value='';loadAccounts();alert('添加成功! Token刷新: '+d.refreshed)}else{alert('错误: '+d.error)}}catch(e){alert('请求失败: '+e)}}
async function refreshAccount(id){try{const r=await fetch('/api/accounts/'+encodeURIComponent(id)+'/refresh',{method:'POST',headers:AH}),d=await r.json();alert(d.success?'Token 刷新成功':'刷新失败');loadAccounts()}catch(e){alert('请求失败: '+e)}}
async function toggleAccount(id,disabled){try{await fetch('/api/accounts/'+encodeURIComponent(id),{method:'PUT',headers:AH,body:JSON.stringify({disabled})});loadAccounts()}catch(e){alert('请求失败: '+e)}}
async function deleteAccount(id){if(!confirm('确认删除账号 '+id+'？'))return;try{await fetch('/api/accounts/'+encodeURIComponent(id),{method:'DELETE',headers:AH});loadAccounts()}catch(e){alert('请求失败: '+e)}}
loadAccounts();
</script></body></html>`
}

// ============================================================================
// API Key 管理页
// ============================================================================
export function renderApiKeysPage(version: string): string {
  return `<!DOCTYPE html><html lang="zh"><head>${commonHead(version)}</head><body>
${commonNav(version)}
<main class="max-w-7xl mx-auto px-4 py-8">
  <div class="flex justify-between items-center mb-6">
    <h1 class="text-3xl font-bold">🔑 API Key 管理</h1>
    <button onclick="showCreateForm()" class="btn-primary">➕ 创建 Key</button>
  </div>
  <div id="createForm" class="card mb-6 hidden">
    <h2 class="text-xl font-semibold mb-4">创建新 API Key</h2>
    <div class="grid md:grid-cols-2 gap-4">
      <div><label class="block text-sm mb-1" style="color:var(--text-muted)">名称</label><input id="keyName" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2" placeholder="My API Key"></div>
      <div><label class="block text-sm mb-1" style="color:var(--text-muted)">额度限制（可选）</label><input id="keyLimit" type="number" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="w-full rounded px-3 py-2" placeholder="0 = 无限制"></div>
    </div>
    <div class="flex gap-3 mt-4">
      <button onclick="createKey()" class="btn-primary">✅ 创建</button>
      <button onclick="hideCreateForm()" class="px-4 py-2 rounded" style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)">取消</button>
    </div>
  </div>
  <div id="newKeyDisplay" class="card mb-6 hidden" style="border-color:#22c55e">
    <h2 class="text-lg font-semibold text-green-400 mb-2">🎉 Key 创建成功</h2>
    <p class="text-sm mb-2" style="color:var(--text-muted)">请立即复制，此 Key 仅显示一次：</p>
    <div class="flex gap-2"><input id="newKeyValue" readonly style="background:var(--bg-input);border:1px solid var(--border);color:var(--text)" class="flex-1 rounded px-3 py-2 font-mono text-sm"><button onclick="copyKey()" class="btn-primary">📋 复制</button></div>
  </div>
  <div id="keyList" class="space-y-3"><div class="text-center py-8" style="color:var(--text-muted)">加载中...</div></div>
</main>
${commonFooter()}
<script>
const AP=localStorage.getItem('adminPwd')||prompt('请输入管理密码:');if(AP)localStorage.setItem('adminPwd',AP);
const AH={'Authorization':'Bearer '+AP,'Content-Type':'application/json'};
function showCreateForm(){document.getElementById('createForm').classList.remove('hidden')}
function hideCreateForm(){document.getElementById('createForm').classList.add('hidden')}
function copyKey(){const i=document.getElementById('newKeyValue');i.select();navigator.clipboard.writeText(i.value);alert('已复制!')}
async function loadKeys(){try{const r=await fetch('/api/keys',{headers:AH}),d=await r.json();if(!r.ok){document.getElementById('keyList').innerHTML='<div class="card text-center text-red-400">'+d.error+'</div>';return}const list=document.getElementById('keyList');list.innerHTML=d.keys.length?d.keys.map(k=>'<div class="card"><div class="flex flex-wrap justify-between items-start gap-2"><div><div class="font-semibold">'+k.name+'</div><div class="text-xs mt-1 font-mono" style="color:var(--text-muted)">'+k.key+'</div></div><div class="flex gap-2 items-center"><span class="text-xs px-2 py-1 rounded '+(k.enabled?'bg-green-600':'bg-red-600')+' text-white">'+(k.enabled?'启用':'禁用')+'</span></div></div><div class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3"><div class="text-center"><div class="text-lg font-bold text-indigo-400">'+(k.stats?.totalRequests||0)+'</div><div class="text-xs" style="color:var(--text-muted)">请求数</div></div><div class="text-center"><div class="text-lg font-bold text-yellow-400">'+(k.stats?.totalCredits||0)+'</div><div class="text-xs" style="color:var(--text-muted)">已用额度</div></div><div class="text-center"><div class="text-lg font-bold text-purple-400">'+(k.creditLimit||'∞')+'</div><div class="text-xs" style="color:var(--text-muted)">额度上限</div></div><div class="text-center"><div class="text-lg font-bold text-cyan-400">'+(k.createdAt?new Date(k.createdAt).toLocaleDateString():'-')+'</div><div class="text-xs" style="color:var(--text-muted)">创建时间</div></div></div><div class="flex flex-wrap gap-2 mt-3"><button onclick="toggleKey(\\''+k.id+'\\','+!k.enabled+')" class="text-xs px-3 py-1 rounded '+(k.enabled?'bg-yellow-600':'bg-green-600')+' text-white">'+(k.enabled?'禁用':'启用')+'</button><button onclick="deleteKey(\\''+k.id+'\\')\" class="text-xs px-3 py-1 rounded bg-red-600 text-white">🗑️ 删除</button></div></div>').join(''):'<div class="card text-center" style="color:var(--text-muted)">暂无 API Key，点击上方按钮创建</div>'}catch(e){console.error(e)}}
async function createKey(){const n=document.getElementById('keyName').value.trim()||'Unnamed Key';const l=parseInt(document.getElementById('keyLimit').value)||undefined;try{const r=await fetch('/api/keys',{method:'POST',headers:AH,body:JSON.stringify({name:n,creditLimit:l})}),d=await r.json();if(r.ok){hideCreateForm();document.getElementById('newKeyDisplay').classList.remove('hidden');document.getElementById('newKeyValue').value=d.key;document.getElementById('keyName').value='';loadKeys()}else{alert('错误: '+d.error)}}catch(e){alert('请求失败: '+e)}}
async function toggleKey(id,enabled){try{await fetch('/api/keys/'+encodeURIComponent(id),{method:'PUT',headers:AH,body:JSON.stringify({enabled})});loadKeys()}catch(e){alert('请求失败: '+e)}}
async function deleteKey(id){if(!confirm('确认删除此 Key？'))return;try{await fetch('/api/keys/'+encodeURIComponent(id),{method:'DELETE',headers:AH});loadKeys()}catch(e){alert('请求失败: '+e)}}
loadKeys();
</script></body></html>`
}
