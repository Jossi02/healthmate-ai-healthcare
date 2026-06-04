const MAX_THREAD_TITLE_LENGTH = 60;

function normalizeText(value) {
  if (typeof value !== 'string') return '';
  return value.trim();
}

function buildThreadTitle(message) {
  const normalized = normalizeText(message).replace(/\s+/g, ' ');
  if (!normalized) return '새 대화';
  if (normalized.length <= MAX_THREAD_TITLE_LENGTH) return normalized;
  return `${normalized.slice(0, MAX_THREAD_TITLE_LENGTH - 1)}…`;
}

async function persistChatTurn(db, {
  userId,
  sessionId,
  userMessage,
  assistantMessage,
  intent = null,
  clientMessageId = null,
  clientUserMessageId = null,
}) {
  const safeUserId = normalizeText(userId);
  const safeSessionId = normalizeText(sessionId);
  const safeUserMessage = normalizeText(userMessage);
  const safeAssistantMessage = normalizeText(assistantMessage);

  if (!safeUserId || !safeSessionId || !safeUserMessage || !safeAssistantMessage) {
    return null;
  }

  const now = new Date().toISOString();
  const { data: existingThread, error: threadLoadError } = await db
    .from('chat_threads')
    .select('title, message_count')
    .eq('user_id', safeUserId)
    .eq('session_id', safeSessionId)
    .maybeSingle();

  if (threadLoadError) throw threadLoadError;

  const nextThread = {
    user_id: safeUserId,
    session_id: safeSessionId,
    title: existingThread?.title || buildThreadTitle(safeUserMessage),
    message_count: Number(existingThread?.message_count || 0) + 2,
    last_message_at: now,
    updated_at: now,
  };

  const { error: threadSaveError } = await db
    .from('chat_threads')
    .upsert(nextThread, { onConflict: 'user_id,session_id' });

  if (threadSaveError) throw threadSaveError;

  const { error: messageSaveError } = await db.from('chat_messages').insert([
    {
      user_id: safeUserId,
      session_id: safeSessionId,
      role: 'user',
      content: safeUserMessage,
      client_message_id: normalizeText(clientUserMessageId) || null,
      role_order: 0,
      created_at: now,
      metadata: {},
    },
    {
      user_id: safeUserId,
      session_id: safeSessionId,
      role: 'assistant',
      content: safeAssistantMessage,
      client_message_id: normalizeText(clientMessageId) || null,
      intent: normalizeText(intent) || null,
      role_order: 1,
      created_at: now,
      metadata: {},
    },
  ]);

  if (messageSaveError) throw messageSaveError;

  return nextThread;
}

async function listChatThreads(db, userId) {
  const safeUserId = normalizeText(userId);
  if (!safeUserId) return [];

  const { data, error } = await db
    .from('chat_threads')
    .select('session_id, title, message_count, created_at, updated_at, last_message_at')
    .eq('user_id', safeUserId)
    .order('last_message_at', { ascending: false });

  if (error) throw error;
  return data || [];
}

async function loadChatMessages(db, userId, sessionId) {
  const safeUserId = normalizeText(userId);
  const safeSessionId = normalizeText(sessionId);
  if (!safeUserId || !safeSessionId) return [];

  const { data, error } = await db
    .from('chat_messages')
    .select('id, session_id, role, content, client_message_id, intent, created_at, role_order')
    .eq('user_id', safeUserId)
    .eq('session_id', safeSessionId)
    .order('created_at', { ascending: true })
    .order('role_order', { ascending: true });

  if (error) throw error;
  return data || [];
}

async function deleteChatThread(db, userId, sessionId) {
  const safeUserId = normalizeText(userId);
  const safeSessionId = normalizeText(sessionId);
  if (!safeUserId || !safeSessionId) return null;

  const { data: thread, error: threadLoadError } = await db
    .from('chat_threads')
    .select('session_id')
    .eq('user_id', safeUserId)
    .eq('session_id', safeSessionId)
    .maybeSingle();

  if (threadLoadError) throw threadLoadError;
  if (!thread) return null;

  const { error: feedbackDeleteError } = await db
    .from('chat_feedback')
    .delete()
    .eq('user_id', safeUserId)
    .eq('session_id', safeSessionId);

  if (feedbackDeleteError) throw feedbackDeleteError;

  const { error: messageDeleteError } = await db
    .from('chat_messages')
    .delete()
    .eq('user_id', safeUserId)
    .eq('session_id', safeSessionId);

  if (messageDeleteError) throw messageDeleteError;

  const { error: threadDeleteError } = await db
    .from('chat_threads')
    .delete()
    .eq('user_id', safeUserId)
    .eq('session_id', safeSessionId);

  if (threadDeleteError) throw threadDeleteError;

  return { session_id: safeSessionId };
}

module.exports = {
  buildThreadTitle,
  deleteChatThread,
  listChatThreads,
  loadChatMessages,
  persistChatTurn,
};
