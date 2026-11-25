import fs from 'node:fs';

// Persist invitations before attempting entry; resume only after WhatsApp connects.
export class InviteQueue {
  constructor({ file, execute, connected, log = () => {}, now = Date.now }) {
    Object.assign(this, { file, execute, connected, log, now });
    this.items = new Map();
    this.busy = false;
    this.timer = null;
    this.stopped = false;
    if (fs.existsSync(file)) {
      const stored = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (!Array.isArray(stored)) throw new Error('Fila de convites inválida; restaure o backup.');
      for (const item of stored) {
        if (!item || typeof item.code !== 'string') throw new Error('Convite inválido na fila salva.');
        this.items.set(item.code, item);
      }
    }
  }

  save() {
    const temporary = `${this.file}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify([...this.items.values()], null, 2), { mode: 0o600 });
    fs.renameSync(temporary, this.file);
  }

  enqueue(item) {
    if (this.items.has(item.code)) return false;
    this.items.set(item.code, { ...item, attempts: 0, status: 'PENDENTE', nextAttempt: 0 });
    this.save();
    this.wake();
    return true;
  }

  wake(delay = 0) {
    if (this.stopped || this.busy || !this.connected()) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      this.run().catch(error => this.log(`Falha na fila de convites: ${error.message}`));
    }, delay);
    this.timer.unref?.();
  }

  async run() {
    if (this.busy || this.stopped || !this.connected()) return;
    this.busy = true;
    try {
      const item = [...this.items.values()].find(value => value.status === 'PENDENTE' && value.nextAttempt <= this.now());
      if (!item) return;
      let result;
      try { result = await this.execute(item); }
      catch (error) { result = { status: 'ERRO', retryable: true, error: error.message }; }
      item.attempts++;
      item.error = result?.error || null;
      if (!result || result.status === 'ERRO') {
        if (result?.retryable !== false && item.attempts < 5) {
          item.status = 'PENDENTE';
          item.nextAttempt = this.now() + (result?.errorCode === 429 ? 60000 : Math.min(60000, 3000 * 2 ** (item.attempts - 1)));
          this.log(`Convite aguardando nova tentativa (${item.attempts}/5).`);
        } else {
          item.status = 'ERRO';
          this.log(`Convite exige conferência: ${item.error || 'entrada sem confirmação'}`);
        }
      } else {
        item.status = result.status;
        item.groupJid = result.groupJid || '';
      }
      this.save();
    } finally {
      this.busy = false;
      const pending = [...this.items.values()].filter(item => item.status === 'PENDENTE');
      if (pending.length) this.wake(Math.max(1500, Math.min(...pending.map(item => item.nextAttempt - this.now()))));
    }
  }

  stop() {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }
}
