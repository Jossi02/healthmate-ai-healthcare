const axiosModule = require('axios');
const supabase = require('../config/db');
const logger = require('../utils/logger');
const { buildDailySessionId } = require('../utils/kst');
const { getSecurityConfig } = require('../config/security');
const {
  deleteChatThread,
  listChatThreads,
  loadChatMessages,
  persistChatTurn,
} = require('../services/chatThreadService');
const axios = axiosModule.default || axiosModule;

const FASTAPI_URL = (process.env.FASTAPI_URL || 'http://localhost:8000').replace(/\/$/, '');
const AI_TIMEOUT = parseInt(process.env.AI_REQUEST_TIMEOUT || '90000', 10);
const FEEDBACK_RATINGS = new Set(['up', 'down']);
const FEEDBACK_REASON_CODES = new Set([
  'not_helpful',
  'not_personalized',
  'incorrect',
  'too_vague',
  'tone_issue',
  'unsafe',
]);

function buildFastApiHeaders() {
  return {
    'Content-Type': 'application/json',
    'x-api-key': getSecurityConfig().internalApiKey,
  };
}

function normalizeText(value) {
  if (typeof value !== 'string') return '';
  return value.trim();
}

// @route   POST /api/v1/chat
// @desc    WAS chat gateway -> FastAPI /chat
// @access  Private
exports.sendMessage = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const rawMessage = typeof req.body.user_message === 'string'
      ? req.body.user_message
      : req.body.message;
    const userMessage = rawMessage ? rawMessage.trim() : '';
    const requestedSessionId = typeof req.body.session_id === 'string'
      ? req.body.session_id.trim()
      : '';
    const clientMessageId = normalizeText(req.body.client_message_id);
    const clientUserMessageId = normalizeText(req.body.client_user_message_id);

    if (!userMessage) {
      return res.status(400).json({ error: 'message is required.' });
    }

    const sessionId = requestedSessionId || buildDailySessionId(userId);
    const payload = {
      user_id: userId,
      user_message: userMessage,
      session_id: sessionId,
    };

    const response = await axios.post(`${FASTAPI_URL}/chat`, payload, {
      timeout: AI_TIMEOUT,
      proxy: false,
      headers: buildFastApiHeaders(),
    });

    const responseSessionId = response.data?.session_id || sessionId;
    const assistantMessage = normalizeText(
      response.data?.response || response.data?.answer || response.data?.message
    );

    try {
      await persistChatTurn(supabase, {
        userId,
        sessionId: responseSessionId,
        userMessage,
        assistantMessage,
        intent: response.data?.intent || null,
        clientMessageId,
        clientUserMessageId,
      });
    } catch (persistError) {
      logger.error(`Chat log persistence error: ${persistError.message}`);
    }

    return res.json({
      ...response.data,
      client_message_id: clientMessageId || null,
      session_id: responseSessionId,
    });
  } catch (error) {
    const upstreamStatus = error.response?.status;
    const upstreamPayload = error.response?.data;

    logger.error(`Chat gateway error: ${error.message}`);

    if (upstreamStatus) {
      return res.status(502).json({
        error: 'FastAPI chat upstream error.',
        upstream_status: upstreamStatus,
        upstream_error: upstreamPayload || null,
      });
    }

    return res.status(500).json({
      error: 'Failed to process chat request.',
    });
  }
};

// @route   GET /api/v1/chat/threads
// @desc    List persisted chat threads for the current user
// @access  Private
exports.listThreads = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const threads = await listChatThreads(supabase, userId);
    return res.json({ threads });
  } catch (error) {
    logger.error(`Chat thread list error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to load chat threads.' });
  }
};

// @route   GET /api/v1/chat/threads/:session_id
// @desc    Load persisted messages for one chat thread
// @access  Private
exports.getThread = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const sessionId = normalizeText(req.params.session_id);
    if (!sessionId) {
      return res.status(400).json({ error: 'session_id is required.' });
    }

    const messages = await loadChatMessages(supabase, userId, sessionId);
    return res.json({ session_id: sessionId, messages });
  } catch (error) {
    logger.error(`Chat thread load error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to load chat thread.' });
  }
};

// @route   DELETE /api/v1/chat/threads/:session_id
// @desc    Delete one persisted chat thread and its related messages/feedback
// @access  Private
exports.deleteThread = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const sessionId = normalizeText(req.params.session_id);
    if (!sessionId) {
      return res.status(400).json({ error: 'session_id is required.' });
    }

    const deleted = await deleteChatThread(supabase, userId, sessionId);
    if (!deleted) {
      return res.status(404).json({ error: 'Chat thread not found.' });
    }

    return res.json({
      status: 'success',
      deleted: true,
      session_id: sessionId,
    });
  } catch (error) {
    logger.error(`Chat thread delete error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to delete chat thread.' });
  }
};

// @route   POST /api/v1/chat/feedback
// @desc    Save explicit user feedback for a chat answer
// @access  Private
exports.submitFeedback = async (req, res) => {
  try {
    const userId = req.user.user_id;
    const clientMessageId = normalizeText(req.body.client_message_id);
    const sessionId = normalizeText(req.body.session_id) || buildDailySessionId(userId);
    const rating = normalizeText(req.body.rating);
    const comment = normalizeText(req.body.comment) || null;
    const reasonCodes = Array.isArray(req.body.reason_codes)
      ? req.body.reason_codes
        .map((item) => normalizeText(item))
        .filter(Boolean)
      : [];

    if (!clientMessageId) {
      return res.status(400).json({ error: 'client_message_id is required.' });
    }

    if (!FEEDBACK_RATINGS.has(rating)) {
      return res.status(400).json({ error: 'rating must be either up or down.' });
    }

    const invalidReasonCode = reasonCodes.find((code) => !FEEDBACK_REASON_CODES.has(code));
    if (invalidReasonCode) {
      return res.status(400).json({ error: `Invalid reason code: ${invalidReasonCode}` });
    }

    if (rating === 'down' && reasonCodes.length === 0 && !comment) {
      return res.status(400).json({ error: 'A downvote requires a reason or comment.' });
    }

    const messages = await loadChatMessages(supabase, userId, sessionId);
    const assistantIndex = messages.findIndex(
      (message) => message.role === 'assistant'
        && (message.client_message_id === clientMessageId || normalizeText(message.id) === clientMessageId)
    );
    const assistantMessage = messages[assistantIndex];
    const userMessage = assistantIndex > 0 ? messages[assistantIndex - 1] : null;

    if (!assistantMessage || userMessage?.role !== 'user') {
      return res.status(404).json({ error: 'Chat message not found.' });
    }

    const payload = {
      user_id: userId,
      client_message_id: clientMessageId,
      session_id: sessionId,
      user_message: normalizeText(userMessage.content),
      assistant_message: normalizeText(assistantMessage.content),
      rating,
      reason_codes: reasonCodes,
      comment,
      intent: normalizeText(assistantMessage.intent) || null,
      updated_at: new Date().toISOString(),
    };

    const { data, error } = await supabase
      .from('chat_feedback')
      .upsert(payload, { onConflict: 'user_id,client_message_id' })
      .select('id, rating, reason_codes, comment, created_at, updated_at')
      .single();

    if (error) {
      logger.error(`Chat feedback save error: ${error.message}`);
      return res.status(500).json({ error: 'Failed to save chat feedback.' });
    }

    return res.status(200).json({
      success: true,
      feedback: data,
    });
  } catch (error) {
    logger.error(`Chat feedback controller error: ${error.message}`);
    return res.status(500).json({ error: 'Failed to save chat feedback.' });
  }
};
