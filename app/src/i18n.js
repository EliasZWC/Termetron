// ═══════════════════════════════════════════════════════════════════════
// 界面语言（i18n）
// ═══════════════════════════════════════════════════════════════════════
//
// 设计取舍：
//   · 用**属性标记**（data-i18n）而不是给每个元素起 id —— 文案变了只需改表
//   · `i18n(key)` 用于 JS 里拼字符串；`applyI18n(root)` 用于批量刷 DOM
//   · 缺 key 时回退英文并打 console.warn，**不抛错** —— 界面上留个
//     英文单词，比整个页面白屏好
//   · 服务端发来的中立标记（[termetron:xxx]）也在这里本地化，
//     因为服务端读不到用户的语言设置
//
// ⚠️ 本模块**不 import settings**（否则与 main.js 形成循环依赖）。
//    当前语言由 main.js 通过 `setLang()` 注入。
// ⚠️ 本地终端（termetron 命令）的输出一律英文，**不进这张表**。

const I18N = {
  en: {
    // 页面标题（全大写由 CSS 的 text-transform 处理）
    'page.sessions': 'Session',
    'page.settings': 'Setting',
    'page.terminal': 'Terminal',

    // 底部导航
    'nav.sessions': 'Session',
    'nav.settings': 'Setting',

    // 设置分组
    'group.general': 'General',
    'group.connection': 'Connection',
    'group.about': 'About',

    // 设置项
    'setting.language': 'Language',
    'setting.theme': 'Theme',
    'setting.fontSize': 'Font Size',
    'setting.tsIp': 'Tailscale IP',
    'setting.port': 'Port',
    'setting.version': 'Version',
    'setting.contact': 'Contact',

    // 值
    'value.notSet': 'not set',

    // 主题名
    'theme.dark': 'Dark',
    'theme.light': 'Light',

    // 空状态
    // ⚠️ 名词统一用**单数**（Session 而不是 Sessions）：底部 tab 是分类名，
    //    标题栏是页面名，两者都取单数；复述"多个"的场景交给列表本身表达。
    // ⚠️ 大小写：这是**短语**，按 Title Case —— 实词首字母均大写，
    //    不是句子所以不能只大写首字母（写成 "No session" 是错的）。
    'empty.noSessions': 'No Session',
    'empty.noSessionsHint': 'Tap + in the top right to start one.',
    'empty.notConnected': 'Not connected',
    'empty.notConnectedHint':
      "Set your computer's Tailscale IP in Setting to start.",
    'empty.unreachable': 'Cannot reach computer',
    'empty.unreachableHint':
      'Check that Termetron is running on {ip}:{port} and Tailscale is connected.',

    // 会话状态
    'session.running': 'running',
    'session.idle': 'idle',
    'session.done': 'done',

    // 终端
    'terminal.placeholder': 'Run a command',
    'terminal.interrupt': 'Interrupt (Ctrl+C)',
    'term.copied': 'Output copied',
    'term.copyEmpty': 'Nothing to copy yet',
    'term.copyFailed': 'Copy failed',

    // 通用按钮
    'action.ok': 'OK',
    'action.save': 'SAVE',
    'action.cancel': 'CANCEL',
    'action.create': 'CREATE',

    // 会话
    'session.new': 'New Session',
    'session.nameHint': 'Name for the new terminal session',
    'session.namePlaceholder': 'Session name',
    'session.errNoConnection': "Set your computer's Tailscale IP in Setting first.",
    'session.errCreate': 'Could not create session:\n{err}',
    'session.errSend': 'Send failed:\n{err}',

    // 连接表单
    'conn.title': 'Connection',
    'conn.ip': 'Tailscale IP',
    'conn.port': 'Port',
    'conn.test': 'TEST CONNECTION',
    'conn.testing': 'Testing…',
    'conn.ok': 'Connected · {n} session(s)',
    'conn.fail': 'Cannot reach the server.\n{err}',
    'conn.timeout': 'Timed out after 8s',
    'conn.invalid': 'Enter a valid IP and a port between 1 and 65535.',

    // 字号
    'font.title': 'Font Size',
    'font.range': '{min} – {max} px',
    'font.aria': 'Font size',

    // 端口校验
    'port.invalid': 'Port must be 1–65535',
    'tsip.prompt': "Your computer's Tailscale IP, e.g. 100.101.102.103",
    'port.prompt': 'Port Termetron listens on (default 8899)',

    // 服务端中立标记
    'server.restored': '↑ Output above is from the previous session (restored)',

    // 无障碍
    'aria.back': 'Back',
    'aria.connection': 'Connection',
    'aria.newSession': 'New session',
    'aria.interrupt': 'Interrupt (Ctrl+C)',
    'aria.copyOutput': 'Copy output',
    'aria.decreaseFont': 'Decrease font size',
    'aria.increaseFont': 'Increase font size',
  },

  zh: {
    'page.sessions': '会话',
    'page.settings': '设置',
    'page.terminal': '终端',

    'nav.sessions': '会话',
    'nav.settings': '设置',

    'group.general': '通用',
    'group.connection': '连接',
    'group.about': '关于',

    'setting.language': '语言',
    'setting.theme': '主题',
    'setting.fontSize': '字号',
    'setting.tsIp': 'Tailscale IP',
    'setting.port': '端口',
    'setting.version': '版本',
    'setting.contact': '联系方式',

    'value.notSet': '未设置',

    'theme.dark': '深色',
    'theme.light': '浅色',

    'empty.noSessions': '暂无会话',
    'empty.noSessionsHint': '点右上角的 + 新建一个会话。',
    'empty.notConnected': '未连接',
    'empty.notConnectedHint': '在设置里填入电脑的 Tailscale IP 以开始使用。',
    'empty.unreachable': '无法连接到电脑',
    'empty.unreachableHint':
      '请确认 Termetron 正运行在 {ip}:{port}，且 Tailscale 已连接。',

    // 会话状态
    'session.running': '运行中',
    'session.idle': '空闲',
    'session.done': '已结束',

    'terminal.placeholder': '输入命令',
    'terminal.interrupt': '中断 (Ctrl+C)',
    'term.copied': '已复制输出',
    'term.copyEmpty': '暂无内容可复制',
    'term.copyFailed': '复制失败',

    'action.ok': '确定',
    'action.save': '保存',
    'action.cancel': '取消',
    'action.create': '创建',

    'session.new': '新建会话',
    'session.nameHint': '新终端会话的名称',
    'session.namePlaceholder': '会话名称',
    'session.errNoConnection': '请先在设置里填入电脑的 Tailscale IP。',
    'session.errCreate': '无法创建会话：\n{err}',
    'session.errSend': '发送失败：\n{err}',

    'conn.title': '连接',
    'conn.ip': 'Tailscale IP',
    'conn.port': '端口',
    'conn.test': '测试连接',
    'conn.testing': '正在测试…',
    'conn.ok': '已连接 · {n} 个会话',
    'conn.fail': '无法连接到服务器。\n{err}',
    'conn.timeout': '8 秒内未响应',
    'conn.invalid': '请输入有效的 IP 和 1–65535 之间的端口。',

    'font.title': '字号',
    'font.range': '{min} – {max} px',
    'font.aria': '字号',

    'port.invalid': '端口必须在 1–65535 之间',
    'tsip.prompt': '电脑的 Tailscale IP，例如 100.101.102.103',
    'port.prompt': 'Termetron 监听的端口（默认 8899）',

    'server.restored': '↑ 以上为上次会话的输出（已恢复）',

    'aria.back': '返回',
    'aria.connection': '连接',
    'aria.newSession': '新建会话',
    'aria.interrupt': '中断 (Ctrl+C)',
    'aria.copyOutput': '复制输出',
    'aria.decreaseFont': '减小字号',
    'aria.increaseFont': '增大字号',
  },
};

// 当前语言。由 main.js 的 setLang() 注入，避免循环依赖。
let LANG = 'en';

/** 设置当前语言（'en' | 'zh'）。 */
export function setLang(code) {
  LANG = code === 'zh' ? 'zh' : 'en';
}

/** 当前生效的语言码。 */
export function getLang() {
  return LANG;
}

/**
 * 取翻译。缺 key 时回退英文；再缺就返回 key 本身（便于发现遗漏）。
 * 支持 `{name}` 占位符替换。
 */
export function i18n(key, vars) {
  let s = I18N[LANG]?.[key] ?? I18N.en[key];
  if (s === undefined) {
    console.warn(`[i18n] missing key: ${key}`);
    return key;
  }
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      s = s.split(`{${k}}`).join(String(v));
    }
  }
  return s;
}

/**
 * 批量刷新 DOM 里的文案。
 *   data-i18n        → textContent
 *   data-i18n-aria   → aria-label
 *   data-i18n-ph     → placeholder
 * 不传 root 时刷整个文档。
 */
export function applyI18n(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((el) => {
    el.textContent = i18n(el.dataset.i18n);
  });
  root.querySelectorAll('[data-i18n-aria]').forEach((el) => {
    el.setAttribute('aria-label', i18n(el.dataset.i18nAria));
  });
  root.querySelectorAll('[data-i18n-ph]').forEach((el) => {
    el.placeholder = i18n(el.dataset.i18nPh);
  });
}

/**
 * 把服务端发来的中立标记（如 `[termetron:restored]`）本地化。
 * 服务端读不到用户语言设置，只能这样分工。
 */
export function localizeServerLines(lines) {
  return lines.map((ln) => {
    const m = /^\[termetron:([a-z-]+)\]$/.exec(ln.trim());
    if (!m) return ln;
    const key = `server.${m[1]}`;
    const hit = I18N[LANG]?.[key] ?? I18N.en[key];
    return hit ?? ln;
  });
}
