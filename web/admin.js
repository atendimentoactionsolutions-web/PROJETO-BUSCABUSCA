const $ = id => document.getElementById(id);
let refreshing = false;
let accounts = [];

function safeText(value) {
  return String(value ?? '');
}

function setFeedback(element, message, error = false) {
  element.textContent = message;
  element.hidden = !message;
  element.classList.toggle('error', error);
}

function formatTime(value) {
  if (!value) return 'Ainda não executou';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? safeText(value) : date.toLocaleString('pt-BR');
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Falha HTTP ${response.status}`);
  return body;
}

function showAccounts(data) {
  accounts = data.accounts;
  const counts = { ativo: 0, pausado: 0, vencido: 0 };
  for (const account of accounts) counts[account.status] = (counts[account.status] || 0) + 1;
  $('accountCounts').textContent = `${accounts.length} contas · ${counts.ativo || 0} ativas · ${counts.pausado || 0} pausadas · ${counts.vencido || 0} vencidas`;
  const body = $('accountsBody');
  if (!accounts.length) {
    body.innerHTML = '<tr><td colspan="7">Nenhuma conta cadastrada.</td></tr>';
    return;
  }
  body.replaceChildren(...accounts.map(account => {
    const row = document.createElement('tr');
    const role = account.is_master ? 'Master' : (account.dfu_enabled ? 'Conta especial' : 'Cliente');
    const statusClass = account.status === 'ativo' ? 'ok' : (account.status === 'pausado' ? 'warn' : 'error');
    const cells = [
      account.username,
      account.name || account.username,
      role,
      account.status,
      account.expires_at || 'Sem limite',
      account.last_login_at || 'Nunca acessou'
    ];
    for (const value of cells) {
      const cell = document.createElement('td');
      cell.textContent = safeText(value);
      if (value === account.status) {
        const pill = document.createElement('span');
        pill.className = `status-pill ${statusClass}`;
        pill.textContent = safeText(value);
        cell.replaceChildren(pill);
      }
      row.append(cell);
    }
    const actions = document.createElement('td');
    const buttons = document.createElement('div');
    buttons.className = 'account-actions';
    const addAction = (label, action, danger = false) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `small-button${danger ? ' danger' : ''}`;
      button.dataset.action = action;
      button.dataset.username = account.username;
      button.textContent = label;
      buttons.append(button);
    };
    if (!account.is_master) addAction(account.active ? 'Pausar' : 'Ativar', 'toggle');
    addAction('Senha', 'password');
    if (!account.is_master) addAction('Vencimento', 'expiry');
    if (!account.is_master && !account.dfu_enabled) addAction('Remover', 'delete', true);
    actions.append(buttons);
    row.append(actions);
    return row;
  }));
}

async function loadAccounts() {
  const result = await requestJson('/api/accounts');
  if (!Array.isArray(result.accounts)) throw new Error('Resposta de contas inválida.');
  showAccounts(result);
}

function statusPill(status, isError = false) {
  const pill = document.createElement('span');
  const tone = isError || ['ERRO', 'CRITICO', 'ATRASADO'].includes(status)
    ? 'error'
    : status === 'OK' || status === 'MONITORANDO' || status === 'FORNECEDOR' || status === 'EXECUTANDO'
      ? 'ok'
      : 'warn';
  pill.className = `status-pill ${tone}`;
  pill.textContent = safeText(status);
  return pill;
}

function showRobots(robots) {
  const body = $('robotsBody');
  const rows = Object.entries(robots || {});
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="5">Nenhum estado de robô disponível.</td></tr>';
    return;
  }
  body.replaceChildren(...rows.map(([key, robot]) => {
    const row = document.createElement('tr');
    const detail = robot.lastError || robot.detail || 'Sem pendências recentes';
    const values = [robot.label || key, robot.intervalLabel || 'Sob demanda'];
    for (const value of values) {
      const cell = document.createElement('td');
      cell.textContent = safeText(value);
      row.append(cell);
    }
    const status = document.createElement('td');
    status.append(statusPill(robot.status || 'AGUARDANDO', robot.status === 'ERRO'));
    row.append(status);
    const checked = document.createElement('td');
    checked.textContent = formatTime(robot.lastRun);
    row.append(checked);
    const details = document.createElement('td');
    details.textContent = safeText(detail);
    if (robot.status === 'ERRO') details.className = 'robot-error';
    row.append(details);
    return row;
  }));
}

function showGroups(data) {
  const wa = data.whatsapp || {};
  const connected = wa.status === 'CONNECTED';
  const badge = $('whatsappBadge');
  badge.textContent = connected ? 'WhatsApp conectado' : safeText(wa.status || 'Desconectado');
  badge.className = `badge ${connected ? 'ok' : wa.status === 'QR_READY' ? 'warn' : 'error'}`;
  $('capturePending').textContent = safeText(wa.capture?.pending ?? 0);
  $('captureFailed').textContent = safeText(wa.capture?.failed ?? 0);
  $('productsToday').textContent = safeText(data.productsTodayCount ?? 0);
  $('invitesPending').textContent = safeText(wa.pendingInvites ?? 0);
  const lastReceived = wa.lastReceivedAt ? formatTime(wa.lastReceivedAt) : 'Sem mensagem recebida nesta sessão';
  const lastProcessed = wa.capture?.lastProcessedAt ? formatTime(wa.capture.lastProcessedAt) : 'Nenhuma lista processada nesta sessão';
  $('captureDetail').textContent = `Última mensagem recebida: ${lastReceived} · Última lista processada: ${lastProcessed}${wa.capture?.lastError ? ` · Erro: ${wa.capture.lastError}` : ''}`;
  $('updatedAt').textContent = `Atualizado em ${formatTime(data.updatedAt)} · atualiza automaticamente a cada 15 segundos`;

  const coverage = data.coverageAudit || {};
  const coverageStatus = coverage.status || 'AGUARDANDO';
  const issues = Array.isArray(coverage.issues) ? coverage.issues : [];
  $('coverageSummary').textContent = `Cobertura: ${coverageStatus} · ${coverage.participatingGroups ?? '—'} grupos participantes · ${coverage.unmonitoredGroupsCount ?? '—'} fora do monitoramento${issues.length ? ` · ${issues.join(' · ')}` : ''}`;
  $('groupsUpdated').textContent = data.groupsCheckedAt
    ? `Lista consultada em ${formatTime(data.groupsCheckedAt)}${data.groupsError ? ` · Erro: ${data.groupsError}` : ''}`
    : (data.groupsError || 'Aguardando conexão para consultar os grupos.');
  const body = $('groupsBody');
  const groups = Array.isArray(data.groups) ? data.groups : [];
  if (!groups.length) {
    body.innerHTML = `<tr><td colspan="5">${connected ? 'Nenhum grupo retornado pela consulta.' : 'Conecte o WhatsApp para listar os grupos.'}</td></tr>`;
    return;
  }
  body.replaceChildren(...groups.map(group => {
    const row = document.createElement('tr');
    const cells = [group.subject || group.id, group.id, group.size ?? 0];
    for (const value of cells) {
      const cell = document.createElement('td');
      cell.textContent = safeText(value);
      row.append(cell);
    }
    const scope = document.createElement('td');
    scope.append(statusPill(group.blacklisted ? 'FORA DO ESCOPO' : group.decision?.allowed ? 'FORNECEDOR' : 'REVISAR'));
    row.append(scope);
    const capture = document.createElement('td');
    capture.append(statusPill(group.monitored ? 'MONITORANDO' : 'NÃO CONFIRMADO', !group.monitored));
    row.append(capture);
    return row;
  }));
}

async function refreshPanel() {
  if (refreshing) return;
  refreshing = true;
  $('refreshButton').disabled = true;
  try {
    const [state] = await Promise.all([
      requestJson('/api/panel-state'),
      loadAccounts()
    ]);
    showGroups(state);
    showRobots(state.robots);
    const monitorError = state.groupsError || state.whatsappError || '';
    setFeedback($('monitorError'), monitorError, Boolean(monitorError));
  } catch (error) {
    setFeedback($('monitorError'), error.message || 'Falha ao atualizar o painel.', true);
  } finally {
    refreshing = false;
    $('refreshButton').disabled = false;
  }
}

$('createAccountForm').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form));
  try {
    const result = await requestJson('/api/accounts/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    form.reset();
    setFeedback($('accountFeedback'), `Acesso de ${result.account.username} criado.`);
    await loadAccounts();
  } catch (error) {
    setFeedback($('accountFeedback'), error.message, true);
  }
});

$('accountsBody').addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const username = button.dataset.username;
  const account = accounts.find(item => item.username === username);
  if (!account) return;
  let endpoint, payload;
  if (button.dataset.action === 'toggle') {
    endpoint = '/api/accounts/pause';
    payload = { username, active: !account.active };
  } else if (button.dataset.action === 'password') {
    const password = prompt(`Nova senha para ${username} (mínimo 3 caracteres):`);
    if (password === null) return;
    endpoint = '/api/accounts/password';
    payload = { username, password };
  } else if (button.dataset.action === 'expiry') {
    const expires_at = prompt(`Vencimento para ${username} (AAAA-MM-DD ou vazio):`, account.expires_at || '');
    if (expires_at === null) return;
    endpoint = '/api/accounts/expiry';
    payload = { username, expires_at };
  } else if (button.dataset.action === 'delete') {
    if (!confirm(`Remover permanentemente a conta ${username}?`)) return;
    endpoint = '/api/accounts/delete';
    payload = { username };
  } else return;
  try {
    await requestJson(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    setFeedback($('accountFeedback'), `Acesso de ${username} atualizado.`);
    await loadAccounts();
  } catch (error) {
    setFeedback($('accountFeedback'), error.message, true);
  }
});

$('auditButton').addEventListener('click', async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await requestJson('/api/coverage-audit', { method: 'POST' });
    await refreshPanel();
  } catch (error) {
    setFeedback($('monitorError'), error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('joinCommunityForm').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  if (!confirm('O convite será validado antes de solicitar entrada. Continuar?')) return;
  const button = form.querySelector('button');
  button.disabled = true;
  try {
    const invite = new FormData(form).get('invite');
    const result = await requestJson('/api/join-community', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ linkOrCode: invite })
    });
    setFeedback($('monitorError'), result.result?.status || 'Convite processado.', false);
    form.reset();
    await refreshPanel();
  } catch (error) {
    setFeedback($('monitorError'), error.message, true);
  } finally {
    button.disabled = false;
  }
});

$('refreshButton').addEventListener('click', refreshPanel);
$('logoutButton').addEventListener('click', async () => {
  await fetch('/api/auth/logout', { method: 'POST' });
  window.location.href = '/login';
});
refreshPanel();
setInterval(refreshPanel, 15000);
