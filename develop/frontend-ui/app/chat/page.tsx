"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import Image from "next/image";
import {
  ArrowLeft,
  Check,
  Loader2,
  MessageSquare,
  Plus,
  Send,
  Settings,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  User,
  X,
} from "lucide-react";
import { useRouter } from "next/navigation";
import {
  AUTH_TOKEN_STORAGE_KEY,
  CHAT_MESSAGES_STORAGE_KEY,
  CHAT_SESSION_STORAGE_KEY,
  redirectToLoginForExpiredSession,
} from "@/lib/auth";
import {
  PERSONA_CHAT_STARTERS,
  resolveVisiblePersona as resolvePersonaConversation,
} from "@/lib/personas";
import { usePlan } from "../context/PlanContext";

type FeedbackRating = "up" | "down";
type FeedbackReasonCode =
  | "not_helpful"
  | "not_personalized"
  | "incorrect"
  | "too_vague"
  | "tone_issue"
  | "unsafe";

type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  isStreaming?: boolean;
  syncPending?: boolean;
  syncFailed?: boolean;
  clientMessageId?: string;
  sessionId?: string;
  userMessage?: string;
  intent?: string | null;
  feedbackStatus?: "idle" | "submitting" | "submitted" | "error";
  feedbackRating?: FeedbackRating | null;
  feedbackReasonCodes?: FeedbackReasonCode[];
  feedbackComment?: string | null;
};

type ChatThread = {
  session_id: string;
  title: string;
  message_count: number;
  created_at: string;
  updated_at: string;
  last_message_at: string;
};

type PersistedChatMessage = {
  id: string;
  session_id: string;
  role: "user" | "assistant";
  content: string;
  client_message_id?: string | null;
  intent?: string | null;
  created_at: string;
};

const FEEDBACK_REASON_OPTIONS: { code: FeedbackReasonCode; label: string }[] = [
  { code: "not_helpful", label: "도움이 안 됐어요" },
  { code: "not_personalized", label: "내 상황에 안 맞아요" },
  { code: "incorrect", label: "내용이 부정확해요" },
  { code: "too_vague", label: "너무 모호해요" },
  { code: "tone_issue", label: "말투가 별로예요" },
  { code: "unsafe", label: "위험하거나 불편해요" },
];

const AI_PERSONAS = [
  {
    id: "cheer_sis",
    name: "응원 누나",
    shortLabel: "응원",
    description: "밝게 밀어주는 치어 코치",
    tone: "칭찬과 에너지",
    imageSrc: "/personas/cheer_sis.jpg",
    imageAlt: "밝은 치어 코치 스타일의 응원 누나 아바타",
    accent: "from-rose-400 to-amber-400",
    selectedClass: "border-rose-300 bg-rose-50 text-rose-700",
  },
  {
    id: "soft_senior",
    name: "다정 선배",
    shortLabel: "다정",
    description: "무리하지 않게 챙기는 선배",
    tone: "안심과 회복",
    imageSrc: "/personas/soft_senior.jpg",
    imageAlt: "부드럽게 챙겨주는 다정 선배 아바타",
    accent: "from-teal-400 to-emerald-500",
    selectedClass: "border-emerald-300 bg-emerald-50 text-emerald-700",
  },
  {
    id: "strict_trainer",
    name: "직진 PT쌤",
    shortLabel: "직진",
    description: "짧고 단호한 실행 코치",
    tone: "명확한 지시",
    imageSrc: "/personas/strict_trainer.jpg",
    imageAlt: "헤드셋을 낀 단호한 직진 PT쌤 아바타",
    accent: "from-slate-700 to-zinc-500",
    selectedClass: "border-slate-300 bg-slate-100 text-slate-800",
  },
  {
    id: "science_coach",
    name: "분석 코치",
    shortLabel: "분석",
    description: "이유와 근거를 차분히 설명",
    tone: "납득과 효율",
    imageSrc: "/personas/science_coach.jpg",
    imageAlt: "안경과 차트가 있는 분석 코치 아바타",
    accent: "from-sky-500 to-cyan-400",
    selectedClass: "border-sky-300 bg-sky-50 text-sky-700",
  },
  {
    id: "playful_buddy",
    name: "운동 메이트",
    shortLabel: "메이트",
    description: "가볍게 같이 움직이는 친구",
    tone: "친근한 동행",
    imageSrc: "/personas/playful_buddy.jpg",
    imageAlt: "캐주얼한 운동 메이트 아바타",
    accent: "from-violet-500 to-fuchsia-400",
    selectedClass: "border-violet-300 bg-violet-50 text-violet-700",
  },
  {
    id: "daily_manager",
    name: "생활 매니저",
    shortLabel: "관리",
    description: "루틴과 일정을 깔끔하게 정리",
    tone: "체계적인 관리",
    imageSrc: "/personas/daily_manager.jpg",
    imageAlt: "체크리스트를 든 생활 매니저 아바타",
    accent: "from-lime-500 to-green-500",
    selectedClass: "border-lime-300 bg-lime-50 text-lime-700",
  },
] as const satisfies readonly {
  id: string;
  name: string;
  shortLabel: string;
  description: string;
  tone: string;
  imageSrc: string;
  imageAlt: string;
  accent: string;
  selectedClass: string;
}[];

type AiPersona = (typeof AI_PERSONAS)[number];
type AiPersonaId = AiPersona["id"];

const LEGACY_PERSONA_ALIASES: Record<string, AiPersonaId> = {
  default: "cheer_sis",
  warm: "soft_senior",
  spartan: "strict_trainer",
  evidence: "science_coach",
  buddy: "playful_buddy",
};

function resolveVisiblePersona(personaId?: string | null): AiPersona {
  const normalizedId =
    personaId && personaId in LEGACY_PERSONA_ALIASES
      ? LEGACY_PERSONA_ALIASES[personaId]
      : personaId;

  return (
    AI_PERSONAS.find((persona) => persona.id === normalizedId) ||
    AI_PERSONAS[0]
  );
}

function getApiBaseUrl() {
  const raw =
    process.env.NEXT_PUBLIC_BACKEND_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "";
  return raw.endsWith("/") ? raw.slice(0, -1) : raw;
}

function buildApiUrl(path: string) {
  const baseUrl = getApiBaseUrl();
  return baseUrl ? `${baseUrl}${path}` : path;
}

function createClientMessageId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }

  return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function createThreadSessionId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return `thread-${crypto.randomUUID()}`;
  }

  return `thread-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const CHAT_WELCOME_MESSAGE = "\uC548\uB155\uD558\uC138\uC694. \uAC74\uAC15, \uC2DD\uB2E8, \uC6B4\uB3D9 \uACC4\uD68D\uC5D0 \uB300\uD574 \uD3B8\uD558\uAC8C \uBB3C\uC5B4\uBCF4\uC138\uC694.";
const CHAT_FALLBACK_MESSAGE = "\uB2F5\uBCC0\uC744 \uBD88\uB7EC\uC624\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4.";
const CHAT_SEND_ERROR_MESSAGE = "\uBA54\uC2DC\uC9C0\uB97C \uBCF4\uB0B4\uB294 \uC911 \uC624\uB958\uAC00 \uBC1C\uC0DD\uD588\uC2B5\uB2C8\uB2E4. \uC7A0\uC2DC \uD6C4 \uB2E4\uC2DC \uC2DC\uB3C4\uD574\uC8FC\uC138\uC694.";
const CHAT_SYNC_PENDING_LABEL = "\uACC4\uD68D \uBC18\uC601 \uC911";
const CHAT_SYNC_FAILED_LABEL = "\uBC18\uC601\uC774 \uC9C0\uC5F0\uB418\uACE0 \uC788\uC5B4\uC694";
const CHAT_FEEDBACK_SAVED_LABEL = "\uD53C\uB4DC\uBC31\uC774 \uC800\uC7A5\uB410\uC5B4\uC694.";
const CHAT_THREAD_FALLBACK_TITLE = "\uC0C8 \uB300\uD654";
const CHAT_THREAD_DELETE_TITLE = "\uB300\uD654 \uC0AD\uC81C";
const CHAT_THREAD_DELETE_BODY = "\uC774 \uB300\uD654\uC640 \uC800\uC7A5\uB41C \uBA54\uC2DC\uC9C0, \uD53C\uB4DC\uBC31\uC744 \uC0AD\uC81C\uD560\uAE4C\uC694?";
const CHAT_THREAD_DELETE_NOTE = "\uC0AD\uC81C\uD558\uBA74 \uB2E4\uC2DC \uBCF5\uAD6C\uD560 \uC218 \uC5C6\uC5B4\uC694.";
const CHAT_THREAD_DELETE_CANCEL_LABEL = "\uCDE8\uC18C";
const CHAT_THREAD_DELETE_CONFIRM_LABEL = "\uC0AD\uC81C";
const CHAT_THREAD_DELETE_DELETING_LABEL = "\uC0AD\uC81C \uC911...";
const CHAT_THREAD_DELETE_ERROR_MESSAGE = "\uB300\uD654 \uC0AD\uC81C\uC5D0 \uC2E4\uD328\uD588\uC5B4\uC694. \uC7A0\uC2DC \uD6C4 \uB2E4\uC2DC \uC2DC\uB3C4\uD574\uC8FC\uC138\uC694.";

function createWelcomeMessages(): Message[] {
  return [
    {
      id: "welcome",
      role: "assistant",
      content: CHAT_WELCOME_MESSAGE,
    },
  ];
}

export default function ChatPage() {
  const router = useRouter();
  const { fetchPlans, isUserLoading, userData } = usePlan();
  const [messages, setMessages] = useState<Message[]>(createWelcomeMessages);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [isThreadListLoading, setIsThreadListLoading] = useState(false);
  const [input, setInput] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [deleteThreadTarget, setDeleteThreadTarget] =
    useState<ChatThread | null>(null);
  const [deletingThreadId, setDeletingThreadId] = useState<string | null>(null);
  const [threadDeleteError, setThreadDeleteError] = useState("");
  const [feedbackModal, setFeedbackModal] = useState<{
    messageId: string;
    selectedReasons: FeedbackReasonCode[];
    comment: string;
    errorMsg: string;
    isSubmitting: boolean;
  } | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const pendingSyncTimersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const selectedPersona = resolveVisiblePersona(userData?.selected_ai_persona);
  const showFeedbackControls = true;
  const personaConversation = resolvePersonaConversation(
    userData?.selected_ai_persona
  );
  const hasUserStartedConversation = messages.some(
    (message) => message.role === "user"
  );

  const mapPersistedMessages = useCallback((
    persistedMessages: PersistedChatMessage[]
  ): Message[] => {
    let latestUserMessage = "";

    const mapped = persistedMessages.map((message) => {
      if (message.role === "user") {
        latestUserMessage = message.content;
      }

      return {
        id: message.id,
        role: message.role,
        content: message.content,
        isStreaming: false,
        clientMessageId:
          message.role === "assistant"
            ? message.client_message_id || message.id
            : message.client_message_id || undefined,
        sessionId: message.session_id,
        userMessage:
          message.role === "assistant" ? latestUserMessage : undefined,
        intent: message.role === "assistant" ? message.intent || null : null,
        feedbackStatus: message.role === "assistant" ? "idle" : undefined,
        feedbackRating: null,
        feedbackReasonCodes: [],
        feedbackComment: null,
      } satisfies Message;
    });

    return mapped.length > 0 ? mapped : createWelcomeMessages();
  }, []);

  const resetCurrentThread = useCallback(() => {
    const nextMessages = createWelcomeMessages();
    setSessionId(null);
    setMessages(nextMessages);
    window.sessionStorage.removeItem(CHAT_SESSION_STORAGE_KEY);
    window.sessionStorage.setItem(
      CHAT_MESSAGES_STORAGE_KEY,
      JSON.stringify(nextMessages)
    );
  }, []);

  const loadThreadList = useCallback(async (token: string) => {
    setIsThreadListLoading(true);
    try {
      const response = await fetch(buildApiUrl("/api/v1/chat/threads"), {
        headers: {
          "ngrok-skip-browser-warning": "true",
          Authorization: `Bearer ${token}`,
        },
      });

      if (response.status === 401) {
        redirectToLoginForExpiredSession();
        return [];
      }

      if (!response.ok) {
        throw new Error("Thread list API request failed.");
      }

      const data = await response.json();
      const nextThreads = Array.isArray(data.threads)
        ? (data.threads as ChatThread[])
        : [];
      setThreads(nextThreads);
      return nextThreads;
    } catch (error) {
      console.error("Failed to load chat threads:", error);
      return [];
    } finally {
      setIsThreadListLoading(false);
    }
  }, []);

  const loadThreadMessages = useCallback(async (
    targetSessionId: string,
    token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  ) => {
    if (!targetSessionId || !token) return;

    try {
      const response = await fetch(
        buildApiUrl(`/api/v1/chat/threads/${encodeURIComponent(targetSessionId)}`),
        {
          headers: {
            "ngrok-skip-browser-warning": "true",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      if (response.status === 401) {
        redirectToLoginForExpiredSession();
        return;
      }

      if (!response.ok) {
        throw new Error("Thread messages API request failed.");
      }

      const data = await response.json();
      const nextMessages = Array.isArray(data.messages)
        ? mapPersistedMessages(data.messages as PersistedChatMessage[])
        : createWelcomeMessages();

      setSessionId(targetSessionId);
      setMessages(nextMessages);
      window.sessionStorage.setItem(CHAT_SESSION_STORAGE_KEY, targetSessionId);
      window.sessionStorage.setItem(
        CHAT_MESSAGES_STORAGE_KEY,
        JSON.stringify(nextMessages)
      );
    } catch (error) {
      console.error("Failed to load chat thread:", error);
    }
  }, [mapPersistedMessages]);

  const startNewThread = () => {
    const nextSessionId = createThreadSessionId();
    setDeleteThreadTarget(null);
    setThreadDeleteError("");
    setSessionId(nextSessionId);
    setMessages(createWelcomeMessages());
    window.sessionStorage.setItem(CHAT_SESSION_STORAGE_KEY, nextSessionId);
    window.sessionStorage.setItem(
      CHAT_MESSAGES_STORAGE_KEY,
      JSON.stringify(createWelcomeMessages())
    );
  };

  const requestDeleteThread = useCallback((thread: ChatThread) => {
    if (deletingThreadId) return;
    if (isLoading && thread.session_id === sessionId) return;

    setThreadDeleteError("");
    setDeleteThreadTarget(thread);
  }, [deletingThreadId, isLoading, sessionId]);

  const closeDeleteThreadDialog = useCallback(() => {
    if (deletingThreadId) return;

    setDeleteThreadTarget(null);
    setThreadDeleteError("");
  }, [deletingThreadId]);

  const confirmDeleteThread = useCallback(async () => {
    if (!deleteThreadTarget || deletingThreadId) return;
    if (isLoading && deleteThreadTarget.session_id === sessionId) return;

    const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
    if (!token) {
      redirectToLoginForExpiredSession();
      return;
    }

    const targetSessionId = deleteThreadTarget.session_id;
    const remainingThreads = threads.filter(
      (thread) => thread.session_id !== targetSessionId
    );

    setDeletingThreadId(targetSessionId);
    setThreadDeleteError("");

    try {
      const response = await fetch(
        buildApiUrl(`/api/v1/chat/threads/${encodeURIComponent(targetSessionId)}`),
        {
          method: "DELETE",
          headers: {
            "ngrok-skip-browser-warning": "true",
            Authorization: `Bearer ${token}`,
          },
        }
      );

      if (response.status === 401) {
        redirectToLoginForExpiredSession();
        return;
      }

      if (!response.ok && response.status !== 404) {
        throw new Error("Chat thread delete API request failed.");
      }

      setThreads(remainingThreads);
      setFeedbackModal(null);
      setDeleteThreadTarget(null);

      if (targetSessionId === sessionId) {
        const nextThread = remainingThreads[0];
        if (nextThread) {
          await loadThreadMessages(nextThread.session_id, token);
        } else {
          resetCurrentThread();
        }
      }
    } catch (error) {
      console.error("Failed to delete chat thread:", error);
      setThreadDeleteError(CHAT_THREAD_DELETE_ERROR_MESSAGE);
    } finally {
      setDeletingThreadId(null);
    }
  }, [
    deleteThreadTarget,
    deletingThreadId,
    isLoading,
    loadThreadMessages,
    resetCurrentThread,
    sessionId,
    threads,
  ]);

  useEffect(() => {
    const storedToken = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
    if (!storedToken) {
      router.replace("/login");
      return;
    }

    const storedSessionId = window.sessionStorage.getItem(
      CHAT_SESSION_STORAGE_KEY
    );
    const storedMessages = window.sessionStorage.getItem(
      CHAT_MESSAGES_STORAGE_KEY
    );
    if (storedSessionId) {
      setSessionId(storedSessionId);
    }
    if (storedMessages) {
      try {
        const parsed = JSON.parse(storedMessages) as Message[];
        if (Array.isArray(parsed) && parsed.length > 0) {
          setMessages(
            parsed.map((message) => ({
              ...message,
              isStreaming: false,
            }))
          );
        }
      } catch (error) {
        console.error("Failed to restore chat messages:", error);
      }
    }

    void (async () => {
      const threadList = await loadThreadList(storedToken);
      if (storedSessionId && !storedMessages) {
        await loadThreadMessages(storedSessionId, storedToken);
        return;
      }

      if (!storedSessionId && !storedMessages && threadList[0]?.session_id) {
        await loadThreadMessages(threadList[0].session_id, storedToken);
      }
    })();
  }, [loadThreadList, loadThreadMessages, router]);

  useEffect(() => {
    const persistedMessages =
      messages.length > 0
        ? messages.map((message) => ({
            ...message,
            isStreaming: false,
          }))
        : createWelcomeMessages();

    window.sessionStorage.setItem(
      CHAT_MESSAGES_STORAGE_KEY,
      JSON.stringify(persistedMessages)
    );
  }, [messages]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  useEffect(() => {
    return () => {
      pendingSyncTimersRef.current.forEach((timer) => clearTimeout(timer));
      pendingSyncTimersRef.current = [];
    };
  }, []);

  const simulateStreamingResponse = async (
    fullText: string,
    metadata?: Partial<
      Pick<Message, "clientMessageId" | "sessionId" | "userMessage" | "intent" | "syncPending">
    >
  ) => {
    const messageId = metadata?.clientMessageId || Date.now().toString();
    setMessages((prev) => [
      ...prev,
      {
        id: messageId,
        role: "assistant",
        content: "",
        isStreaming: true,
        syncPending: metadata?.syncPending === true,
        syncFailed: false,
        clientMessageId: metadata?.clientMessageId,
        sessionId: metadata?.sessionId,
        userMessage: metadata?.userMessage,
        intent: metadata?.intent,
        feedbackStatus: metadata?.clientMessageId ? "idle" : undefined,
        feedbackRating: null,
        feedbackReasonCodes: [],
        feedbackComment: null,
      },
    ]);

    let currentText = "";
    const chars = fullText.split("");
    const fastStreamForTest =
      typeof window !== "undefined" &&
      Boolean(
        (
          window as Window & {
            __HEALTH_MATE_TEST_FAST_STREAM__?: boolean;
          }
        ).__HEALTH_MATE_TEST_FAST_STREAM__
      );

    if (fastStreamForTest) {
      setMessages((prev) =>
        prev.map((message) =>
          message.id === messageId
            ? { ...message, content: fullText, isStreaming: false }
            : message
        )
      );
      return;
    }

    for (let i = 0; i < chars.length; i += 1) {
      const delay = chars[i] === " " ? 20 : Math.random() * 30 + 10;
      await new Promise((resolve) => setTimeout(resolve, delay));
      currentText += chars[i];
      setMessages((prev) =>
        prev.map((message) =>
          message.id === messageId
            ? { ...message, content: currentText }
            : message
        )
      );
    }

    setMessages((prev) =>
        prev.map((message) =>
          message.id === messageId
            ? { ...message, isStreaming: false }
            : message
        )
      );
  };

  const clearPendingSyncTimers = useCallback(() => {
    pendingSyncTimersRef.current.forEach((timer) => clearTimeout(timer));
    pendingSyncTimersRef.current = [];
  }, []);

  const markMessageSynced = useCallback((messageId: string) => {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === messageId
          ? { ...message, syncPending: false, syncFailed: false }
          : message
      )
    );
  }, []);

  const markMessageSyncFailed = useCallback((messageId: string) => {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === messageId
          ? { ...message, syncPending: false, syncFailed: true }
          : message
      )
    );
  }, []);

  const schedulePendingPlanRefresh = useCallback(
    (messageId: string) => {
      clearPendingSyncTimers();
      [2500, 6000, 12000, 20000].forEach((delay, index, delays) => {
        const timer = setTimeout(async () => {
          const synced = await fetchPlans({ trackChanges: true });
          if (synced) {
            markMessageSynced(messageId);
          } else if (index === delays.length - 1) {
            markMessageSyncFailed(messageId);
          }
        }, delay);
        pendingSyncTimersRef.current.push(timer);
      });
    },
    [clearPendingSyncTimers, fetchPlans, markMessageSyncFailed, markMessageSynced]
  );

  const submitFeedback = async (
    message: Message,
    rating: FeedbackRating,
    reasonCodes: FeedbackReasonCode[] = [],
    comment = ""
  ) => {
    if (!message.clientMessageId || !message.sessionId || !message.userMessage) {
      return false;
    }

    const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
    if (!token) {
      redirectToLoginForExpiredSession();
      return false;
    }

    setMessages((prev) =>
      prev.map((item) =>
        item.id === message.id
          ? { ...item, feedbackStatus: "submitting" }
          : item
      )
    );

    try {
      const response = await fetch(buildApiUrl("/api/v1/chat/feedback"), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "true",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          client_message_id: message.clientMessageId,
          session_id: message.sessionId,
          user_message: message.userMessage,
          assistant_message: message.content,
          rating,
          reason_codes: reasonCodes,
          comment: comment.trim() || null,
          intent: message.intent || null,
        }),
      });

      if (response.status === 401) {
        redirectToLoginForExpiredSession();
        return false;
      }

      if (!response.ok) {
        throw new Error("Feedback API request failed.");
      }

      setMessages((prev) =>
        prev.map((item) =>
          item.id === message.id
            ? {
                ...item,
                feedbackStatus: "submitted",
                feedbackRating: rating,
                feedbackReasonCodes: reasonCodes,
                feedbackComment: comment.trim() || null,
              }
            : item
        )
      );
      return true;
    } catch (error) {
      console.error("Chat feedback error:", error);
      setMessages((prev) =>
        prev.map((item) =>
          item.id === message.id
            ? { ...item, feedbackStatus: "error" }
            : item
        )
      );
      return false;
    }
  };

  const handleThumbsUp = async (message: Message) => {
    if (message.feedbackStatus === "submitted") return;
    await submitFeedback(message, "up");
  };

  const openThumbsDownModal = (messageId: string) => {
    const target = messages.find((message) => message.id === messageId);
    if (!target || target.feedbackStatus === "submitted") return;

    setFeedbackModal({
      messageId,
      selectedReasons: target.feedbackReasonCodes || [],
      comment: target.feedbackComment || "",
      errorMsg: "",
      isSubmitting: false,
    });
  };

  const toggleFeedbackReason = (reasonCode: FeedbackReasonCode) => {
    setFeedbackModal((prev) => {
      if (!prev) return prev;

      const exists = prev.selectedReasons.includes(reasonCode);
      return {
        ...prev,
        selectedReasons: exists
          ? prev.selectedReasons.filter((code) => code !== reasonCode)
          : [...prev.selectedReasons, reasonCode],
      };
    });
  };

  const closeFeedbackModal = () => {
    setFeedbackModal(null);
  };

  const handleThumbsDownSubmit = async () => {
    if (!feedbackModal) return;

    const targetMessage = messages.find(
      (message) => message.id === feedbackModal.messageId
    );
    if (!targetMessage) {
      closeFeedbackModal();
      return;
    }

    if (
      feedbackModal.selectedReasons.length === 0 &&
      !feedbackModal.comment.trim()
    ) {
      setFeedbackModal((prev) =>
        prev
          ? {
              ...prev,
              errorMsg: "싫어요 이유를 하나 이상 선택하거나 코멘트를 입력해주세요.",
            }
          : prev
      );
      return;
    }

    setFeedbackModal((prev) =>
      prev
        ? {
            ...prev,
            isSubmitting: true,
            errorMsg: "",
          }
        : prev
    );

    const didSave = await submitFeedback(
      targetMessage,
      "down",
      feedbackModal.selectedReasons,
      feedbackModal.comment
    );

    if (didSave) {
      closeFeedbackModal();
      return;
    }

    setFeedbackModal((prev) =>
      prev
        ? {
            ...prev,
            isSubmitting: false,
            errorMsg: "피드백 저장에 실패했습니다. 다시 시도해주세요.",
          }
        : prev
    );
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!input.trim() || isLoading) return;

    const userMessage = input.trim();
    const requestSessionId = sessionId || createThreadSessionId();
    const userMessageId = createClientMessageId();
    const assistantMessageId = createClientMessageId();
    setInput("");
    setMessages((prev) => [
      ...prev,
      {
        id: userMessageId,
        role: "user",
        content: userMessage,
        sessionId: requestSessionId,
      },
    ]);
    if (!sessionId) {
      setSessionId(requestSessionId);
      window.sessionStorage.setItem(CHAT_SESSION_STORAGE_KEY, requestSessionId);
    }
    setIsLoading(true);

    try {
      const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY);
      if (!token) {
        redirectToLoginForExpiredSession();
        return;
      }

      const endpoint = buildApiUrl("/api/v1/chat");

      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "ngrok-skip-browser-warning": "true",
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({
          message: userMessage,
          session_id: requestSessionId,
          client_user_message_id: userMessageId,
          client_message_id: assistantMessageId,
        }),
      });

      if (response.status === 401) {
        redirectToLoginForExpiredSession();
        return;
      }

      if (!response.ok) {
        throw new Error("Chat API request failed.");
      }

      const data = await response.json();
      const nextSessionId =
        typeof data.session_id === "string" ? data.session_id : null;
      const effectiveSessionId = nextSessionId || requestSessionId;
      if (nextSessionId) {
        setSessionId(nextSessionId);
        window.sessionStorage.setItem(
          CHAT_SESSION_STORAGE_KEY,
          nextSessionId
        );
      }

      const botText =
        data.response || data.answer || data.message || CHAT_FALLBACK_MESSAGE;
      const intent = typeof data.intent === "string" ? data.intent : null;
      const planSyncApplied = data.plan_sync_applied === true;
      const syncPending = Number(data.pending_writes_count || 0) > 0;

      if (planSyncApplied) {
        void fetchPlans({ trackChanges: true });
      }

      setIsLoading(false);
      await simulateStreamingResponse(botText, {
        clientMessageId:
          typeof data.client_message_id === "string"
            ? data.client_message_id
            : assistantMessageId,
        sessionId: effectiveSessionId || undefined,
        userMessage,
        intent,
        syncPending,
      });
      if (syncPending) {
        schedulePendingPlanRefresh(
          typeof data.client_message_id === "string"
            ? data.client_message_id
            : assistantMessageId
        );
      }
      void loadThreadList(token);
    } catch (error) {
      console.error("Chat API Error:", error);
      setIsLoading(false);
      await simulateStreamingResponse(
        CHAT_SEND_ERROR_MESSAGE,
        {
          clientMessageId: assistantMessageId,
          sessionId: requestSessionId,
          userMessage,
        }
      );
    }
  };

  return (
    <div className="flex h-[100dvh] bg-[#f8fafc] font-sans">
      <aside className="hidden w-72 shrink-0 flex-col border-r border-gray-200/70 bg-white lg:flex">
        <div className="border-b border-gray-100 px-4 py-5">
          <button
            type="button"
            onClick={startNewThread}
            className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#2563eb] px-4 py-3 text-sm font-extrabold text-white shadow-sm transition-colors hover:bg-blue-700"
          >
            <Plus className="h-4 w-4" />
            새 대화
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-3 py-3">
          {isThreadListLoading ? (
            <div className="flex items-center gap-2 px-3 py-3 text-xs font-bold text-gray-400">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              불러오는 중
            </div>
          ) : threads.length === 0 ? (
            <div className="px-3 py-3 text-xs font-bold text-gray-400">
              저장된 대화 없음
            </div>
          ) : (
            <div className="space-y-1">
              {threads.map((thread) => {
                const isActive = thread.session_id === sessionId;
                return (
                  <div
                    key={thread.session_id}
                    className={`group flex w-full items-center rounded-xl transition-colors ${
                      isActive
                        ? "bg-blue-50 text-blue-700"
                        : "text-gray-600 hover:bg-gray-50"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => loadThreadMessages(thread.session_id)}
                      disabled={deletingThreadId === thread.session_id}
                      className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2.5 text-left disabled:cursor-wait disabled:opacity-60"
                    >
                    <MessageSquare className="h-4 w-4 shrink-0" />
                    <span className="min-w-0 flex-1 truncate text-sm font-bold">
                      {thread.title || "새 대화"}
                    </span>
                    <span className="shrink-0 text-[10px] font-black text-gray-400">
                      {thread.message_count}
                    </span>
                  </button>
                    <button
                      type="button"
                      aria-label={`${thread.title || CHAT_THREAD_FALLBACK_TITLE} ${CHAT_THREAD_DELETE_CONFIRM_LABEL}`}
                      onClick={() => requestDeleteThread(thread)}
                      disabled={
                        deletingThreadId === thread.session_id ||
                        (isLoading && isActive)
                      }
                      className="mr-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-gray-400 opacity-0 transition-all hover:bg-rose-50 hover:text-rose-500 focus:opacity-100 focus:outline-none focus:ring-2 focus:ring-rose-200 disabled:cursor-not-allowed disabled:opacity-40 group-hover:opacity-100"
                    >
                      {deletingThreadId === thread.session_id ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
      <header className="sticky top-0 z-10 border-b border-gray-200/60 bg-white/90 px-5 pb-4 pt-12 shadow-sm backdrop-blur-md">
        <div className="mx-auto flex max-w-2xl items-center justify-between gap-4">
          <button
            aria-label="홈으로 돌아가기"
            onClick={() => router.push("/")}
            className="rounded-full p-2 text-gray-600 transition-colors hover:bg-gray-100"
          >
            <ArrowLeft className="h-6 w-6" />
          </button>
          <div className="flex items-center gap-3">
            <div
              className={`relative flex h-11 w-11 items-center justify-center overflow-hidden rounded-xl bg-gradient-to-br ${selectedPersona.accent} text-white shadow-inner`}
            >
              <Image
                src={selectedPersona.imageSrc}
                alt={selectedPersona.imageAlt}
                width={44}
                height={44}
                priority
                unoptimized
                className="h-full w-full object-cover"
              />
              <span className="absolute -right-1 -top-1 inline-flex h-3 w-3 rounded-full bg-emerald-500" />
            </div>
            <div>
              <h1 className="text-xl font-extrabold tracking-tight text-gray-900">
                AI 건강 비서
              </h1>
              <p className="text-xs font-semibold text-emerald-600">
                {isUserLoading
                  ? "프로필 확인 중"
                  : `${selectedPersona.name}와 대화 중`}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => router.push("/profile")}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-2 text-xs font-bold text-gray-600 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50"
          >
            <Settings className="h-4 w-4" />
            코치 설정
          </button>
        </div>
        <div className="mx-auto mt-4 flex max-w-2xl gap-2 overflow-x-auto lg:hidden">
          <button
            type="button"
            onClick={startNewThread}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-[#2563eb] px-3 py-2 text-xs font-black text-white"
          >
            <Plus className="h-3.5 w-3.5" />
            새 대화
          </button>
          {threads.map((thread) => {
            const isActive = thread.session_id === sessionId;
            return (
            <div
              key={thread.session_id}
              className={`inline-flex shrink-0 items-center overflow-hidden rounded-full border text-xs font-black ${
                isActive
                  ? "border-blue-200 bg-blue-50 text-blue-700"
                  : "border-gray-200 bg-white text-gray-500"
              }`}
            >
              <button
                type="button"
                onClick={() => loadThreadMessages(thread.session_id)}
                disabled={deletingThreadId === thread.session_id}
                className="max-w-36 truncate px-3 py-2 disabled:cursor-wait disabled:opacity-60"
              >
              {thread.title || "새 대화"}
              </button>
              <button
                type="button"
                aria-label={`${thread.title || CHAT_THREAD_FALLBACK_TITLE} ${CHAT_THREAD_DELETE_CONFIRM_LABEL}`}
                onClick={() => requestDeleteThread(thread)}
                disabled={
                  deletingThreadId === thread.session_id ||
                  (isLoading && isActive)
                }
                className="inline-flex h-8 w-8 items-center justify-center border-l border-current/10 text-current/70 transition-colors hover:bg-rose-50 hover:text-rose-500 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {deletingThreadId === thread.session_id ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Trash2 className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
            );
          })}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-6 py-6 pb-32 md:px-8">
        <div className="mx-auto flex min-h-full max-w-2xl flex-col gap-6">
          {!hasUserStartedConversation && !isLoading && (
            <motion.section
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.24 }}
              className="mx-auto flex w-full max-w-xl flex-col items-center text-center"
            >
              <div
                className={`relative flex h-44 w-44 items-center justify-center overflow-hidden rounded-[2rem] bg-gradient-to-br ${selectedPersona.accent} shadow-[0_24px_60px_-22px_rgba(15,23,42,0.55)] ring-1 ring-white md:h-52 md:w-52`}
              >
                <Image
                  src={selectedPersona.imageSrc}
                  alt={selectedPersona.imageAlt}
                  width={208}
                  height={208}
                  priority
                  unoptimized
                  className="h-full w-full object-cover"
                />
              </div>
              <div className="mt-6">
                <p className="text-xs font-black uppercase tracking-[0.18em] text-emerald-600">
                  지금 대화할 코치
                </p>
                <h2 className="mt-2 text-3xl font-black tracking-tight text-gray-950 md:text-4xl">
                  {selectedPersona.name}
                </h2>
                <p className="mx-auto mt-4 max-w-md whitespace-pre-wrap text-[15px] font-semibold leading-relaxed text-gray-600">
                  “{personaConversation.intro}”
                </p>
              </div>
              <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
                {PERSONA_CHAT_STARTERS.map((starter) => (
                  <button
                    key={starter.label}
                    type="button"
                    onClick={() => setInput(starter.prompt)}
                    className="rounded-2xl border border-gray-200 bg-white px-4 py-3 text-sm font-extrabold text-gray-700 shadow-sm transition-colors hover:border-gray-300 hover:bg-gray-50"
                  >
                    {starter.label}
                  </button>
                ))}
              </div>
            </motion.section>
          )}
          <AnimatePresence initial={false}>
            {messages.filter((message) => message.id !== "welcome").map((message) => (
              <motion.div
                key={message.id}
                initial={{ opacity: 0, y: 12, scale: 0.96 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.2 }}
                className={`flex max-w-[85%] gap-3 ${
                  message.role === "user"
                    ? "ml-auto flex-row-reverse"
                    : "mr-auto"
                }`}
              >
                <div
                  className={`mt-auto flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full shadow-sm ${
                    message.role === "user"
                      ? "bg-blue-600 text-white"
                      : `bg-gradient-to-br ${selectedPersona.accent} text-white`
                  }`}
                >
                  {message.role === "user" ? (
                    <User className="h-4 w-4" />
                  ) : (
                    <Image
                      src={selectedPersona.imageSrc}
                      alt={selectedPersona.imageAlt}
                      width={32}
                      height={32}
                      unoptimized
                      className="h-full w-full rounded-full object-cover"
                    />
                  )}
                </div>

                <div
                  className={`rounded-2xl p-4 text-[15px] leading-relaxed ${
                    message.role === "user"
                      ? "rounded-br-sm bg-[#2563eb] text-white shadow-[0_4px_20px_rgba(37,99,235,0.25)]"
                      : "rounded-bl-sm border border-gray-100/70 bg-white text-gray-800 shadow-[0_4px_20px_rgba(0,0,0,0.06)]"
                  }`}
                >
                  <p className="whitespace-pre-wrap">{message.content}</p>
                  {message.role === "assistant" &&
                    message.syncPending &&
                    !message.isStreaming && (
                      <div className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-[11px] font-bold text-amber-700">
                        <Loader2 className="h-3 w-3 animate-spin" />
                        <span>{CHAT_SYNC_PENDING_LABEL}</span>
                      </div>
                    )}
                  {message.role === "assistant" &&
                    message.syncFailed &&
                    !message.isStreaming && (
                      <div className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-rose-200 bg-rose-50 px-3 py-1 text-[11px] font-bold text-rose-600">
                        <X className="h-3 w-3" />
                        <span>{CHAT_SYNC_FAILED_LABEL}</span>
                      </div>
                    )}
                  {message.isStreaming && (
                    <motion.span
                      animate={{ opacity: [1, 0] }}
                      transition={{ duration: 0.8, repeat: Infinity }}
                      className="ml-1 inline-block h-4 w-1.5 align-middle bg-[#2563eb]"
                    />
                  )}
                  {message.role === "assistant" &&
                    showFeedbackControls &&
                    message.clientMessageId &&
                    !message.isStreaming && (
                      <div className="mt-3 border-t border-gray-100 pt-3">
                        <div className="flex items-center justify-between gap-3">
                          <span className="text-[11px] font-medium text-gray-400">
                            이 답변이 도움이 됐나요?
                          </span>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => handleThumbsUp(message)}
                              disabled={message.feedbackStatus === "submitting" || message.feedbackStatus === "submitted"}
                              className={`inline-flex items-center justify-center rounded-full border p-2 transition-colors ${
                                message.feedbackRating === "up"
                                  ? "border-emerald-200 bg-emerald-50 text-emerald-600"
                                  : "border-gray-200 bg-white text-gray-400 hover:border-emerald-200 hover:text-emerald-600"
                              } disabled:cursor-not-allowed disabled:opacity-70`}
                              aria-label="좋아요"
                            >
                              <ThumbsUp className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => openThumbsDownModal(message.id)}
                              disabled={message.feedbackStatus === "submitting" || message.feedbackStatus === "submitted"}
                              className={`inline-flex items-center justify-center rounded-full border p-2 transition-colors ${
                                message.feedbackRating === "down"
                                  ? "border-rose-200 bg-rose-50 text-rose-600"
                                  : "border-gray-200 bg-white text-gray-400 hover:border-rose-200 hover:text-rose-600"
                              } disabled:cursor-not-allowed disabled:opacity-70`}
                              aria-label="싫어요"
                            >
                              <ThumbsDown className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        </div>
                        {message.feedbackStatus === "submitted" && (
                          <div className="mt-2 flex items-center gap-1.5 text-[11px] font-semibold text-emerald-600">
                            <Check className="h-3.5 w-3.5" />
                            <span>{CHAT_FEEDBACK_SAVED_LABEL}</span>
                          </div>
                        )}
                        {message.feedbackStatus === "error" && (
                          <p className="mt-2 text-[11px] font-semibold text-rose-500">
                            피드백 저장에 실패했습니다. 다시 시도해주세요.
                          </p>
                        )}
                      </div>
                    )}
                </div>
              </motion.div>
            ))}

            {isLoading && (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="mr-auto flex max-w-[85%] gap-3"
              >
                <div
                  className={`mt-auto flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br ${selectedPersona.accent} text-white shadow-sm`}
                >
                  <Image
                    src={selectedPersona.imageSrc}
                    alt={selectedPersona.imageAlt}
                    width={32}
                    height={32}
                    unoptimized
                    className="h-full w-full rounded-full object-cover"
                  />
                </div>
                <div className="flex items-center space-x-2 rounded-2xl rounded-bl-sm border border-gray-100/60 bg-white px-5 py-4 shadow-[0_4px_20px_rgba(0,0,0,0.06)]">
                  <Loader2 className="h-4 w-4 animate-spin text-[#2563eb]" />
                  <span className="text-sm font-medium text-gray-500">
                    응답 생성 중
                  </span>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
          <div ref={messagesEndRef} className="h-4" />
        </div>
      </div>

      <AnimatePresence>
        {deleteThreadTarget && (
          <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm"
              onClick={closeDeleteThreadDialog}
            />
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 18 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 18 }}
              transition={{ type: "spring", damping: 24, stiffness: 280 }}
              className="relative z-10 w-full max-w-sm overflow-hidden rounded-3xl border border-gray-100 bg-white shadow-[0_20px_60px_-12px_rgba(0,0,0,0.18)]"
            >
              <div className="flex items-start justify-between gap-4 border-b border-gray-100 px-6 py-5">
                <div>
                  <h2 className="text-lg font-black text-gray-950">
                    {CHAT_THREAD_DELETE_TITLE}
                  </h2>
                  <p className="mt-1 text-sm font-semibold text-gray-500">
                    {CHAT_THREAD_DELETE_BODY}
                  </p>
                </div>
                <button
                  type="button"
                  aria-label={CHAT_THREAD_DELETE_CANCEL_LABEL}
                  onClick={closeDeleteThreadDialog}
                  disabled={deletingThreadId === deleteThreadTarget.session_id}
                  className="rounded-full border border-gray-100 bg-white p-2 text-gray-400 transition-colors hover:text-gray-600 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-3 px-6 py-5">
                <div className="rounded-2xl bg-gray-50 px-4 py-3 text-sm font-bold text-gray-800">
                  {deleteThreadTarget.title || CHAT_THREAD_FALLBACK_TITLE}
                </div>
                <p className="text-xs font-semibold text-rose-500">
                  {CHAT_THREAD_DELETE_NOTE}
                </p>
                {threadDeleteError && (
                  <p className="text-sm font-bold text-rose-600">
                    {threadDeleteError}
                  </p>
                )}
              </div>

              <div className="flex gap-3 border-t border-gray-100 bg-white px-6 py-5">
                <button
                  type="button"
                  onClick={closeDeleteThreadDialog}
                  disabled={deletingThreadId === deleteThreadTarget.session_id}
                  className="flex-1 rounded-2xl bg-gray-100 py-3 text-sm font-bold text-gray-700 transition-colors hover:bg-gray-200 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {CHAT_THREAD_DELETE_CANCEL_LABEL}
                </button>
                <button
                  type="button"
                  onClick={confirmDeleteThread}
                  disabled={deletingThreadId === deleteThreadTarget.session_id}
                  className="inline-flex flex-1 items-center justify-center gap-2 rounded-2xl bg-rose-500 py-3 text-sm font-bold text-white transition-colors hover:bg-rose-600 disabled:cursor-wait disabled:opacity-70"
                >
                  {deletingThreadId === deleteThreadTarget.session_id ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      {CHAT_THREAD_DELETE_DELETING_LABEL}
                    </>
                  ) : (
                    CHAT_THREAD_DELETE_CONFIRM_LABEL
                  )}
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      <AnimatePresence>
        {feedbackModal && (
          <div className="fixed inset-0 z-[120] flex items-center justify-center p-4">
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 bg-gray-900/40 backdrop-blur-sm"
              onClick={closeFeedbackModal}
            />
            <motion.div
              initial={{ opacity: 0, scale: 0.96, y: 18 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.96, y: 18 }}
              transition={{ type: "spring", damping: 24, stiffness: 280 }}
              className="relative z-10 w-full max-w-md overflow-hidden rounded-3xl border border-gray-100 bg-white shadow-[0_20px_60px_-12px_rgba(0,0,0,0.15)]"
            >
              <div className="flex items-center justify-between border-b border-gray-100 bg-gray-50/70 px-6 py-5">
                <div>
                  <h2 className="text-lg font-bold text-gray-900">
                    싫어요 이유를 알려주세요
                  </h2>
                  <p className="mt-1 text-sm font-medium text-gray-500">
                    답변 개선에만 사용됩니다.
                  </p>
                </div>
                <button
                  type="button"
                  aria-label="피드백 닫기"
                  onClick={closeFeedbackModal}
                  className="rounded-full border border-gray-100 bg-white p-2 text-gray-400 transition-colors hover:text-gray-600"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              <div className="space-y-5 px-6 py-6">
                <div className="flex flex-wrap gap-2">
                  {FEEDBACK_REASON_OPTIONS.map((reason) => {
                    const isSelected = feedbackModal.selectedReasons.includes(
                      reason.code
                    );

                    return (
                      <button
                        key={reason.code}
                        type="button"
                        onClick={() => toggleFeedbackReason(reason.code)}
                        className={`rounded-full border px-4 py-2 text-sm font-semibold transition-colors ${
                          isSelected
                            ? "border-rose-200 bg-rose-50 text-rose-600"
                            : "border-gray-200 bg-white text-gray-600 hover:border-rose-200 hover:text-rose-600"
                        }`}
                      >
                        {reason.label}
                      </button>
                    );
                  })}
                </div>

                <div>
                  <label className="mb-2 block text-sm font-semibold text-gray-700">
                    추가 의견
                  </label>
                  <textarea
                    value={feedbackModal.comment}
                    onChange={(event) =>
                      setFeedbackModal((prev) =>
                        prev
                          ? {
                              ...prev,
                              comment: event.target.value,
                            }
                          : prev
                      )
                    }
                    rows={4}
                    placeholder="선택 사항입니다. 어떤 점이 아쉬웠는지 알려주세요."
                    className="w-full resize-none rounded-2xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-900 transition-all placeholder:text-gray-400 focus:border-rose-300 focus:bg-white focus:outline-none focus:ring-4 focus:ring-rose-100"
                  />
                </div>

                {feedbackModal.errorMsg && (
                  <p className="text-sm font-semibold text-rose-500">
                    {feedbackModal.errorMsg}
                  </p>
                )}
              </div>

              <div className="flex gap-3 border-t border-gray-100 bg-white px-6 py-5">
                <button
                  type="button"
                  onClick={closeFeedbackModal}
                  className="flex-1 rounded-2xl bg-gray-100 py-3 text-sm font-bold text-gray-700 transition-colors hover:bg-gray-200"
                >
                  취소
                </button>
                <button
                  type="button"
                  onClick={handleThumbsDownSubmit}
                  disabled={feedbackModal.isSubmitting}
                  className="flex-1 rounded-2xl bg-rose-500 py-3 text-sm font-bold text-white transition-colors hover:bg-rose-600 disabled:opacity-70"
                >
                  {feedbackModal.isSubmitting ? "저장 중..." : "보내기"}
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      <div className="relative z-10 border-t border-gray-200/80 bg-white p-4 pb-28 shadow-[0_-4px_30px_rgba(0,0,0,0.04)]">
        <div className="mx-auto max-w-2xl">
          <form onSubmit={handleSubmit} className="flex items-end gap-3">
            <div className="relative flex-1">
              <input
                type="text"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="건강 관련 질문을 입력해주세요"
                className="w-full rounded-2xl border border-gray-200 bg-gray-50/80 py-4 pl-5 pr-14 text-[15px] text-gray-900 shadow-inner transition-all placeholder:text-gray-400 focus:border-[#2563eb] focus:bg-white focus:outline-none focus:ring-4 focus:ring-[#2563eb]/15"
                disabled={isLoading}
              />
              <button
                aria-label="메시지 보내기"
                type="submit"
                disabled={!input.trim() || isLoading}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-xl bg-[#2563eb] p-2.5 text-white shadow-[0_4px_12px_rgba(37,99,235,0.3)] transition-all hover:bg-blue-700 disabled:opacity-50"
              >
                {isLoading ? (
                  <Loader2 className="h-5 w-5 animate-spin" />
                ) : (
                  <Send className="h-5 w-5" />
                )}
              </button>
            </div>
          </form>
          <p className="mt-3 text-center text-[11px] font-medium text-gray-400">
            프론트는 backend-api를 통해 AI 서버와 통신합니다.
          </p>
        </div>
      </div>
      </div>
    </div>
  );
}
