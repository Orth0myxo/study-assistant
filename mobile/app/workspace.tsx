import * as DocumentPicker from 'expo-document-picker';
import { useLocalSearchParams, useRouter } from 'expo-router';
import * as Sharing from 'expo-sharing';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Keyboard,
  KeyboardAvoidingView,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import {
  SafeAreaView,
  useSafeAreaInsets,
} from 'react-native-safe-area-context';

import {
  ApiError,
  createTopicSource,
  createTopicStudyOutput,
  downloadStudyExport,
  downloadTopicResultExport,
  getStudyJob,
  getTopicResult,
  requestChatCommand,
  setBackendWakeListener,
  reviewTopicSource,
  startRangeStudy,
  startStudyJob,
  uploadDocument,
  listStudyJobs,
  listTopicResults,
  type UploadFile,
} from '@/services/apiClient';
import {
  CHAT_TOPIC_MAX_CHARS,
  CHAT_TOPIC_SOURCE_SEED,
  parseChatIntent,
  parsePageSelection,
} from '@/services/chatOrchestrator';
import {
  formatSourceRef,
  TOPIC_MAX_IMAGES,
} from '@/services/topicWorkflow';
import { pickNoteImages } from '@/services/imagePicker';
import {
  groupRecentSessions,
  latestOutput,
  RecentStudySession,
} from '@/services/recentSessions';
import {
  displayFont,
  palette,
  radii,
  spacing,
} from '@/theme';
import {
  DocumentFormat,
  DocumentResponse,
  ExportFormat,
  StudyJobResponse,
  StudyMode,
  StudyStatus,
  TopicReviewResponse,
  TopicSourceDocument,
  TopicStudyMode,
  TopicStudyOutputResponse,
  ChatCommand,
} from '@/types/api';

const PDF = 'application/pdf';
const DOCX =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const DOCX_MIME =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const POLL_INTERVAL_MS = 2000;
const MAX_POLLS = 150;
const TERMINAL_STATUSES: StudyStatus[] = [
  'completed',
  'partial',
  'failed',
];

const TOPIC_MODE_LABELS: Record<TopicStudyMode, string> = {
  source_passages: 'Original passages',
  ai_study_material: 'Topic summary',
  both: 'Combined study',
};

const STUDY_STATUS_LABELS: Record<StudyStatus, string> = {
  queued: 'Queued',
  running: 'Working',
  completed: 'Ready',
  partial: 'Ready with notes',
  failed: 'Failed',
};

interface SourceDoc {
  documentId: string;
  filename: string;
  format: DocumentFormat;
  pageCount: number | null;
  available: boolean;
  textExtractable: boolean | null;
  extractionStatus: string | null;
  sourceId?: string;
}

type ChatMessage =
  | { id: string; role: 'user'; kind: 'text'; text: string }
  | {
      id: string;
      role: 'assistant';
      kind: 'text';
      text: string;
      tone?: 'info' | 'warn';
    }
  | {
      id: string;
      role: 'assistant';
      kind: 'summary';
      job: StudyJobResponse;
      retry?: () => void;
      refresh?: () => void;
    }
  | {
      id: string;
      role: 'assistant';
      kind: 'topic';
      topic: string;
      review: TopicReviewResponse;
      retry?: () => void;
    }
  | {
      id: string;
      role: 'assistant';
      kind: 'material';
      topic: string;
      mode: TopicStudyMode;
      output: TopicStudyOutputResponse;
      retry?: () => void;
    }
  | {
      id: string;
      role: 'assistant';
      kind: 'error';
      text: string;
      retry?: () => void;
    };

type DistributiveOmit<T, K extends keyof T> = T extends unknown
  ? Omit<T, K>
  : never;
type ChatMessageDraft = DistributiveOmit<ChatMessage, 'id'>;

type PendingState = { title: string; detail?: string };

let messageSequence = 0;

function nextMessageId(): string {
  messageSequence += 1;
  return `msg_${Date.now()}_${messageSequence}`;
}

function makeMessage(draft: ChatMessageDraft): ChatMessage {
  return { ...draft, id: nextMessageId() } as ChatMessage;
}

function assistantText(
  text: string,
  tone: 'info' | 'warn' = 'info',
): ChatMessage {
  return makeMessage({ role: 'assistant', kind: 'text', text, tone });
}

function isTerminal(status: StudyStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function errorText(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function safeStem(filename: string | null | undefined): string {
  const base = (filename ?? 'study').replace(/\.[^/.]+$/, '') || 'study';
  const cleaned = base.replace(/[\\/:*?"<>|]+/g, '-').trim();
  return cleaned.slice(0, 100).replace(/[.\s]+$/g, '') || 'study';
}

function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${Math.round(bytes / 1024)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function sourceFromDocument(document: DocumentResponse): SourceDoc {
  return {
    documentId: document.document_id,
    filename: document.filename,
    format: document.format,
    pageCount: document.page_count,
    available: true,
    textExtractable: document.extraction.text_extractable,
    extractionStatus: document.extraction.status,
  };
}

function sourceFromJob(job: StudyJobResponse): SourceDoc {
  return {
    documentId: job.document_id,
    filename: job.filename,
    format: job.format,
    pageCount: job.page_count,
    available: job.document_available,
    textExtractable: null,
    extractionStatus: null,
  };
}

function sourceFromTopicDocument(
  document: TopicSourceDocument,
  sourceId: string,
  available: boolean,
): SourceDoc {
  return {
    documentId: document.document_id,
    filename: document.filename,
    format: document.format,
    pageCount: document.page_count,
    available,
    textExtractable: null,
    extractionStatus: null,
    sourceId,
  };
}

const LOCAL_VERBATIM_PATTERN =
  /\b(verbatim|exact(?:ly)?|as written|original text|source text)\b/i;
const LOCAL_SUMMARY_ONLY_PATTERN =
  /\b(summary only|summari[sz]e only|explain|no original text|without the original)\b/i;
const LOCAL_BOTH_PATTERN =
  /\b(with|plus|and)\s+(?:a\s+)?(?:short\s+)?(?:summary|explanation)\b|\bsummary\s+and\s+original\b/i;
const LOCAL_WHOLE_SOURCE_PATTERN =
  /\b(?:whole|entire|full|complete)\s+(?:document|material|notes?|text|file|source)\b|\b(?:the|this|my)\s+(?:document|material|notes?|file)\b|\b(?:source text|document text|full text|everything|all of it|all (?:the )?(?:exact |original |source )?(?:text|content))\b/i;
const LOCAL_TOPIC_MARKER_PATTERN =
  /\b(?:about|on|regarding|covering|discussing)\b/i;
const LOCAL_EXACT_ONLY_PATTERN =
  /^(?:please\s+)?(?:give|show|return|get|extract|fetch|copy|send)?\s*(?:me\s+)?(?:the\s+)?(?:verbatim|exact|original)\s+(?:text|source|content|material)(?:\s+text)?\s*[.!?]*$/i;

function localChatMode(text: string): ChatCommand['mode'] {
  if (LOCAL_VERBATIM_PATTERN.test(text) && LOCAL_BOTH_PATTERN.test(text)) {
    return 'both';
  }
  if (LOCAL_SUMMARY_ONLY_PATTERN.test(text)) {
    return 'ai_study_material';
  }
  return 'source_passages';
}

function commandFromLocalIntent(text: string): ChatCommand {
  const selector: ChatCommand['selector'] = {
    type: 'whole',
    topic: null,
    pages: [],
    chapter: null,
    items: [],
  };
  const mode = localChatMode(text);
  const exportFormat: ChatCommand['export_format'] =
    /\b(word|docx|document file)\b/i.test(text)
      ? 'docx'
      : /\b(pdf|printable)\b/i.test(text)
        ? 'pdf'
        : null;
  const pages = parsePageSelection(text);
  if (pages.length > 0) {
    return {
      action: 'extract',
      selector: { ...selector, type: 'pages', pages },
      mode,
      export_format: exportFormat,
      question: null,
    };
  }
  if (
    LOCAL_VERBATIM_PATTERN.test(text) &&
    !LOCAL_SUMMARY_ONLY_PATTERN.test(text) &&
    !LOCAL_TOPIC_MARKER_PATTERN.test(text) &&
    (LOCAL_WHOLE_SOURCE_PATTERN.test(text) ||
      LOCAL_EXACT_ONLY_PATTERN.test(text))
  ) {
    return {
      action: 'summarize_whole',
      selector,
      mode,
      export_format: exportFormat,
      question: null,
    };
  }
  const intent = parseChatIntent(text);
  if (intent.kind === 'whole_summary') {
    return {
      action: 'summarize_whole',
      selector,
      mode: 'ai_study_material',
      export_format: null,
      question: null,
    };
  }
  if (intent.kind === 'topic') {
    return {
      action: 'extract',
      selector: { ...selector, type: 'topic', topic: intent.topic },
      mode: intent.generate ? 'ai_study_material' : 'source_passages',
      export_format: null,
      question: null,
    };
  }
  if (intent.kind === 'help') {
    return { action: 'help', selector, mode: 'source_passages', export_format: null, question: null };
  }
  return {
    action: 'clarify',
    selector,
    mode: 'source_passages',
    export_format: null,
    question:
      intent.kind === 'need_topic'
        ? 'Name the topic you want to find.'
        : intent.kind === 'empty'
          ? 'What would you like to study?'
          : 'Try naming a topic, page range, or study action.',
  };
}

export default function StudyWorkspaceScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    jobId?: string;
    resultId?: string;
    prompt?: string;
  }>();
  const jobId = typeof params.jobId === 'string' ? params.jobId : null;
  const resultId =
    typeof params.resultId === 'string' ? params.resultId : null;
  const initialPrompt =
    typeof params.prompt === 'string' ? params.prompt.trim() : '';

  const [sources, setSources] = useState<SourceDoc[]>([]);
  const [activeDocumentId, setActiveDocumentId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [pending, setPending] = useState<PendingState | null>(null);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [lastTopic, setLastTopic] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [recentSessions, setRecentSessions] = useState<RecentStudySession[]>([]);
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false);
  const [pendingImageFiles, setPendingImageFiles] = useState<UploadFile[] | null>(null);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const insets = useSafeAreaInsets();

  const listRef = useRef<ScrollView>(null);
  const runningRef = useRef(false);
  const mountedRef = useRef(true);
  const topicSourceRef = useRef(new Map<string, string>());
  const refreshSummaryRef = useRef<(jobId: string) => void>(() => {});
  const queuedPromptRef = useRef<string | null>(null);
  const initialPromptSentRef = useRef(false);
  const sendRef = useRef<
    (text: string, options?: { silentUser?: boolean }) => void
  >(() => {});

  const loadHistory = async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const [jobs, results] = await Promise.all([
        listStudyJobs(50),
        listTopicResults(50),
      ]);
      setRecentSessions(groupRecentSessions(jobs, results));
    } catch (error) {
      setHistoryError(errorText(error, 'Study history could not be loaded.'));
    } finally {
      setHistoryLoading(false);
    }
  };

  const startNewChat = () => {
    setHistoryOpen(false);
    setAttachmentMenuOpen(false);
    setSources([]);
    setActiveDocumentId(null);
    setMessages([]);
    setInput('');
    setPending(null);
    setPendingImageFiles(null);
    setBusy(false);
    setPicking(false);
    setLastTopic(null);
    runningRef.current = false;
    queuedPromptRef.current = null;
    initialPromptSentRef.current = false;
    topicSourceRef.current.clear();
    router.replace('/');
  };

  const openSavedSession = (session: RecentStudySession) => {
    const output = latestOutput(session);
    if (!output) {
      return;
    }
    setHistoryOpen(false);
    router.push({
      pathname: '/workspace',
      params: { [output.kind === 'study' ? 'jobId' : 'resultId']: output.id },
    });
  };

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    setBackendWakeListener(() => {
      setPending({
        title: 'Waking up the server…',
        detail: 'The server may take a moment to start.',
      });
    });
    return () => setBackendWakeListener(null);
  }, []);

  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', () =>
      setKeyboardVisible(true),
    );
    const hide = Keyboard.addListener('keyboardDidHide', () =>
      setKeyboardVisible(false),
    );
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);

  useEffect(() => {
    listRef.current?.scrollToEnd({ animated: true });
  }, [messages, pending]);

  useEffect(() => {
    if (!jobId && !resultId) {
      return;
    }

    let cancelled = false;
    const reopen = async () => {
      setBusy(true);
      try {
        if (resultId) {
          setPending({ title: 'Reopening your saved study material…' });
          const output = await getTopicResult(resultId);
          if (cancelled) {
            return;
          }
          const document = output.document;
          if (document) {
            const source = sourceFromTopicDocument(
              document,
              output.source_id,
              output.document_available,
            );
            topicSourceRef.current.set(document.document_id, output.source_id);
            setSources([source]);
            setActiveDocumentId(source.documentId);
          }
          const topic = output.topics[0]?.text ?? null;
          if (topic) {
            setLastTopic(topic);
          }
          setMessages([
            assistantText(
              `Reopened your saved ${TOPIC_MODE_LABELS[output.mode].toLowerCase()} for “${document?.filename ?? 'your material'}”.`,
            ),
            makeMessage({
              role: 'assistant',
              kind: 'material',
              topic: topic ?? 'Saved topic',
              mode: output.mode,
              output,
            }),
          ]);
        } else if (jobId) {
          setPending({ title: 'Reopening your saved summary…' });
          let job = await getStudyJob(jobId);
          let attempts = 0;
          while (
            !cancelled &&
            !isTerminal(job.status) &&
            attempts < MAX_POLLS
          ) {
            setPending({
              title: 'Finishing your saved summary…',
              detail: `${job.progress.processed_units} of ${job.progress.total_units} parts processed`,
            });
            await sleep(POLL_INTERVAL_MS);
            job = await getStudyJob(jobId);
            attempts += 1;
          }
          if (cancelled) {
            return;
          }
          const source = sourceFromJob(job);
          setSources([source]);
          setActiveDocumentId(source.documentId);
          setMessages([
            assistantText(`Reopened your saved summary for “${job.filename}”.`),
            makeMessage({
              role: 'assistant',
              kind: 'summary',
              job,
              refresh: () => {
                refreshSummaryRef.current(job.job_id);
              },
            }),
          ]);
        }
      } catch (error) {
        if (!cancelled) {
          setMessages([
            makeMessage({
              role: 'assistant',
              kind: 'error',
              text: errorText(
                error,
                'The saved session could not be reopened. It may have expired.',
              ),
            }),
          ]);
        }
      } finally {
        if (!cancelled) {
          setBusy(false);
          setPending(null);
        }
      }
    };

    void reopen();
    return () => {
      cancelled = true;
    };
  }, [jobId, resultId]);

  const activeDoc =
    sources.find((source) => source.documentId === activeDocumentId) ??
    sources[0] ??
    null;

  const push = (message: ChatMessage) => {
    setMessages((current) => [...current, message]);
  };

  const pushError = (
    error: unknown,
    fallback: string,
    retry?: () => void,
  ) => {
    push(
      makeMessage({
        role: 'assistant',
        kind: 'error',
        text: errorText(error, fallback),
        retry,
      }),
    );
  };

  const beginRun = (): boolean => {
    if (runningRef.current) {
      return false;
    }
    runningRef.current = true;
    setBusy(true);
    return true;
  };

  const finishRun = () => {
    runningRef.current = false;
    setBusy(false);
    setPending(null);
  };

  const uploadImagesForDocument = async (
    files: UploadFile[],
    doc: SourceDoc,
  ) => {
    setPending({
      title: 'Reading your note images...',
      detail: `Matching OCR text to ${doc.filename}`,
    });
    try {
      const source = await createTopicSource(doc.documentId, files);
      if (!mountedRef.current) {
        return;
      }
      topicSourceRef.current.set(doc.documentId, source.source_id);
      const recognized = source.images
        .map((image, index) => {
          const text = image.text.trim();
          return text ? `Image ${index + 1}: ${text}` : '';
        })
        .filter(Boolean);
      push(
        assistantText(
          recognized.length > 0
            ? `Added ${files.length} ${files.length === 1 ? 'image' : 'images'} and read this text:\n\n${recognized.join('\n\n')}\n\nAsk me to find a topic from these notes in ${doc.filename}.`
            : `Added ${files.length} ${files.length === 1 ? 'image' : 'images'}, but OCR did not find readable text. You can still name a topic to search for in ${doc.filename}, or use the guided jottings flow to edit transcripts.`,
          recognized.length > 0 ? 'info' : 'warn',
        ),
      );
    } catch (error) {
      if (mountedRef.current) {
        pushError(error, 'The note images could not be processed.');
      }
    }
  };

  const attachFile = async (file: UploadFile) => {
    if (!beginRun()) {
      return;
    }
    setPending({
      title: 'Uploading and extracting your material…',
      detail: file.name,
    });
    try {
      const document = await uploadDocument(file);
      if (!mountedRef.current) {
        return;
      }
      const source = sourceFromDocument(document);
      setSources((current) => [
        source,
        ...current.filter((item) => item.documentId !== source.documentId),
      ]);
      setActiveDocumentId(source.documentId);
      setLastTopic(null);
      const detail =
        document.page_count === null
          ? `${document.extraction.section_count} sections`
          : `${document.page_count} pages`;
      const extractionWarning = document.warnings
        .filter((warning) =>
          ['scanned_pdf_no_text', 'ocr_unavailable', 'file_too_large'].includes(
            warning.code,
          ),
        )
        .map((warning) => warning.message)
        .filter(Boolean)
        .join('\n');
      push(
        assistantText(
          document.extraction.text_extractable
            ? `Added “${document.filename}” (${document.format.toUpperCase()} · ${detail} · ${formatSize(document.size_bytes)}). Ask me about a topic, or request a whole-material summary.`
            : `Added “${document.filename}”, but no extractable text was found.${extractionWarning ? `\n${extractionWarning}` : ' Scanned pages need OCR on the server before study material can be generated.'}`,
          document.extraction.text_extractable ? 'info' : 'warn',
        ),
      );
      const waitingImages = pendingImageFiles;
      if (waitingImages?.length) {
        setPendingImageFiles(null);
        await uploadImagesForDocument(waitingImages, source);
      }
    } catch (error) {
      if (mountedRef.current) {
        pushError(error, 'The upload failed. Please try again.', () => {
          void attachFile(file);
        });
      }
    } finally {
      finishRun();
    }
  };

  const pickAndAttach = async () => {
    if (busy || picking) {
      return;
    }
    setPicking(true);
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: [PDF, DOCX],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (result.canceled) {
        return;
      }
      const asset = result.assets?.[0];
      if (!asset) {
        return;
      }
      await attachFile({
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType,
        size: asset.size,
      });
    } catch (error) {
      pushError(error, 'The file picker could not be opened.');
    } finally {
      setPicking(false);
    }
  };

  const pickImagesInChat = async () => {
    const doc = activeDoc;
    if (busy || picking) {
      return;
    }
    setAttachmentMenuOpen(false);
    setPicking(true);
    try {
      const selection = await pickNoteImages(TOPIC_MAX_IMAGES);
      if (selection.status === 'denied') {
        push(assistantText('Allow photo access to attach images of your notes.', 'warn'));
        return;
      }
      if (selection.status === 'error') {
        push(assistantText('The photo library could not be opened.', 'warn'));
        return;
      }
      if (selection.status !== 'selected' || selection.assets.length === 0) {
        return;
      }
      const assets = selection.assets.filter((asset) => {
        const mime = asset.mimeType?.toLowerCase();
        const name = asset.fileName ?? asset.uri;
        return (
          mime === 'image/png' ||
          mime === 'image/jpeg' ||
          (!mime && /\.(png|jpe?g)$/i.test(name))
        );
      });
      if (assets.length === 0) {
        push(assistantText('Choose PNG or JPEG images of your notes.', 'warn'));
        return;
      }
      if (assets.length !== selection.assets.length) {
        push(assistantText('Only PNG and JPEG images can be attached. Unsupported images were skipped.', 'warn'));
      }
      const files: UploadFile[] = assets.map((asset, index) => {
        const name = asset.fileName || `study-note-${index + 1}.jpg`;
        return {
          uri: asset.uri,
          name,
          mimeType:
            asset.mimeType ?? (/\.png$/i.test(name) ? 'image/png' : 'image/jpeg'),
          size: asset.fileSize,
        };
      });
      push(
        makeMessage({
          role: 'user',
          kind: 'text',
          text: `Attached ${files.length} ${files.length === 1 ? 'image' : 'images'}: ${files.map((file) => file.name).join(', ')}`,
        }),
      );
      if (!doc) {
        setPendingImageFiles(files);
        push(
          assistantText(
            'Images are ready. Attach a PDF or DOCX with + so I can OCR the images and match topics to your source.',
          ),
        );
        return;
      }
      if (!beginRun()) {
        return;
      }
      try {
        await uploadImagesForDocument(files, doc);
      } finally {
        finishRun();
      }
    } catch {
      push(assistantText('The photo library could not be opened.', 'warn'));
    } finally {
      setPicking(false);
    }
  };

  const ensureTopicSource = async (doc: SourceDoc): Promise<string> => {
    const cached = topicSourceRef.current.get(doc.documentId);
    if (cached) {
      return cached;
    }
    if (doc.sourceId) {
      topicSourceRef.current.set(doc.documentId, doc.sourceId);
      return doc.sourceId;
    }
    const created = await createTopicSource(
      doc.documentId,
      [],
      CHAT_TOPIC_SOURCE_SEED,
    );
    topicSourceRef.current.set(doc.documentId, created.source_id);
    setSources((current) =>
      current.map((item) =>
        item.documentId === doc.documentId
          ? { ...item, sourceId: created.source_id }
          : item,
      ),
    );
    return created.source_id;
  };

  const generateTopicMaterial = async (
    topic: string,
    doc: SourceDoc,
    mode: TopicStudyMode,
    existingSourceId?: string,
  ) => {
    setPending({
      title:
        mode === 'source_passages'
          ? 'Collecting the original passages…'
          : mode === 'both'
            ? 'Building combined study material…'
            : 'Writing your topic summary…',
      detail: `“${topic}” grounded in ${doc.filename}`,
    });
    const sourceId = existingSourceId ?? (await ensureTopicSource(doc));
    const output = await createTopicStudyOutput(sourceId, mode, [topic]);
    if (!mountedRef.current) {
      return;
    }
    push(
      makeMessage({
        role: 'assistant',
        kind: 'material',
        topic,
        mode,
        output,
        retry: () => {
          void generateForTopic(topic, mode);
        },
      }),
    );
  };

  const runTopic = async (
    topic: string,
    doc: SourceDoc,
    generateMode: TopicStudyMode | null,
  ) => {
    if (!beginRun()) {
      return;
    }
    setPending({
      title: 'Looking through your material…',
      detail: `Searching for “${topic}” in ${doc.filename}`,
    });
    try {
      const sourceId = await ensureTopicSource(doc);
      const review = await reviewTopicSource(sourceId, [topic]);
      if (!mountedRef.current) {
        return;
      }
      const found = review.topics.some(
        (item) => item.status !== 'unmatched',
      );
      push(
        assistantText(
          found
            ? `I found “${topic}” in “${doc.filename}”. Here is the original wording and the closest references.`
            : `I could not match “${topic}” to a passage in “${doc.filename}”. Try a different phrase, or request a whole-material summary.`,
          found ? 'info' : 'warn',
        ),
      );
      push(
        makeMessage({
          role: 'assistant',
          kind: 'topic',
          topic,
          review,
          retry: () => {
            void runTopic(topic, doc, generateMode);
          },
        }),
      );
      setLastTopic(topic);
      if (generateMode && found) {
        await generateTopicMaterial(topic, doc, generateMode, sourceId);
      }
    } catch (error) {
      if (mountedRef.current) {
        pushError(
          error,
          `The topic “${topic}” could not be processed.`,
          () => {
            void runTopic(topic, doc, generateMode);
          },
        );
      }
    } finally {
      finishRun();
    }
  };

  const generateForTopic = async (topic: string, mode: TopicStudyMode) => {
    const doc = activeDoc;
    if (!doc) {
      push(
        assistantText(
          'Attach a PDF or DOCX first, then I can build study material.',
          'warn',
        ),
      );
      return;
    }
    if (!beginRun()) {
      return;
    }
    try {
      setLastTopic(topic);
      await generateTopicMaterial(topic, doc, mode);
    } catch (error) {
      if (mountedRef.current) {
        pushError(
          error,
          'The study material could not be generated.',
          () => {
            void generateForTopic(topic, mode);
          },
        );
      }
    } finally {
      finishRun();
    }
  };

  const refreshSummary = async (jobId: string) => {
    if (!beginRun()) {
      return;
    }
    setPending({ title: 'Checking your study job…' });
    try {
      let job = await getStudyJob(jobId);
      let attempts = 0;
      while (
        mountedRef.current &&
        !isTerminal(job.status) &&
        attempts < MAX_POLLS
      ) {
        setPending({
          title: 'Checking your study job…',
          detail: `${job.filename} · ${job.progress.processed_units} of ${job.progress.total_units} parts`,
        });
        await sleep(POLL_INTERVAL_MS);
        job = await getStudyJob(jobId);
        attempts += 1;
      }
      if (!mountedRef.current) {
        return;
      }
      push(
        makeMessage({
          role: 'assistant',
          kind: 'summary',
          job,
          refresh: () => {
            void refreshSummary(job.job_id);
          },
        }),
      );
    } catch (error) {
      if (mountedRef.current) {
        pushError(error, 'The study job could not be refreshed.', () => {
          void refreshSummary(jobId);
        });
      }
    } finally {
      finishRun();
    }
  };

  useEffect(() => {
    refreshSummaryRef.current = (jobId: string) => {
      void refreshSummary(jobId);
    };
  });

  const runWholeSummary = async (
    doc: SourceDoc,
    mode: ChatCommand['mode'] = 'ai_study_material',
  ) => {
    if (!beginRun()) {
      return;
    }
    setPending({
      title:
        mode === 'source_passages'
          ? 'Collecting the original material…'
          : 'Building a whole-material summary…',
      detail: doc.filename,
    });
    try {
      const studyMode: StudyMode =
        mode === 'source_passages'
          ? 'extracted_text_only'
          : mode === 'both'
            ? 'text_and_summaries'
            : 'summaries_only';
      let job = await startStudyJob(doc.documentId, studyMode);
      let attempts = 0;
      while (mountedRef.current && !isTerminal(job.status) && attempts < MAX_POLLS) {
        setPending({
          title: 'Building a whole-material summary…',
          detail: `${doc.filename} · ${job.progress.processed_units} of ${job.progress.total_units} parts`,
        });
        await sleep(POLL_INTERVAL_MS);
        job = await getStudyJob(job.job_id);
        attempts += 1;
      }
      if (!mountedRef.current) {
        return;
      }
      push(
        makeMessage({
          role: 'assistant',
          kind: 'summary',
          job,
          retry: () => {
            void runWholeSummary(doc, mode);
          },
          refresh: () => {
            void refreshSummary(job.job_id);
          },
        }),
      );
    } catch (error) {
      if (mountedRef.current) {
        pushError(
          error,
          'The whole-material summary could not be generated.',
          () => {
            void runWholeSummary(doc, mode);
          },
        );
      }
    } finally {
      finishRun();
    }
  };

  const shareExport = async (
    target: { jobId?: string; resultId?: string; filename: string },
    format: ExportFormat,
  ) => {
    const filename = `${safeStem(target.filename)}.${format}`;
    const uri = target.jobId
      ? await downloadStudyExport(target.jobId, format, filename)
      : await downloadTopicResultExport(target.resultId!, format, filename);
    if (!(await Sharing.isAvailableAsync())) {
      throw new ApiError('Sharing is not available on this device.', 'sharing_unavailable', 0);
    }
    await Sharing.shareAsync(uri, {
      mimeType: format === 'pdf' ? 'application/pdf' : DOCX_MIME,
      dialogTitle: 'Share study material',
    });
  };

  const exportLatestChatResult = async (format: ExportFormat | null) => {
    if (!format) {
      push(assistantText('Please specify PDF or Word for the export.'));
      return;
    }
    const latest = [...messages].reverse().find(
      (message) =>
        message.role === 'assistant' &&
        ((message.kind === 'summary' &&
          (message.job.status === 'completed' || message.job.status === 'partial')) ||
          (message.kind === 'material' &&
            message.output.result_id &&
            (message.output.status === 'completed' || message.output.status === 'partial'))),
    );
    if (!latest || latest.role !== 'assistant') {
      push(assistantText('There is no completed study output in this chat to export yet.'));
      return;
    }
    if (!beginRun()) {
      return;
    }
    const label = format === 'docx' ? 'Word' : 'PDF';
    setPending({ title: `Exporting your ${label} file…` });
    try {
      if (latest.kind === 'summary') {
        await shareExport({ jobId: latest.job.job_id, filename: latest.job.filename }, format);
      } else if (latest.kind === 'material' && latest.output.result_id) {
        await shareExport(
          {
            resultId: latest.output.result_id,
            filename: latest.output.document?.filename ?? latest.topic,
          },
          format,
        );
      }
      push(assistantText(`Your ${label} export is ready to share.`));
    } catch (error) {
      pushError(error, 'The export could not be downloaded. Please try again.');
    } finally {
      finishRun();
    }
  };

  const runRangeStudy = async (
    doc: SourceDoc,
    command: ChatCommand,
  ) => {
    if (!beginRun()) {
      return;
    }
    const selector =
      command.selector.type === 'pages'
        ? `pages ${command.selector.pages
            .map(([first, last]) => (first === last ? `${first}` : `${first}-${last}`))
            .join(', ')}`
        : command.selector.type === 'headings'
          ? `chapter ${command.selector.chapter ?? ''}`
          : command.selector.type === 'items'
            ? command.selector.items
                .map(([first, last]) => `questions ${first} to ${last}`)
                .join(', ')
            : '';
    setPending({
      title: `Reading ${selector}…`,
      detail: `Extracting original text from ${doc.filename}`,
    });
    try {
      const job = await startRangeStudy(doc.documentId, selector, command.mode);
      if (mountedRef.current) {
        push(
          makeMessage({
            role: 'assistant',
            kind: 'summary',
            job,
            retry: () => void runRangeStudy(doc, command),
          }),
        );
      }
      if (command.export_format) {
        const label = command.export_format === 'docx' ? 'Word' : 'PDF';
        setPending({ title: `Exporting your ${label} file…` });
        await shareExport({ jobId: job.job_id, filename: job.filename }, command.export_format);
        push(assistantText(`Your ${label} export is ready to share.`));
      }
    } catch (error) {
      if (mountedRef.current) {
        pushError(error, 'The selected source range could not be processed.', () => {
          void runRangeStudy(doc, command);
        });
      }
    } finally {
      finishRun();
    }
  };

  const handleSend = (
    raw: string,
    options?: { silentUser?: boolean },
  ) => {
    if (busy || picking) {
      return;
    }
    const text = raw.trim();
    if (!text) {
      return;
    }
    setInput('');
    if (!options?.silentUser) {
      push(makeMessage({ role: 'user', kind: 'text', text }));
    }
    setBusy(true);
    setPending({ title: 'Understanding your request…' });
    void (async () => {
      let command: ChatCommand;
      try {
        command = await requestChatCommand(text, {
          document_names: sources.map((source) => source.filename),
          page_count: activeDoc?.pageCount ?? null,
          doc_format: activeDoc?.format ?? null,
        });
        if (command.action === 'clarify') {
          const local = commandFromLocalIntent(text);
          if (local.action !== 'clarify') {
            command = local;
          }
        }
      } catch {
        command = commandFromLocalIntent(text);
      }

      const doc = activeDoc;
      if (command.action === 'help') {
        push(
          assistantText(
            doc
              ? 'Name a topic or page range, or ask for a study action. Use + to attach another PDF, DOCX, or note image.'
              : 'Attach a PDF, DOCX, or note image with +, then name a topic or ask for a page range or summary.',
          ),
        );
      } else if (command.action === 'clarify') {
        push(assistantText(command.question ?? 'Name a topic, page range, or study action.'));
      } else if (command.action === 'export') {
        await exportLatestChatResult(command.export_format);
      } else if (command.action === 'summarize_whole') {
        if (!doc) {
          queuedPromptRef.current = text;
          push(assistantText('Attach a PDF or DOCX first. I’ll continue this request when it is ready.'));
        } else {
          await runWholeSummary(doc, command.mode);
        }
      } else if (command.selector.type === 'topic' && command.selector.topic) {
        if (!doc) {
          queuedPromptRef.current = text;
          push(assistantText('Attach a PDF or DOCX first. I’ll continue this request when it is ready.'));
        } else {
          await runTopic(command.selector.topic, doc, command.mode);
        }
      } else if (
        command.selector.type === 'pages' ||
        command.selector.type === 'headings' ||
        command.selector.type === 'items'
      ) {
        if (!doc) {
          queuedPromptRef.current = text;
          push(assistantText('Attach a PDF or DOCX first. I’ll continue this request when it is ready.'));
        } else {
          await runRangeStudy(doc, command);
        }
      }
    })()
      .catch((error: unknown) => {
        if (mountedRef.current) {
          pushError(error, 'I could not understand that request. Try a topic or page range.');
        }
      })
      .finally(() => {
        setBusy(false);
        setPending(null);
      });
  };

  useEffect(() => {
    sendRef.current = (text, options) => {
      handleSend(text, options);
    };
  });

  useEffect(() => {
    if (!queuedPromptRef.current || busy || picking || !activeDoc) {
      return;
    }
    const queued = queuedPromptRef.current;
    queuedPromptRef.current = null;
    sendRef.current(queued, { silentUser: true });
  }, [activeDoc, busy, picking]);

  useEffect(() => {
    if (!initialPrompt || jobId || resultId) {
      return;
    }
    const timer = setTimeout(() => {
      if (initialPromptSentRef.current) {
        return;
      }
      initialPromptSentRef.current = true;
      sendRef.current(initialPrompt);
    }, 0);
    return () => clearTimeout(timer);
  }, [initialPrompt, jobId, resultId]);


  const quickGenerate = (mode: TopicStudyMode) => {
    if (lastTopic) {
      void generateForTopic(lastTopic, mode);
    }
  };

  return (
    <SafeAreaView style={styles.safe} edges={['top']}>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior="padding"
        keyboardVerticalOffset={0}>
        <View style={styles.topBar}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Open study history"
            accessibilityState={{ expanded: historyOpen }}
            onPress={() => {
              setHistoryOpen(true);
              void loadHistory();
            }}
            style={styles.topBarButton}>
            <Text style={styles.historyIcon}>☰</Text>
          </Pressable>
          <Text style={styles.brandTitle}>Study Assistant</Text>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Open settings"
            onPress={() => router.push('/settings')}
            style={styles.topBarButton}>
            <Text style={styles.settingsLabel}>Settings</Text>
          </Pressable>
        </View>
        {sources.length > 0 ? (
          <View style={styles.sourceBar}>
            <Text style={styles.sourceBarLabel}>MATERIAL</Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.sourceRow}
              keyboardShouldPersistTaps="handled">
              {sources.map((source) => {
                const active = source.documentId === activeDoc?.documentId;
                return (
                  <Pressable
                    key={source.documentId}
                    accessibilityRole="button"
                    accessibilityLabel={`${source.filename}${active ? ', active source' : ''}`}
                    onPress={() => {
                      if (source.documentId !== activeDoc?.documentId) {
                        setLastTopic(null);
                      }
                      setActiveDocumentId(source.documentId);
                    }}
                    style={[
                      styles.sourceCard,
                      active ? styles.sourceCardActive : null,
                      !source.available ? styles.sourceCardUnavailable : null,
                    ]}>
                    <Text
                      numberOfLines={1}
                      style={[
                        styles.sourceName,
                        active ? styles.sourceNameActive : null,
                      ]}>
                      {source.filename}
                    </Text>
                    <Text style={styles.sourceMeta}>
                      {source.format.toUpperCase()}
                      {source.pageCount ? ` · ${source.pageCount} pages` : ''}
                      {source.available ? '' : ' · expired'}
                    </Text>
                  </Pressable>
                );
              })}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Attach a PDF or DOCX"
                onPress={() => void pickAndAttach()}
                disabled={busy || picking}
                style={[
                  styles.attachChip,
                  busy || picking ? styles.disabled : null,
                ]}>
                {picking ? (
                  <ActivityIndicator size="small" color={palette.accent} />
                ) : (
                  <Text style={styles.attachLabel}>+ Attach</Text>
                )}
              </Pressable>
            </ScrollView>
          </View>
        ) : null}

        <ScrollView
          ref={listRef}
          style={styles.flex}
          contentContainerStyle={styles.messageList}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          onContentSizeChange={() =>
            listRef.current?.scrollToEnd({ animated: true })
          }>
          {messages.length === 0 && !pending ? (
            <Welcome />
          ) : null}

          {messages.map((message) => (
            <MessageBubble
              key={message.id}
              message={message}
              busy={busy}
              onGenerate={(topic, mode) => void generateForTopic(topic, mode)}
            />
          ))}

          {pending && <PendingBubble pending={pending} />}
        </ScrollView>

        <View style={styles.converterRow}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="File Converter, coming soon"
            accessibilityState={{ disabled: true }}
            disabled
            style={styles.converterButton}>
            <Text style={styles.converterLabel}>File Converter</Text>
            <Text style={styles.converterSoon}>Coming soon</Text>
          </Pressable>
        </View>
        <View
          style={[
            styles.composerArea,
            {
              paddingBottom: keyboardVisible
                ? spacing.xs
                : Math.max(insets.bottom, spacing.xs),
            },
          ]}>
          {lastTopic && (
            <>
              <View style={styles.modeHeader}>
                <Text style={styles.modeHeaderLabel}>STUDY MODE</Text>
                <Text style={styles.modeHeaderTopic} numberOfLines={1}>
                  {lastTopic}
                </Text>
              </View>
              <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={styles.quickRow}
                keyboardShouldPersistTaps="handled">
                {(['ai_study_material', 'both', 'source_passages'] as const).map(
                  (mode) => (
                    <Pressable
                      key={mode}
                      accessibilityRole="button"
                      disabled={busy}
                      onPress={() => quickGenerate(mode)}
                      style={[
                        styles.quickChip,
                        busy ? styles.disabled : null,
                      ]}>
                      <Text style={styles.quickChipText}>
                        {TOPIC_MODE_LABELS[mode]}
                      </Text>
                    </Pressable>
                  ),
                )}
              </ScrollView>
            </>
          )}
          <View style={styles.composer}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Attach a PDF, DOCX, or images"
              onPress={() => setAttachmentMenuOpen(true)}
              disabled={busy || picking}
              style={[
                styles.composerAttach,
                busy || picking ? styles.disabled : null,
              ]}>
              <Text style={styles.composerAttachText}>+</Text>
            </Pressable>
            <TextInput
              value={input}
              onChangeText={setInput}
              placeholder="Ask about your notes…"
              placeholderTextColor={palette.faint}
              style={styles.input}
              multiline
              scrollEnabled
              maxLength={CHAT_TOPIC_MAX_CHARS * 4}
              accessibilityLabel="Study request"
              textAlignVertical="top"
              onFocus={() => {
                requestAnimationFrame(() =>
                  listRef.current?.scrollToEnd({ animated: true }),
                );
              }}
            />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Send message"
              disabled={!input.trim() || busy}
              onPress={() => handleSend(input)}
              style={[
                styles.sendButton,
                !input.trim() || busy ? styles.sendButtonDisabled : null,
              ]}>
              <Text style={styles.sendLabel}>↑</Text>
            </Pressable>
          </View>
        </View>
      </KeyboardAvoidingView>

      <Modal
        visible={attachmentMenuOpen}
        transparent
        animationType="fade"
        statusBarTranslucent
        onRequestClose={() => setAttachmentMenuOpen(false)}>
        <View style={styles.modalRoot}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Close attachment options"
            onPress={() => setAttachmentMenuOpen(false)}
            style={styles.modalScrim}
          />
          <View style={styles.attachmentSheet}>
            <View style={styles.sheetHandle} />
            <Text style={styles.sheetTitle}>Add to your study</Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                setAttachmentMenuOpen(false);
                void pickAndAttach();
              }}
              style={styles.menuOption}>
              <Text style={styles.menuOptionIcon}>▤</Text>
              <View style={styles.menuOptionCopy}>
                <Text style={styles.menuOptionTitle}>Upload a document</Text>
                <Text style={styles.menuOptionDetail}>PDF or DOCX</Text>
              </View>
              <Text style={styles.menuArrow}>›</Text>
            </Pressable>
            <Pressable
              accessibilityRole="button"
              onPress={() => {
                void pickImagesInChat();
              }}
              style={styles.menuOption}>
              <Text style={styles.menuOptionIcon}>▧</Text>
              <View style={styles.menuOptionCopy}>
                <Text style={styles.menuOptionTitle}>
                  {activeDoc ? 'Choose note images' : 'Add photos of notes'}
                </Text>
                <Text style={styles.menuOptionDetail}>
                  {activeDoc
                    ? 'Read with OCR and search this document'
                    : 'Choose a source document, then select images'}
                </Text>
              </View>
              <Text style={styles.menuArrow}>›</Text>
            </Pressable>
            {activeDoc ? (
              <Pressable
                accessibilityRole="button"
                onPress={() => {
                  setAttachmentMenuOpen(false);
                  const sourceId = topicSourceRef.current.get(activeDoc.documentId);
                  router.push({
                    pathname: '/photo-notes',
                    params: {
                      documentId: activeDoc.documentId,
                      ...(sourceId ? { sourceId } : {}),
                    },
                  });
                }}
                style={styles.menuOption}>
                <Text style={styles.menuOptionIcon}>✎</Text>
                <View style={styles.menuOptionCopy}>
                  <Text style={styles.menuOptionTitle}>
                    Use camera or edit OCR
                  </Text>
                  <Text style={styles.menuOptionDetail}>
                    Add typed jottings or correct recognized text
                  </Text>
                </View>
                <Text style={styles.menuArrow}>›</Text>
              </Pressable>
            ) : null}
            <Pressable
              accessibilityRole="button"
              onPress={() => setAttachmentMenuOpen(false)}
              style={styles.cancelOption}>
              <Text style={styles.cancelLabel}>Cancel</Text>
            </Pressable>
          </View>
        </View>
      </Modal>

      <Modal
        visible={historyOpen}
        transparent
        animationType="fade"
        statusBarTranslucent
        onRequestClose={() => setHistoryOpen(false)}>
        <View style={styles.drawerRoot}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Close study history"
            onPress={() => setHistoryOpen(false)}
            style={styles.drawerScrim}
          />
          <SafeAreaView style={styles.historyPanel} edges={['top', 'bottom']}>
            <View style={styles.historyHeader}>
              <Text style={styles.historyTitle}>Your studies</Text>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Close history"
                onPress={() => setHistoryOpen(false)}
                style={styles.closeButton}>
                <Text style={styles.closeLabel}>×</Text>
              </Pressable>
            </View>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ disabled: busy || picking }}
              disabled={busy || picking}
              onPress={startNewChat}
              style={[
                styles.newChatButton,
                busy || picking ? styles.disabled : null,
              ]}>
              <Text style={styles.newChatPlus}>+</Text>
              <Text style={styles.newChatLabel}>New Chat</Text>
            </Pressable>
            <ScrollView
              style={styles.historyList}
              contentContainerStyle={styles.historyListContent}>
              {historyLoading ? (
                <View style={styles.historyState}>
                  <ActivityIndicator color={palette.accent} />
                  <Text style={styles.historyHint}>Loading study history...</Text>
                </View>
              ) : historyError ? (
                <View style={styles.historyState}>
                  <Text style={styles.historyHint}>{historyError}</Text>
                  <Pressable onPress={() => void loadHistory()}>
                    <Text style={styles.historyRetry}>Try again</Text>
                  </Pressable>
                </View>
              ) : recentSessions.length === 0 ? (
                <Text style={styles.historyHint}>Your saved studies will appear here.</Text>
              ) : (
                recentSessions.map((session) => {
                  const latest = latestOutput(session);
                  return (
                    <Pressable
                      key={session.key}
                      accessibilityRole="button"
                      accessibilityLabel={`Reopen ${session.filename}`}
                      onPress={() => openSavedSession(session)}
                      style={styles.historyItem}>
                      <Text style={styles.historyItemTitle} numberOfLines={2}>
                        {session.filename || 'Untitled material'}
                      </Text>
                      <Text style={styles.historyItemMeta} numberOfLines={1}>
                        {latest?.label ?? 'Study session'} ·{' '}
                        {new Date(session.lastActivity).toLocaleDateString()}
                      </Text>
                    </Pressable>
                  );
                })
              )}
            </ScrollView>
          </SafeAreaView>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

function Welcome() {
  return (
    <View style={styles.emptyState}>
      <Text style={styles.emptyTitle}>What would you like to study?</Text>
      <Text style={styles.emptyCopy}>Ask a question or attach your material.</Text>
    </View>
  );
}

function MessageBubble({
  message,
  busy,
  onGenerate,
}: {
  message: ChatMessage;
  busy: boolean;
  onGenerate: (topic: string, mode: TopicStudyMode) => void;
}) {
  if (message.role === 'user') {
    return (
      <View style={[styles.bubble, styles.userBubble]}>
        <Text style={styles.userText}>{message.text}</Text>
      </View>
    );
  }

  return (
    <View style={[styles.bubble, styles.assistantBubble]}>
      {message.kind === 'text' && (
        <Text
          style={[
            styles.body,
            message.tone === 'warn' ? styles.warnText : null,
          ]}>
          {message.text}
        </Text>
      )}

      {message.kind === 'error' && (
        <>
          <Text style={styles.errorText}>{message.text}</Text>
          {message.retry && (
            <Pressable
              accessibilityRole="button"
              onPress={message.retry}
              style={styles.retryButton}>
              <Text style={styles.retryLabel}>Try again</Text>
            </Pressable>
          )}
        </>
      )}

      {message.kind === 'summary' && (
        <SummaryCard
          job={message.job}
          onRetry={message.retry}
          onRefresh={message.refresh}
        />
      )}

      {message.kind === 'topic' && (
        <TopicCard
          review={message.review}
          busy={busy}
          onGenerate={onGenerate}
          onRetry={message.retry}
        />
      )}

      {message.kind === 'material' && (
        <MaterialCard
          topic={message.topic}
          output={message.output}
          busy={busy}
          onGenerate={onGenerate}
          onRetry={message.retry}
        />
      )}
    </View>
  );
}

function PendingBubble({ pending }: { pending: PendingState }) {
  return (
    <View
      accessibilityLiveRegion="polite"
      style={[styles.bubble, styles.assistantBubble, styles.pendingBubble]}>
      <ActivityIndicator size="small" color={palette.accent} />
      <View style={styles.pendingTextWrap}>
        <Text style={styles.bubbleTitle}>{pending.title}</Text>
        {pending.detail ? (
          <Text style={styles.meta}>{pending.detail}</Text>
        ) : null}
      </View>
    </View>
  );
}

function SummaryCard({
  job,
  onRetry,
  onRefresh,
}: {
  job: StudyJobResponse;
  onRetry?: () => void;
  onRefresh?: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const sectionItems = job.items.filter((item) => item.summary);
  const textOnly =
    sectionItems.length === 0 && job.items.some((item) => Boolean(item.text));
  const visibleItems = expanded ? sectionItems : sectionItems.slice(0, 3);
  const ready = job.status === 'completed' || job.status === 'partial';
  const cardTitle =
    job.mode === 'summaries_only'
      ? 'Whole-material summary'
      : job.mode === 'text_and_summaries'
        ? 'Whole-material study material'
        : 'Saved study job';

  return (
    <>
      <View style={styles.cardHeader}>
        <Text style={styles.bubbleTitle}>
          {cardTitle} · {job.filename}
        </Text>
        <Text
          style={[
            styles.badge,
            job.status === 'completed'
              ? styles.badgeOk
              : job.status === 'partial'
                ? styles.badgeWarn
                : job.status === 'failed'
                  ? styles.badgeError
                  : styles.badgeBusy,
          ]}>
          {STUDY_STATUS_LABELS[job.status]}
        </Text>
      </View>

      {!job.document_available && (
        <Text style={styles.warnText}>
          The original source file is no longer available, but this saved
          summary remains readable and exportable.
        </Text>
      )}

      {job.error && (
        <>
          <Text style={styles.errorText}>{job.error.message}</Text>
          {onRetry && (
            <Pressable
              accessibilityRole="button"
              onPress={onRetry}
              style={styles.retryButton}>
              <Text style={styles.retryLabel}>Try again</Text>
            </Pressable>
          )}
        </>
      )}

      {!ready && !job.error && (
        <>
          <Text style={styles.meta}>
            The server is still processing this job. You can keep chatting.
          </Text>
          {onRefresh && (
            <Pressable
              accessibilityRole="button"
              onPress={onRefresh}
              style={styles.retryButton}>
              <Text style={styles.retryLabel}>Check again</Text>
            </Pressable>
          )}
        </>
      )}

      {textOnly && (
        <Text style={styles.meta}>
          This saved job contains extracted text only. Ask for a whole-material
          summary to generate study material.
        </Text>
      )}

      {job.document_summary && (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Overall summary</Text>
          <Prose text={job.document_summary} />
        </View>
      )}

      {visibleItems.map((item) => (
        <View key={item.index} style={styles.section}>
          <Text style={styles.refs}>
            {item.source_refs.map(formatSourceRef).join(' · ') || 'Section'}
          </Text>
          {item.summary && <Prose text={item.summary} />}
          {item.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warnText}>
              {warning.message}
            </Text>
          ))}
        </View>
      ))}

      {sectionItems.length > 3 && (
        <Pressable
          accessibilityRole="button"
          onPress={() => setExpanded((current) => !current)}
          style={styles.moreButton}>
          <Text style={styles.moreLabel}>
            {expanded
              ? 'Show fewer sections'
              : `Show ${sectionItems.length - 3} more sections`}
          </Text>
        </Pressable>
      )}

      {job.warnings.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Notes</Text>
          {job.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warnText}>
              {warning.message}
            </Text>
          ))}
        </View>
      )}

      {ready && (
        <ExportActions
          jobId={job.job_id}
          filename={`${safeStem(job.filename)}-${job.mode}`}
        />
      )}
    </>
  );
}

function TopicCard({
  review,
  busy,
  onGenerate,
  onRetry,
}: {
  review: TopicReviewResponse;
  busy: boolean;
  onGenerate: (topic: string, mode: TopicStudyMode) => void;
  onRetry?: () => void;
}) {
  return (
    <>
      {review.topics.map((topic) => {
        const matched = topic.status === 'matched';
        return (
          <View key={topic.topic_id} style={styles.section}>
            <View style={styles.cardHeader}>
              <Text style={styles.topicName}>{topic.text}</Text>
              <Text
                style={[
                  styles.badge,
                  matched
                    ? styles.badgeOk
                    : topic.status === 'ambiguous'
                      ? styles.badgeWarn
                      : styles.badgeError,
                ]}>
                {matched
                  ? 'Found'
                  : topic.status === 'ambiguous'
                    ? 'Needs review'
                    : 'No match'}
              </Text>
            </View>
            {topic.matches.length > 0 ? (
              topic.matches.map((match) => (
                <View key={match.passage_id} style={styles.passage}>
                  <Text style={styles.refs}>
                    {formatSourceRef(match.anchor)}
                  </Text>
                  <Text style={styles.passageText}>
                    {match.excerpt || match.passage}
                  </Text>
                  {typeof match.score === 'number' && (
                    <Text style={styles.meta}>
                      Match confidence {Math.round(match.score * 100)}%
                    </Text>
                  )}
                </View>
              ))
            ) : (
              <Text style={styles.body}>
                No passage matched this phrase. Try the exact wording used in
                the material.
              </Text>
            )}
            {topic.warnings.map((warning, index) => (
              <Text key={`${warning.code}-${index}`} style={styles.warnText}>
                {warning.message}
              </Text>
            ))}
            {matched ? (
              <View style={styles.actionRow}>
                <ModeAction
                  label="Topic summary"
                  disabled={busy}
                  onPress={() => onGenerate(topic.text, 'ai_study_material')}
                />
                <ModeAction
                  label="Combined"
                  disabled={busy}
                  onPress={() => onGenerate(topic.text, 'both')}
                />
                <ModeAction
                  label="Original passages"
                  disabled={busy}
                  onPress={() => onGenerate(topic.text, 'source_passages')}
                />
              </View>
            ) : (
              <Text style={styles.meta}>
                Ask for another topic, or request a whole-material summary.
              </Text>
            )}
          </View>
        );
      })}

      {review.warnings.length > 0 && (
        <View style={styles.section}>
          {review.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warnText}>
              {warning.message}
            </Text>
          ))}
        </View>
      )}

      {review.source_material.status !== 'available' && (
        <Text style={styles.warnText}>
          Source matching is unavailable for this document.
        </Text>
      )}

      {!review.topics.length && onRetry && (
        <Pressable
          accessibilityRole="button"
          onPress={onRetry}
          style={styles.retryButton}>
          <Text style={styles.retryLabel}>Try again</Text>
        </Pressable>
      )}
    </>
  );
}

function MaterialCard({
  topic,
  output,
  busy,
  onGenerate,
  onRetry,
}: {
  topic: string;
  output: TopicStudyOutputResponse;
  busy: boolean;
  onGenerate: (topic: string, mode: TopicStudyMode) => void;
  onRetry?: () => void;
}) {
  const ready = output.status === 'completed' || output.status === 'partial';
  return (
    <>
      <View style={styles.cardHeader}>
        <Text style={styles.bubbleTitle}>
          {TOPIC_MODE_LABELS[output.mode]} · {topic}
        </Text>
        <Text
          style={[
            styles.badge,
            output.status === 'completed'
              ? styles.badgeOk
              : output.status === 'partial'
                ? styles.badgeWarn
                : styles.badgeError,
          ]}>
          {output.status === 'completed'
            ? 'Ready'
            : output.status === 'partial'
              ? 'Ready with notes'
              : 'Failed'}
        </Text>
      </View>

      {!output.document_available && (
        <Text style={styles.warnText}>
          The source document has expired. This material remains readable and
          exportable.
        </Text>
      )}

      {output.error && (
        <>
          <Text style={styles.errorText}>{output.error.message}</Text>
          {onRetry && (
            <Pressable
              accessibilityRole="button"
              onPress={onRetry}
              style={styles.retryButton}>
              <Text style={styles.retryLabel}>Try again</Text>
            </Pressable>
          )}
        </>
      )}

      {output.topics.map((item) => (
        <View key={item.topic_id} style={styles.section}>
          {item.source_passages.length > 0 && (
            <>
              <Text style={styles.sectionLabel}>Original source passages</Text>
              {item.source_passages.map((passage) => (
                <View key={passage.passage_id} style={styles.passage}>
                  <Text style={styles.refs}>
                    {formatSourceRef(passage.anchor)}
                  </Text>
                  <Text style={styles.passageText}>
                    {passage.excerpt || passage.passage}
                  </Text>
                </View>
              ))}
            </>
          )}

          {item.study_material?.source_grounded && (
            <View style={styles.section}>
              <Text style={styles.sectionLabel}>
                AI study material (source-grounded)
              </Text>
              <Prose text={item.study_material.source_grounded.text} />
              {item.study_material.source_grounded.basis_refs.length > 0 && (
                <Text style={styles.meta}>
                  Based on:{' '}
                  {item.study_material.source_grounded.basis_refs
                    .map(formatSourceRef)
                    .join(' · ')}
                </Text>
              )}
            </View>
          )}

          {item.study_material?.beyond_source && (
            <View style={styles.section}>
              <Text style={styles.meta}>
                Optional background · not grounded in the uploaded material
              </Text>
              <Prose text={item.study_material.beyond_source.text} />
            </View>
          )}

          {item.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warnText}>
              {warning.message}
            </Text>
          ))}
        </View>
      ))}

      {output.warnings.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionLabel}>Notes</Text>
          {output.warnings.map((warning, index) => (
            <Text key={`${warning.code}-${index}`} style={styles.warnText}>
              {warning.message}
            </Text>
          ))}
        </View>
      )}

      {output.result_id && ready && (
        <ExportActions
          resultId={output.result_id}
          filename={`${safeStem(output.document?.filename)}-${output.mode}`}
        />
      )}

      {ready && (
        <View style={styles.actionRow}>
          <ModeAction
            label={
              output.mode === 'ai_study_material'
                ? 'Refresh summary'
                : 'Generate summary'
            }
            disabled={busy}
            onPress={() => onGenerate(topic, 'ai_study_material')}
          />
          <ModeAction
            label="Combined"
            disabled={busy}
            onPress={() => onGenerate(topic, 'both')}
          />
          <ModeAction
            label="Passages"
            disabled={busy}
            onPress={() => onGenerate(topic, 'source_passages')}
          />
        </View>
      )}
    </>
  );
}

function ModeAction({
  label,
  disabled,
  onPress,
}: {
  label: string;
  disabled: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={disabled}
      onPress={onPress}
      style={[styles.modeAction, disabled ? styles.disabled : null]}>
      <Text style={styles.modeActionText}>{label}</Text>
    </Pressable>
  );
}

function ExportActions({
  jobId,
  resultId,
  filename,
}: {
  jobId?: string;
  resultId?: string;
  filename: string;
}) {
  const [exporting, setExporting] = useState<ExportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runExport = async (format: ExportFormat) => {
    setError(null);
    setExporting(format);
    try {
      const target = `${filename}.${format}`;
      const uri = jobId
        ? await downloadStudyExport(jobId, format, target)
        : await downloadTopicResultExport(resultId!, format, target);
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, {
          mimeType: format === 'pdf' ? 'application/pdf' : DOCX_MIME,
          dialogTitle: 'Share study material',
        });
      } else {
        setError('Sharing is not available on this device.');
      }
    } catch (exportError) {
      setError(
        exportError instanceof ApiError
          ? exportError.message
          : 'The export could not be downloaded. Please try again.',
      );
    } finally {
      setExporting(null);
    }
  };

  return (
    <View style={styles.section}>
      <Text style={styles.sectionLabel}>Export</Text>
      <View style={styles.actionRow}>
        {(['pdf', 'docx'] as const).map((format) => (
          <ModeAction
            key={format}
            label={
              exporting === format
                ? 'Exporting…'
                : format === 'pdf'
                  ? 'Export PDF'
                  : 'Export DOCX'
            }
            disabled={exporting !== null}
            onPress={() => void runExport(format)}
          />
        ))}
      </View>
      {error && <Text style={styles.errorText}>{error}</Text>}
    </View>
  );
}

function Prose({ text }: { text: string }) {
  return (
    <View>
      {text.split('\n').map((line, index) => {
        if (!line.trim()) {
          return <View key={`gap-${index}`} style={styles.gap} />;
        }
        if (line.startsWith('- ') || line.startsWith('• ')) {
          return (
            <View key={index} style={styles.bulletRow}>
              <Text style={styles.bulletDot}>•</Text>
              <Text style={[styles.body, styles.bulletText]}>
                {line.slice(2)}
              </Text>
            </View>
          );
        }
        return (
          <Text key={index} style={styles.body}>
            {line}
          </Text>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: palette.canvas,
  },
  topBar: {
    minHeight: 54,
    paddingHorizontal: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    borderBottomWidth: 1,
    borderBottomColor: palette.line,
    backgroundColor: palette.canvas,
  },
  topBarButton: {
    minWidth: 48,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radii.md,
  },
  historyIcon: {
    color: palette.inkSoft,
    fontSize: 21,
  },
  brandTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 17,
    fontWeight: '600',
  },
  settingsLabel: {
    color: palette.muted,
    fontSize: 12,
    fontWeight: '600',
  },
  flex: {
    flex: 1,
  },
  sourceBar: {
    backgroundColor: palette.canvas,
    borderBottomWidth: 1,
    borderBottomColor: palette.line,
    paddingTop: spacing.sm,
  },
  sourceBarLabel: {
    color: palette.sage,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1.4,
    paddingHorizontal: spacing.lg,
    marginBottom: 6,
  },
  sourceRow: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.sm + 2,
    gap: spacing.sm,
    alignItems: 'center',
  },
  sourceCard: {
    maxWidth: 200,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radii.md,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.line,
  },
  sourceCardActive: {
    backgroundColor: palette.accentSoft,
    borderColor: palette.accent,
  },
  sourceCardUnavailable: {
    opacity: 0.55,
  },
  sourceName: {
    color: palette.inkSoft,
    fontSize: 13,
    fontWeight: '600',
  },
  sourceNameActive: {
    color: palette.accentInk,
  },
  sourceMeta: {
    color: palette.muted,
    fontSize: 10,
    marginTop: 2,
  },
  attachChip: {
    minWidth: 82,
    minHeight: 44,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: palette.lineStrong,
    alignItems: 'center',
    justifyContent: 'center',
  },
  attachLabel: {
    color: palette.accent,
    fontSize: 12,
    fontWeight: '700',
  },
  messageList: {
    padding: spacing.lg,
    gap: spacing.lg,
    paddingBottom: spacing.xl,
    flexGrow: 1,
  },
  bubble: {
    maxWidth: '94%',
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
  },
  assistantBubble: {
    alignSelf: 'flex-start',
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.line,
    borderBottomLeftRadius: 6,
  },
  userBubble: {
    alignSelf: 'flex-end',
    backgroundColor: palette.accent,
    borderBottomRightRadius: 6,
    maxWidth: '86%',
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.md,
  },
  userText: {
    color: palette.surface,
    fontSize: 15,
    lineHeight: 22,
  },
  bubbleTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 18,
    lineHeight: 24,
    fontWeight: '600',
  },
  body: {
    color: palette.inkSoft,
    fontSize: 14,
    lineHeight: 22,
  },
  meta: {
    color: palette.muted,
    fontSize: 11,
    lineHeight: 17,
  },
  warnText: {
    color: palette.amber,
    fontSize: 12,
    lineHeight: 18,
  },
  errorText: {
    color: palette.danger,
    fontSize: 12,
    lineHeight: 18,
  },
  emptyState: {
    flex: 1,
    minHeight: 280,
    justifyContent: 'center',
    alignItems: 'center',
    paddingHorizontal: spacing.xl,
    gap: spacing.sm,
  },
  emptyTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 23,
    lineHeight: 30,
    fontWeight: '600',
    textAlign: 'center',
  },
  emptyCopy: {
    color: palette.muted,
    fontSize: 13,
    lineHeight: 20,
    textAlign: 'center',
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  badge: {
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 3,
    fontSize: 10,
    fontWeight: '700',
    overflow: 'hidden',
  },
  badgeOk: {
    backgroundColor: palette.accentSoft,
    color: palette.accentInk,
  },
  badgeWarn: {
    backgroundColor: palette.amberSoft,
    color: palette.amber,
  },
  badgeError: {
    backgroundColor: palette.dangerSoft,
    color: palette.danger,
  },
  badgeBusy: {
    backgroundColor: palette.canvasDeep,
    color: palette.inkSoft,
  },
  section: {
    gap: 6,
  },
  sectionLabel: {
    color: palette.sage,
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 1.2,
  },
  topicName: {
    flex: 1,
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 18,
    lineHeight: 24,
    fontWeight: '600',
  },
  refs: {
    color: palette.sage,
    fontSize: 10,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.8,
  },
  passage: {
    gap: 4,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: palette.line,
  },
  passageText: {
    color: palette.inkSoft,
    fontSize: 13,
    lineHeight: 20,
  },
  actionRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  modeAction: {
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radii.sm,
    backgroundColor: 'transparent',
    borderWidth: 1,
    borderColor: palette.lineStrong,
  },
  modeActionText: {
    color: palette.accentInk,
    fontSize: 12,
    fontWeight: '700',
  },
  quickRow: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.sm,
    gap: spacing.sm,
    alignItems: 'center',
  },
  modeHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: 6,
  },
  modeHeaderLabel: {
    color: palette.sage,
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1.2,
  },
  modeHeaderTopic: {
    flex: 1,
    color: palette.inkSoft,
    fontSize: 11,
    fontWeight: '600',
  },
  quickChip: {
    minHeight: 40,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    borderRadius: radii.pill,
    backgroundColor: palette.surface,
    borderWidth: 1,
    borderColor: palette.line,
  },
  quickChipText: {
    color: palette.accentInk,
    fontSize: 11,
    fontWeight: '700',
  },
  composerArea: {
    backgroundColor: palette.surface,
    borderTopWidth: 1,
    borderTopColor: palette.line,
  },
  converterRow: {
    minHeight: 30,
    justifyContent: 'flex-end',
    paddingHorizontal: spacing.md,
    backgroundColor: palette.canvas,
  },
  converterButton: {
    minHeight: 28,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.sm,
    borderTopLeftRadius: radii.sm,
    borderTopRightRadius: radii.sm,
    borderWidth: 1,
    borderBottomWidth: 0,
    borderColor: palette.line,
    backgroundColor: palette.surface,
  },
  converterLabel: {
    color: palette.accentInk,
    fontSize: 10,
    fontWeight: '700',
  },
  converterSoon: {
    color: palette.muted,
    fontSize: 9,
  },
  modalRoot: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'transparent',
  },
  modalScrim: {
    ...StyleSheet.absoluteFill,
    backgroundColor: 'rgba(18, 29, 22, 0.38)',
  },
  attachmentSheet: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.md,
    paddingBottom: spacing.xl,
    borderTopLeftRadius: radii.xl,
    borderTopRightRadius: radii.xl,
    backgroundColor: palette.surface,
    gap: spacing.sm,
  },
  sheetHandle: {
    width: 38,
    height: 4,
    borderRadius: 2,
    alignSelf: 'center',
    backgroundColor: palette.lineStrong,
    marginBottom: spacing.xs,
  },
  sheetTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 19,
    fontWeight: '600',
    marginBottom: spacing.xs,
  },
  menuOption: {
    minHeight: 62,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
    backgroundColor: palette.canvas,
  },
  menuOptionIcon: {
    color: palette.accent,
    fontSize: 22,
  },
  menuOptionCopy: {
    flex: 1,
    gap: 2,
  },
  menuOptionTitle: {
    color: palette.ink,
    fontSize: 14,
    fontWeight: '600',
  },
  menuOptionDetail: {
    color: palette.muted,
    fontSize: 11,
    lineHeight: 16,
  },
  menuArrow: {
    color: palette.sage,
    fontSize: 22,
  },
  cancelOption: {
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.xs,
  },
  cancelLabel: {
    color: palette.muted,
    fontSize: 13,
    fontWeight: '600',
  },
  drawerRoot: {
    flex: 1,
    flexDirection: 'row',
    backgroundColor: 'transparent',
  },
  drawerScrim: {
    ...StyleSheet.absoluteFill,
    backgroundColor: 'rgba(18, 29, 22, 0.38)',
  },
  historyPanel: {
    width: '84%',
    maxWidth: 360,
    height: '100%',
    backgroundColor: palette.canvas,
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
    borderTopRightRadius: radii.lg,
    borderBottomRightRadius: radii.lg,
  },
  historyHeader: {
    minHeight: 52,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: spacing.md,
  },
  historyTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 20,
    fontWeight: '600',
  },
  closeButton: {
    width: 44,
    height: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  closeLabel: {
    color: palette.muted,
    fontSize: 27,
    lineHeight: 30,
  },
  newChatButton: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    borderRadius: radii.md,
    backgroundColor: palette.accentSoft,
  },
  newChatPlus: {
    color: palette.accent,
    fontSize: 21,
  },
  newChatLabel: {
    color: palette.accentInk,
    fontSize: 13,
    fontWeight: '700',
  },
  historyList: {
    flex: 1,
    marginTop: spacing.lg,
  },
  historyListContent: {
    gap: spacing.xs,
    paddingBottom: spacing.xl,
  },
  historyState: {
    alignItems: 'center',
    gap: spacing.md,
    paddingVertical: spacing.xl,
  },
  historyHint: {
    color: palette.muted,
    fontSize: 13,
    lineHeight: 20,
    textAlign: 'center',
    paddingVertical: spacing.md,
  },
  historyRetry: {
    color: palette.accent,
    fontSize: 13,
    fontWeight: '700',
  },
  historyItem: {
    minHeight: 62,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: palette.line,
    gap: 3,
  },
  historyItemTitle: {
    color: palette.inkSoft,
    fontSize: 13,
    fontWeight: '600',
    lineHeight: 18,
  },
  historyItemMeta: {
    color: palette.muted,
    fontSize: 10,
  },
  composer: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: spacing.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  composerAttach: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.accentSoft,
  },
  composerAttachText: {
    color: palette.accent,
    fontSize: 24,
    lineHeight: 26,
  },
  input: {
    flex: 1,
    minWidth: 0,
    minHeight: 44,
    maxHeight: 120,
    paddingHorizontal: spacing.lg,
    paddingVertical: 11,
    borderRadius: 22,
    backgroundColor: palette.canvas,
    borderWidth: 1,
    borderColor: palette.line,
    color: palette.ink,
    fontSize: 15,
    lineHeight: 22,
  },
  sendButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: palette.accent,
  },
  sendButtonDisabled: {
    backgroundColor: palette.lineStrong,
  },
  sendLabel: {
    color: palette.surface,
    fontSize: 21,
    lineHeight: 23,
    fontWeight: '600',
  },
  pendingBubble: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
  },
  pendingTextWrap: {
    flex: 1,
    gap: 2,
  },
  retryButton: {
    alignSelf: 'flex-start',
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
    borderRadius: radii.sm,
    backgroundColor: palette.dangerSoft,
  },
  retryLabel: {
    color: palette.danger,
    fontSize: 12,
    fontWeight: '700',
  },
  moreButton: {
    alignSelf: 'flex-start',
    paddingVertical: spacing.xs,
    minHeight: 40,
    justifyContent: 'center',
  },
  moreLabel: {
    color: palette.accent,
    fontSize: 12,
    fontWeight: '700',
  },
  gap: {
    height: 6,
  },
  bulletRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    marginBottom: 2,
  },
  bulletDot: {
    color: palette.accent,
    fontSize: 14,
    lineHeight: 21,
    marginRight: 6,
  },
  bulletText: {
    flex: 1,
  },
  disabled: {
    opacity: 0.5,
  },
});
