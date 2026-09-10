/* 磷文献英语 - 共享前端逻辑。
   以相对路径工作：页面可能挂在本机根路径(/)或服务器子路径(/english/)下，
   这里取当前页目录作为 BASE，内部链接与 API 都基于它拼接。 */
const B = location.pathname.replace(/\/[^/]*$/, '/');
const H = (p) => p.replace(/^\//, '');          // 去掉前导斜杠
const href = (p) => B + H(p);

/* App 识别：Android 壳启动地址带 ?appc=，跨页记住，供“应用内下载”判断 */
try {
  const m = location.search.match(/[?&]appc=(\d+)/);
  if (m) sessionStorage.setItem('se_app', m[1]);
} catch (e) {}
let isInApp = false;
try { isInApp = !!sessionStorage.getItem('se_app'); } catch (e) {}

async function api(path, opts = {}) {
  const o = { headers: { 'Content-Type': 'application/json' }, ...opts };
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const r = await fetch(href(path), o);
  const j = await r.json().catch(() => ({}));
  if (r.status === 401 && !location.pathname.endsWith('login.html')) {
    location.replace(href('login.html'));
    throw Object.assign(new Error('未登录'), { json: j, status: 401 });
  }
  if (!r.ok) throw Object.assign(new Error((j.error || r.status) + ''), { json: j, status: r.status });
  return j;
}

function navBar(page) {
  const items = [
    ['index.html', '背单词'],
    ['sentence.html', '翻译'],
    ['words.html', '词表'],
    ['papers.html', '文献'],
    ['progress.html', '进度'],
    ['settings.html', '设置'],
  ];
  const html = items.map(([h, t]) =>
    `<a href="${href(h)}" class="${h === page ? 'on' : ''}">${t}</a>`).join('');
  document.querySelectorAll('.nav').forEach(el => { el.innerHTML = html; });
}

/** 高亮例句里的目标词（lemma，忽略大小写，词边界） */
function highlight(word, sentence) {
  if (!sentence) return '';
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const re = new RegExp(`\\b(${esc(word)})\\b`, 'ig');
  return sentence.replace(re, '<mark>$1</mark>');
}

/** 播放单词发音：type=2 美音 / type=1 英音（有道词典语音，免 key） */
function playWord(word, type = 2) {
  const w = encodeURIComponent(word);
  const a = new Audio(`https://dict.youdao.com/dictvoice?audio=${w}&type=${type}`);
  a.onerror = () => { // 兜底：Google 语音
    new Audio(`https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&tl=en&q=${w}`).play().catch(() => {});
  };
  a.play().catch(() => {});
}

function tierPill(t) {
  const map = { D0: '领域核心', D1: '学术高频', D2: '文献语境' };
  return `<span class="pill ${(t || '').toLowerCase()}">${map[t] || t}</span>`;
}

function fmtDate(d) {
  const wd = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  const [y, m, day] = d.split('-');
  return `${y}年${+m}月${+day}日 ${wd[new Date(d).getDay()]}`;
}

function bar(n, max) {
  const pct = max ? Math.min(100, Math.round(n / max * 100)) : 0;
  return `<div class="bar"><i style="width:${pct}%"></i></div>`;
}
