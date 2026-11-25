import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

// Use the supplier event time also when WhatsApp replays an old edit.
export function supplierEventTime(message, envelopeTimestamp, editTimestamp) {
  for (const value of [message?.protocolMessage?.timestampMs,
                       message?.editedMessage?.message?.timestampMs, editTimestamp, envelopeTimestamp]) {
    const number = Number(value);
    if (Number.isFinite(number) && number > 0) {
      return Math.floor(number > 20000000000 ? number / 1000 : number);
    }
  }
  return 0;
}

// Each message is committed to disk before the panel is contacted. Completed
// receipts retain only the ID, so history sync cannot repeatedly parse a list.
export class CaptureQueue {
  constructor({ directory, deliver, log = () => {}, now = Date.now }) {
    Object.assign(this, { directory, deliver, log, now });
    this.items = new Map();
    this.busy = false;
    this.stopped = false;
    this.timer = null;
    this.unreadable = 0;
    this.unreadableIds = new Set();
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
    for (const name of fs.readdirSync(directory)) {
      if (!/^[a-f0-9]{64}\.json$/.test(name)) continue;
      try {
        const item = JSON.parse(fs.readFileSync(path.join(directory, name), 'utf8'));
        if (name !== `${item.id}.json` || !['PENDING', 'DONE', 'FAILED'].includes(item.status)
          || !Number.isFinite(item.updatedAt) || !Number.isFinite(item.nextAttempt)
          || (item.status !== 'DONE' && (!item.payload || typeof item.payload !== 'object'))) {
          throw new Error('registro inválido');
        }
        this.items.set(item.id, item);
      } catch (error) {
        this.unreadable++;
        this.unreadableIds.add(name.slice(0, -5));
        this.log(`[CAPTURA] Registro preservado para recuperação: ${name} (${error.message}).`);
      }
    }
    this.prune();
  }

  save(item) {
    const file = path.join(this.directory, `${item.id}.json`);
    const temporary = `${file}.${process.pid}.tmp`;
    const fd = fs.openSync(temporary, 'w', 0o600);
    try {
      fs.writeFileSync(fd, JSON.stringify(item));
      fs.fsyncSync(fd);
    } finally { fs.closeSync(fd); }
    fs.renameSync(temporary, file);
    this.items.set(item.id, item);
  }

  enqueue(payload) {
    const identity = payload.messageId
      ? [payload.remoteJid, payload.participantJid, payload.messageId, ...(payload.editKey ? [payload.timestamp, payload.editKey] : [])]
      : [payload.remoteJid, payload.participantJid, payload.timestamp, payload.text, payload.imageBase64];
    const id = createHash('sha256').update(JSON.stringify(identity)).digest('hex');
    if (this.unreadableIds.has(id)) throw new Error('Lista possui registro ilegível; recuperar o arquivo preservado antes de repetir.');
    if (this.items.has(id)) return false;
    this.save({ id, payload, status: 'PENDING', attempts: 0, nextAttempt: 0, updatedAt: this.now() });
    this.wake();
    return true;
  }

  prune() {
    for (const [id, item] of this.items) {
      if (item.status === 'DONE' && item.updatedAt < this.now() - 48 * 3600000) {
        fs.unlinkSync(path.join(this.directory, `${id}.json`));
        this.items.delete(id);
      }
    }
  }

  wake(delay = 0) {
    if (this.stopped || this.busy) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.run().catch(error => {
        this.log(`[CAPTURA] Fila preservada; falha de gravação: ${error.message}`);
        this.wake(30000);
      });
    }, delay);
    this.timer.unref?.();
  }

  async run() {
    if (this.busy || this.stopped) return;
    this.busy = true;
    try {
      this.prune();
      const item = [...this.items.values()].find(entry => entry.status === 'PENDING' && entry.nextAttempt <= this.now());
      if (!item) return;
      let error;
      try { await this.deliver(item.payload); }
      catch (failure) { error = failure; }
      const next = { ...item, attempts: item.attempts + 1, updatedAt: this.now() };
      if (error) {
        next.status = error.retryable === false ? 'FAILED' : 'PENDING';
        next.error = error.message;
        next.nextAttempt = this.now() + Math.min(60000, 3000 * 2 ** Math.min(item.attempts, 5));
        this.log(`[CAPTURA] ${next.status === 'FAILED' ? 'Lista exige conferência' : 'Lista salva; nova tentativa agendada'}: ${error.message}`);
      } else {
        next.status = 'DONE';
        delete next.payload;
        delete next.error;
      }
      this.save(next);
    } finally {
      this.busy = false;
      const pending = [...this.items.values()].filter(item => item.status === 'PENDING');
      if (pending.length) this.wake(Math.max(50, pending.reduce((delay, item) => Math.min(delay, item.nextAttempt - this.now()), 60000)));
    }
  }

  status() {
    const items = [...this.items.values()];
    const last = items.reduce((latest, item) => item.updatedAt > (latest?.updatedAt || 0) ? item : latest, null);
    return {
      pending: items.filter(item => item.status === 'PENDING').length,
      failed: items.filter(item => item.status === 'FAILED').length + this.unreadable,
      lastProcessedAt: items.reduce((latest, item) => item.status === 'DONE' ? Math.max(latest, item.updatedAt) : latest, 0) || null,
      lastError: last?.error || null,
      processing: this.busy
    };
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}

export async function withTimeout(promise, milliseconds, message) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), milliseconds);
    })]);
  } finally { clearTimeout(timer); }
}

export async function deliverCapture(url, payload, { fetchImpl = fetch, timeoutMs = 90000 } = {}) {
  const response = await fetchImpl(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload), signal: AbortSignal.timeout(timeoutMs)
  });
  if (!response.ok) {
    throw Object.assign(new Error(`Painel retornou HTTP ${response.status}`), {
      retryable: ![400, 401, 403, 404, 413, 415, 422].includes(response.status)
    });
  }
  const data = await response.json();
  if (data?.ok !== true || !Number.isInteger(data.parsedCount) || data.parsedCount < 0) {
    throw new Error('Painel não confirmou o processamento da lista');
  }
  return data;
}
