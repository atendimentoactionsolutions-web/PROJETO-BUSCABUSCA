// A returned message object is not proof of delivery or phone synchronization.
export function sentMessageReceipt(message, jid) {
  if (!message?.key?.id || !message.key.remoteJid || message.key.fromMe !== true) {
    throw new Error('O WhatsApp não retornou uma mensagem própria com ID válido. Conferir a conversa antes de tentar novamente.');
  }
  if (message.key.remoteJid !== jid) throw new Error('O destino retornado não corresponde ao contato solicitado.');
  const ack = message.status === undefined ? 1 : Number(message.status);
  if (!Number.isInteger(ack) || ack < 0 || ack > 5) throw new Error('Confirmação inválida retornada pelo WhatsApp.');
  return { jid, messageId: message.key.id, ack };
}
