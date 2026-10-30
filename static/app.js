const $ = (id) => document.getElementById(id);
const state = { mode: 'login', csrf: '', registration: true, busy: false, characterRequest: 0, account: null, view: 'characters', monitorRequest: 0, monitorBusy: false, rows: [], page: 1, settingsRequest: 0, settingsBusy: false, keyConfigured: false, settingsLoaded: false };

async function api(path, body) {
  const options = { credentials: 'same-origin', headers: {}, signal: AbortSignal.timeout(15000) };
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = { 'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf };
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new Error('无法连接服务，请检查网络后重试。');
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error('服务返回异常，请稍后重试。');
  }
  if (!response.ok) {
    const error = new Error(data.error || '操作失败，请稍后重试。');
    error.status = response.status;
    throw error;
  }
  if (data.csrf_token) state.csrf = data.csrf_token;
  return data;
}

function message(text, neutral = false) {
  $('form-message').textContent = text;
  $('form-message').classList.toggle('neutral', neutral);
}

function setBusy(busy) {
  state.busy = busy;
  $('account-form').setAttribute('aria-busy', String(busy));
  for (const id of ['username', 'password', 'confirm-password', 'submit-button', 'toggle-password', 'login-tab']) {
    $(id).disabled = busy;
  }
  $('register-tab').disabled = busy || !state.registration;
  $('submit-label').textContent = busy ? '正在处理…' : state.mode === 'login' ? '登录账号' : '创建账号';
}

function switchMode(mode) {
  if (state.busy || (mode === 'register' && !state.registration)) return;
  state.mode = mode;
  const register = mode === 'register';
  $('auth-title').textContent = register ? '开启你的冒险' : '欢迎回来';
  $('auth-description').textContent = register ? '创建游戏账号，踏上艾泽拉斯的旅程。' : '登录游戏账号，查看你的艾泽拉斯角色。';
  for (const tab of ['login', 'register']) {
    const selected = tab === mode;
    $(`${tab}-tab`).setAttribute('aria-selected', String(selected));
    $(`${tab}-tab`).tabIndex = selected ? 0 : -1;
  }
  $('form-panel').setAttribute('aria-labelledby', `${mode}-tab`);
  $('username-hint').textContent = register ? '3–17 位英文字母、数字或下划线。' : '与游戏客户端使用同一个账号。';
  $('username').minLength = register ? 3 : 1;
  $('password').minLength = register ? 6 : 1;
  $('password').autocomplete = register ? 'new-password' : 'current-password';
  $('password').placeholder = register ? '设置 6–16 位密码' : '输入密码';
  $('password').type = 'password';
  $('toggle-password').textContent = '显示';
  $('toggle-password').setAttribute('aria-label', '显示密码');
  $('toggle-password').setAttribute('aria-pressed', 'false');
  $('password').value = '';
  $('confirm-password').value = '';
  $('confirm-password').required = register;
  $('confirm-field').hidden = !register;
  $('submit-label').textContent = register ? '创建账号' : '登录账号';
  $('account-note').textContent = register ? '注册成功即可登录游戏，密码不区分大小写。' : '网站与游戏共用账号，密码不区分大小写。';
  message('');
}

function showAuth() {
  state.account = null;
  state.monitorRequest += 1;
  state.monitorBusy = false;
  state.rows = [];
  $('monitor-rows').replaceChildren();
  $('nav-bots').hidden = true;
  $('nav-players').hidden = true;
  $('nav-bot-settings').hidden = true;
  resetBotSettings();
  state.characterRequest += 1;
  $('auth-view').hidden = false;
  $('dashboard').hidden = true;
  $('logout').hidden = true;
  $('character-list').replaceChildren();
  $('account-name').textContent = '';
  $('account-form').reset();
  setBusy(false);
  switchMode('login');
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function characterCard(character) {
  const card = element('article', `character-card class-${character.class_id}`);
  const top = element('div', 'character-top');
  const emblem = element('div', 'class-emblem', character.class_name.slice(0, 1));
  emblem.setAttribute('aria-hidden', 'true');
  const identity = element('div');
  identity.append(element('h3', '', character.name));
  identity.append(element('p', 'character-race', `${character.race_name} · ${character.faction}`));
  top.append(emblem, identity);
  const bottom = element('div', 'character-bottom');
  const level = element('span', 'level', String(character.level));
  level.append(element('small', '', '级'));
  bottom.append(element('span', 'class-badge', character.class_name), level);
  const status = element('div', `character-status${character.online ? ' is-online' : ''}`);
  status.append(element('span', 'status-dot'), element('span', '', character.online ? '游戏在线' : '游戏离线'));
  card.append(top, bottom, status);
  return card;
}

async function loadCharacters() {
  const requestId = ++state.characterRequest;
  $('refresh-characters').disabled = true;
  $('dashboard-message').classList.remove('error');
  $('dashboard-message').textContent = '正在读取角色档案…';
  $('empty-state').hidden = true;
  try {
    const data = await api('/api/characters');
    if (requestId !== state.characterRequest) return;
    updateAccount(data.account);
    $('realm-name').textContent = data.realm;
    $('character-list').replaceChildren(...data.characters.map(characterCard));
    $('total-characters').textContent = String(data.characters.length);
    const highestLevel = data.characters.length ? Math.max(...data.characters.map(c => c.level)) : null;
    $('highest-level').textContent = highestLevel === null ? '—' : String(highestLevel);
    $('online-characters').textContent = String(data.characters.filter(c => c.online).length);
    $('empty-state').hidden = data.characters.length > 0;
    $('dashboard-message').textContent = `已更新 · ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`;
  } catch (error) {
    if (requestId !== state.characterRequest) return;
    if (error.status === 401 || error.status === 403) {
      showAuth();
      await reconnectSession();
      message(error.message);
    } else {
      $('dashboard-message').textContent = `${error.message} 点击“刷新角色”重试。`;
      $('dashboard-message').classList.add('error');
    }
  } finally {
    if (requestId === state.characterRequest) $('refresh-characters').disabled = false;
  }
}

function showDashboard(account) {
  $('auth-view').hidden = true;
  $('dashboard').hidden = false;
  $('logout').hidden = false;
  updateAccount(account);
  $('account-form').reset();
  for (const id of ['total-characters', 'highest-level', 'online-characters']) $(id).textContent = '—';
  loadCharacters();
  navigate();
}

async function reconnectSession() {
  setBusy(true);
  message('正在连接服务…', true);
  $('retry-connection').hidden = true;
  try {
    const data = await api('/api/session');
    state.registration = data.registration_enabled;
    $('register-tab').hidden = !state.registration;
    setBusy(false);
    message('');
    if (data.authenticated) showDashboard(data.account);
    return true;
  } catch (error) {
    message(error.message);
    $('retry-connection').hidden = false;
    return false;
  }
}

$('login-tab').addEventListener('click', () => switchMode('login'));
$('register-tab').addEventListener('click', () => switchMode('register'));
document.querySelector('.tabs').addEventListener('keydown', (event) => {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) || state.busy || !state.registration) return;
  event.preventDefault();
  const mode = event.key === 'Home' ? 'login' : event.key === 'End' ? 'register' :
    state.mode === 'login' ? 'register' : 'login';
  switchMode(mode);
  $(`${mode}-tab`).focus();
});
$('toggle-password').addEventListener('click', () => {
  const visible = $('password').type === 'password';
  $('password').type = visible ? 'text' : 'password';
  $('toggle-password').textContent = visible ? '隐藏' : '显示';
  $('toggle-password').setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
  $('toggle-password').setAttribute('aria-pressed', String(visible));
});
$('retry-connection').addEventListener('click', reconnectSession);
$('refresh-characters').addEventListener('click', loadCharacters);
$('account-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.busy) return;
  const body = { username: $('username').value.trim(), password: $('password').value };
  if (state.mode === 'register') {
    body.confirm_password = $('confirm-password').value;
    if (body.password !== body.confirm_password) {
      message('两次输入的密码不一致。');
      $('confirm-password').focus();
      return;
    }
  }
  setBusy(true);
  message('');
  try {
    const data = await api(`/api/${state.mode}`, body);
    showDashboard(data.account);
  } catch (error) {
    if (error.status === 403) await reconnectSession();
    message(error.message);
  } finally {
    setBusy(false);
  }
});
$('logout').addEventListener('click', async () => {
  $('logout').disabled = true;
  try {
    await api('/api/logout', {});
    showAuth();
    message('已安全退出账号。', true);
  } catch (error) {
    $('dashboard-message').textContent = error.message;
    $('dashboard-message').classList.add('error');
  } finally {
    $('logout').disabled = false;
  }
});

function updateAccount(account) {
  state.account = account;
  $('account-name').textContent = account.username;
  const canMonitor = account.gmlevel >= 1;
  const canConfigure = account.gmlevel >= 3;
  $('nav-bots').hidden = !canMonitor;
  $('nav-players').hidden = !canMonitor;
  $('nav-bot-settings').hidden = !canConfigure;
  $('account-role').textContent = account.gmlevel ? `GM ${account.gmlevel}` : '冒险者';
  if ((!canMonitor && ['bots', 'players'].includes(state.view)) ||
      (!canConfigure && state.view === 'bot-settings')) {
    state.rows = [];
    $('monitor-rows').replaceChildren();
    location.hash = 'characters';
    navigate();
  }
}

function navigate() {
  if (!state.account) return;
  let view = location.hash.slice(1);
  if (view === 'bot-settings') {
    if (state.account.gmlevel < 3) view = 'characters';
  } else if (!['bots', 'players'].includes(view) || state.account.gmlevel < 1) view = 'characters';
  resetBotSettings();
  state.view = view;
  state.monitorRequest += 1;
  state.monitorBusy = false;
  state.rows = [];
  state.page = 1;
  $('monitor-rows').replaceChildren();
  $('characters-page').hidden = view !== 'characters';
  $('monitor-page').hidden = !['bots', 'players'].includes(view);
  $('bot-settings-page').hidden = view !== 'bot-settings';
  for (const name of ['characters', 'bots', 'players', 'bot-settings']) {
    if (view === name) $(`nav-${name}`).setAttribute('aria-current', 'page');
    else $(`nav-${name}`).removeAttribute('aria-current');
  }
  if (view === 'characters') return;
  if (view === 'bot-settings') {
    loadBotSettings();
    return;
  }
  const bots = view === 'bots';
  $('monitor-title').replaceChildren(document.createTextNode(bots ? '机器人状态' : '世界玩家'), element('span', '', '.'));
  $('monitor-eyebrow').textContent = bots ? 'PLAYERBOTS OBSERVATORY' : 'PLAYERS IN AZEROTH';
  $('monitor-description').textContent = bots ? '查看机器人的当前活动、位置，以及与真人玩家的组队情况。' : '查看当前在线的真人玩家，包含使用自己角色 AI 的玩家。';
  $('monitor-caption').textContent = bots ? 'Playerbots 状态列表' : '当前在线玩家';
  $('monitor-stat-label').textContent = bots ? '战斗中' : '联盟玩家';
  $('monitor-third-label').textContent = bots ? '与玩家组队' : '部落玩家';
  $('status-heading').textContent = bots ? '活动状态' : '在线状态';
  $('group-heading').hidden = !bots;
  for (const id of ['activity-filter', 'group-filter', 'online-filter']) $(id).hidden = !bots;
  for (const id of ['monitor-search', 'monitor-faction', 'monitor-activity', 'monitor-group']) $(id).value = '';
  $('monitor-presence').value = 'online';
  for (const id of ['monitor-online', 'monitor-active', 'monitor-grouped']) $(id).textContent = '—';
  $('monitor-empty').hidden = true;
  $('monitor-count').textContent = '';
  $('telemetry-note').textContent = '正在连接世界状态…';
  loadMonitor();
}

function monitorRow(row) {
  const bots = state.view === 'bots';
  const tr = element('tr', `class-${row.class_id}`);
  const name = element('td');
  name.append(element('strong', '', row.name), element('small', '', row.online ? '在线' : '离线'));
  const faction = element('td', row.faction === '联盟' ? 'faction-alliance' : 'faction-horde', row.faction);
  const profession = element('td', '', `${row.class_name} · ${row.level} 级`);
  const place = element('td', '', row.location);
  place.append(element('small', '', `${row.map_name} · ${row.x}, ${row.y}, ${row.z}`));
  place.append(element('small', '', row.location_source === 'live' ? '实时位置' : '最后保存位置'));
  const status = element('td');
  status.append(element('span', `activity-badge activity-${row.activity}`, bots ? row.activity_label : '游戏在线'));
  tr.append(name, faction, profession, place, status);
  if (bots) {
    const group = element('td');
    if (row.grouped_with_player === true) {
      group.append(element('span', 'group-confirmed', '是 · 与玩家组队'), element('small', '', row.player_teammates.join('、')));
    } else if (row.grouped_with_player === false) {
      group.append(element('span', '', row.group_id ? '否 · 机器人队伍' : '否 · 未组队'));
    } else {
      group.append(element('span', 'muted', row.online ? '尚未确认' : '—'));
      if (row.group_source === 'saved') group.append(element('small', '', '仅有历史队伍记录'));
    }
    tr.append(group);
  }
  return tr;
}

function renderMonitor() {
  const search = $('monitor-search').value.trim().toLocaleLowerCase();
  const faction = $('monitor-faction').value;
  const activity = $('monitor-activity').value;
  const group = $('monitor-group').value;
  const presence = $('monitor-presence').value;
  const bots = state.view === 'bots';
  const rows = state.rows.filter(row => {
    if (search && !`${row.name} ${row.class_name} ${row.location} ${row.map_name}`.toLocaleLowerCase().includes(search)) return false;
    if (faction && row.faction !== faction) return false;
    if (!bots) return true;
    if (activity && row.activity !== activity) return false;
    if (group === 'yes' && row.grouped_with_player !== true) return false;
    if (group === 'no' && row.grouped_with_player !== false) return false;
    if (group === 'unknown' && row.grouped_with_player !== null) return false;
    return !presence || row.online === (presence === 'online');
  });
  const pages = Math.max(1, Math.ceil(rows.length / 50));
  state.page = Math.min(pages, Math.max(1, state.page));
  const offset = (state.page - 1) * 50;
  $('monitor-rows').replaceChildren(...rows.slice(offset, offset + 50).map(monitorRow));
  $('monitor-empty').hidden = rows.length > 0;
  $('monitor-empty-title').textContent = state.rows.length ? '没有符合条件的角色' : bots ? '暂无机器人' : '暂无在线玩家';
  $('monitor-empty-description').textContent = state.rows.length ? '调整筛选条件，或搜索其他名字和地区。' : '角色上线后，这里会自动更新。';
  $('monitor-count').textContent = `共 ${rows.length} 条${rows.length !== state.rows.length ? ` · 全部 ${state.rows.length} 条` : ''}`;
  $('monitor-page-number').textContent = `${state.page} / ${pages}`;
  $('monitor-prev').disabled = state.page <= 1;
  $('monitor-next').disabled = state.page >= pages;
}

async function loadMonitor() {
  if (!state.account || !['bots', 'players'].includes(state.view) || state.monitorBusy) return;
  const view = state.view;
  const requestId = ++state.monitorRequest;
  state.monitorBusy = true;
  $('refresh-monitor').disabled = true;
  $('monitor-message').classList.remove('error');
  $('monitor-message').textContent = '正在读取世界状态…';
  try {
    const data = await api(`/api/monitor/${view}`);
    if (requestId !== state.monitorRequest) return;
    updateAccount(data.account);
    if (view !== state.view) return;
    state.rows = data[view];
    $('monitor-online').textContent = state.rows.filter(row => row.online).length;
    $('monitor-active').textContent = state.rows.filter(row => view === 'bots' ? row.activity === 'combat' : row.faction === '联盟').length;
    $('monitor-grouped').textContent = view === 'bots' && !data.telemetry.available ? '—' :
      state.rows.filter(row => view === 'bots' ? row.grouped_with_player === true : row.faction === '部落').length;
    $('monitor-message').textContent = `已更新 · ${new Date(data.queried_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })}`;
    const live = data.telemetry.available;
    const partial = state.rows.some(row => row.online && !row.sampled_at);
    $('telemetry-note').classList.toggle('is-live', live);
    $('telemetry-note').textContent = live ?
      `实时采集已连接 · ${new Date(data.telemetry.updated_at * 1000).toLocaleTimeString('zh-CN', { hour12: false })}。${partial ? '部分角色尚无实时样本，显示最后保存位置。' : '位置、活动与组队信息来自游戏服务。'}` :
      `${data.telemetry.reason}。当前仅显示数据库在线状态和最后保存位置；活动及实时组队状态暂不可确认。`;
    if (view === 'bots' && !live) $('monitor-active').textContent = '—';
    renderMonitor();
  } catch (error) {
    if (requestId !== state.monitorRequest) return;
    state.rows = [];
    $('monitor-rows').replaceChildren();
    $('monitor-empty').hidden = true;
    $('monitor-count').textContent = '';
    for (const id of ['monitor-online', 'monitor-active', 'monitor-grouped']) $(id).textContent = '—';
    if (error.status === 401) {
      showAuth();
      await reconnectSession();
      message(error.message);
    } else if (error.status === 403) {
      location.hash = 'characters';
      navigate();
      await loadCharacters();
      $('dashboard-message').textContent = error.message;
      $('dashboard-message').classList.add('error');
    } else {
      $('monitor-message').textContent = `${error.message} 可点击“刷新状态”重试。`;
      $('monitor-message').classList.add('error');
      $('telemetry-note').textContent = '本次更新失败，尚未确认当前世界状态。';
      $('telemetry-note').classList.remove('is-live');
    }
  } finally {
    if (requestId === state.monitorRequest) {
      state.monitorBusy = false;
      $('refresh-monitor').disabled = false;
    }
  }
}

function setSettingsBusy(busy) {
  state.settingsBusy = busy;
  $('bot-settings-form').setAttribute('aria-busy', String(busy));
  $('bot-api-key').disabled = busy || !state.settingsLoaded;
  $('save-bot-key').disabled = busy || !state.settingsLoaded;
  $('clear-bot-key').disabled = busy || !state.settingsLoaded || !state.keyConfigured;
  $('refresh-bot-settings').disabled = busy;
}

function resetBotSettings() {
  state.settingsRequest += 1;
  state.settingsLoaded = false;
  state.keyConfigured = false;
  $('bot-api-key').value = '';
  $('bot-key-status').textContent = '正在读取配置状态…';
  $('bot-settings-message').textContent = '';
  $('bot-settings-message').classList.remove('error');
  setSettingsBusy(false);
}

function renderBotSettings(settings) {
  state.settingsLoaded = true;
  state.keyConfigured = settings.configured;
  $('bot-key-status').textContent = settings.configured ? '已配置密钥 · 可输入新密钥替换' : '尚未配置密钥';
  $('bot-key-status').classList.toggle('is-live', settings.configured);
  $('bot-api-key').value = '';
  $('bot-api-key').placeholder = settings.configured ? '输入新密钥以替换' : '输入千问 API key';
}

async function botSettingsError(error) {
  if (error.status === 401) {
    showAuth();
    await reconnectSession();
    message(error.message);
  } else if (error.status === 403) {
    location.hash = 'characters';
    navigate();
    await loadCharacters();
    $('dashboard-message').textContent = error.message;
    $('dashboard-message').classList.add('error');
  } else {
    $('bot-settings-message').textContent = error.message;
    $('bot-settings-message').classList.add('error');
  }
}

async function loadBotSettings() {
  if (!state.account || state.account.gmlevel < 3 || state.view !== 'bot-settings' || state.settingsBusy) return;
  const requestId = ++state.settingsRequest;
  setSettingsBusy(true);
  $('bot-settings-message').textContent = '';
  $('bot-settings-message').classList.remove('error');
  try {
    const data = await api('/api/settings/playerbots-ai');
    if (requestId !== state.settingsRequest) return;
    updateAccount(data.account);
    if (requestId === state.settingsRequest) renderBotSettings(data.settings);
  } catch (error) {
    if (requestId === state.settingsRequest) await botSettingsError(error);
  } finally {
    if (requestId === state.settingsRequest) setSettingsBusy(false);
  }
}

async function saveBotSettings(clear = false) {
  if (state.settingsBusy || !state.settingsLoaded || !state.account || state.account.gmlevel < 3) return;
  const body = clear ? { clear: true } : { api_key: $('bot-api-key').value };
  $('bot-api-key').value = '';
  const requestId = ++state.settingsRequest;
  setSettingsBusy(true);
  $('bot-settings-message').textContent = '正在保存…';
  $('bot-settings-message').classList.remove('error');
  try {
    const data = await api('/api/settings/playerbots-ai', body);
    if (requestId !== state.settingsRequest) return;
    updateAccount(data.account);
    if (requestId !== state.settingsRequest) return;
    renderBotSettings(data.settings);
    $('bot-settings-message').textContent = clear ? '密钥已清除。' : '密钥已保存，后续对话将使用新密钥。';
  } catch (error) {
    if (requestId === state.settingsRequest) await botSettingsError(error);
  } finally {
    if (requestId === state.settingsRequest) setSettingsBusy(false);
  }
}

$('bot-settings-form').addEventListener('submit', event => { event.preventDefault(); saveBotSettings(); });
$('clear-bot-key').addEventListener('click', () => saveBotSettings(true));
$('refresh-bot-settings').addEventListener('click', loadBotSettings);
window.addEventListener('hashchange', navigate);
$('refresh-monitor').addEventListener('click', loadMonitor);
for (const id of ['monitor-search', 'monitor-faction', 'monitor-activity', 'monitor-group', 'monitor-presence']) {
  $(id).addEventListener(id === 'monitor-search' ? 'input' : 'change', () => { state.page = 1; renderMonitor(); });
}
$('monitor-prev').addEventListener('click', () => { state.page -= 1; renderMonitor(); });
$('monitor-next').addEventListener('click', () => { state.page += 1; renderMonitor(); });
setInterval(() => { if (!document.hidden) loadMonitor(); }, 10000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadMonitor(); });
reconnectSession();
