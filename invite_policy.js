// Invitation discovery never grants permission to join unrelated groups.
export function projectInviteDecision(groupName, { knownSupplier = false, blocked = false } = {}) {
  const name = String(groupName || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  if (blocked) return { allowed: false, status: 'FORA_ESCOPO', reason: 'Grupo bloqueado por decisão do usuário.' };
  const hardBlocked = /desapeg|feira.{0,16}rolo|feirao|brecho|promissoria|capital\s+gain|so\s+links|links\s+(?:de\s+grupos|bairros)|moradores|condominio|rataiada|tudo\s+nosso\s+capao/;
  if (hardBlocked.test(name)) return { allowed: false, status: 'FORA_ESCOPO', reason: 'Grupo de desapego, vendas gerais ou assunto alheio ao projeto.' };
  const project = /\b(?:apple|iphone\w*|ipad\w*|macbook\w*|cell\w*|celular\w*|phone\w*|eletronic\w*|atacado|fornecedor\w*|import\w*|mobile|tech|swap|games)\b/.test(name);
  const generalSales = /(?:compra\w*.{0,16}venda|venda\w*.{0,16}compra)|vende\s+tudo|venda\w*.{0,24}(?:regiao|bairro|zona|osasco|grajau|franklin|ipiranga)|(?:produtos\s+e\s+servicos)|gostou\s+comprou|negocios.{0,24}(?:parelheiros|regiao)|kids/;
  if (!project && generalSales.test(name)) return { allowed: false, status: 'FORA_ESCOPO', reason: 'Grupo de vendas gerais sem relação identificada com fornecedores.' };
  if (project || knownSupplier) return { allowed: true, status: 'PERMITIDO' };
  return { allowed: false, status: 'REVISAR_ESCOPO', reason: 'Relação com fornecedores não confirmada; requer revisão explícita.' };
}

// Also enforce explicit blocks if an old pending request is approved later.
export function createBlockedMembershipGuard({ getSocket, isConnected, isBlocked, onLeft, onError }) {
  const active = new Set();
  return async function leaveBlockedMembership(jid) {
    if (!jid || active.has(jid)) return false;
    active.add(jid);
    try {
      const socket = getSocket();
      if (!socket || !isConnected() || !isBlocked(jid)) return false;
      await socket.groupLeave(jid);
      await onLeft(jid);
      return true;
    } catch (error) {
      onError(error, jid);
      return false;
    } finally { active.delete(jid); }
  };
}
