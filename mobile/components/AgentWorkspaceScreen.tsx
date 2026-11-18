import * as DocumentPicker from 'expo-document-picker';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
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
  AGENT_MAX_ATTACHMENTS,
  AGENT_MAX_MESSAGE_CHARS,
  ApiError,
  listStudyJobs,
  listTopicResults,
  requestAgentChat,
  type AgentAttachment,
  type AgentBlock,
  type AgentExportFormat,
  type AgentPassageResult,
  type UploadFile,
} from '@/services/apiClient';
import { presentAgentBlock } from '@/services/agentPresentation';
import {
  agentFailureMessage,
  agentMessageText,
  agentResponseWarnings,
  AgentExportError,
  conversationIdAfterAgentError,
  exportAgentResult,
  isExpiredAgentConversation,
  prepareAgentResultOcr,
  loadAgentPassages,
  shareAgentFile,
  uploadAgentDocument,
  uploadAgentImages,
} from '@/services/agentWorkflow';
import { pickNoteImages } from '@/services/imagePicker';
import { ocrProgressLabel } from '@/services/ocrProgressLabel';
import {
  groupRecentSessions,
  latestOutput,
  type RecentStudySession,
} from '@/services/recentSessions';
import { TOPIC_MAX_IMAGES } from '@/services/topicWorkflow';
import { displayFont, palette, radii, spacing } from '@/theme';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  tone?: 'warn';
  blocks?: AgentBlock[];
  warnings?: string[];
};

type AgentWorkspaceScreenProps = {
  onAgentDisabled: (message: string) => void;
};

const PDF = 'application/pdf';
const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

export default function AgentWorkspaceScreen({
  onAgentDisabled,
}: AgentWorkspaceScreenProps) {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [attachments, setAttachments] = useState<AgentAttachment[]>([]);
  const [activeAttachmentId, setActiveAttachmentId] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadStatus, setUploadStatus] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [recentSessions, setRecentSessions] = useState<RecentStudySession[]>([]);
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const list = useRef<ScrollView>(null);
  const nextId = useRef(0);
  const attachmentOperation = useRef(false);

  const addMessage = (message: Omit<Message, 'id'>) => {
    nextId.current += 1;
    setMessages((current) => [
      ...current,
      { ...message, id: String(nextId.current) },
    ]);
  };

  const activeAttachment =
    attachments.find((item) => item.id === activeAttachmentId) ??
    attachments[attachments.length - 1] ??
    null;
  const imageSource =
    (activeAttachment?.kind === 'pdf' || activeAttachment?.kind === 'docx'
      ? activeAttachment
      : null) ??
    [...attachments]
      .reverse()
      .find((item) => item.kind === 'pdf' || item.kind === 'docx') ??
    null;

  const loadHistory = async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const [jobs, results] = await Promise.all([
        listStudyJobs(50),
        listTopicResults(50),
      ]);
      setRecentSessions(groupRecentSessions(jobs, results));
    } catch (cause) {
      setHistoryError(
        cause instanceof ApiError
          ? cause.message
          : 'Study history could not be loaded.',
      );
    } finally {
      setHistoryLoading(false);
    }
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

  const startNewChat = () => {
    setHistoryOpen(false);
    setAttachmentMenuOpen(false);
    setMessages([]);
    setInput('');
    setAttachments([]);
    setActiveAttachmentId(null);
    setConversationId(null);
    setBusy(false);
    setUploadStatus('');
    setError(null);
    router.replace('/');
  };

  const attachDocument = async () => {
    if (attachmentOperation.current || busy) {
      return;
    }
    if (attachments.length >= AGENT_MAX_ATTACHMENTS) {
      setError(`Attach at most ${AGENT_MAX_ATTACHMENTS} items.`);
      return;
    }
    attachmentOperation.current = true;
    setUploadStatus('Choose a file…');
    setError(null);
    try {
      const selected = await DocumentPicker.getDocumentAsync({
        type: [PDF, DOCX],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (selected.canceled || !selected.assets[0]) {
        return;
      }
      const asset = selected.assets[0];
      setUploadStatus(`Uploading ${asset.name}…`);
      const attached = await uploadAgentDocument(
        {
          uri: asset.uri,
          name: asset.name,
          mimeType: asset.mimeType,
          size: asset.size,
        },
        (progress) => {
          const total = progress.progress.total_units;
          const done = progress.progress.processed_units;
          setUploadStatus(
            total > 0
              ? `Preparing ${asset.name}… ${done} of ${total}`
              : `Preparing ${asset.name}…`,
          );
        },
      );
      setAttachments((current) => [
        ...current.filter((item) => item.id !== attached.id),
        attached,
      ]);
      setActiveAttachmentId(attached.id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'The file could not be attached.',
      );
    } finally {
      attachmentOperation.current = false;
      setUploadStatus('');
    }
  };

  const attachImages = async () => {
    if (attachmentOperation.current || busy) {
      return;
    }
    if (attachments.length >= AGENT_MAX_ATTACHMENTS) {
      setError(`Attach at most ${AGENT_MAX_ATTACHMENTS} items.`);
      return;
    }
    if (!imageSource) {
      setError('Attach a PDF or DOCX before adding photos of your notes.');
      return;
    }
    attachmentOperation.current = true;
    setUploadStatus('Choose images…');
    setError(null);
    try {
      const selected = await pickNoteImages(TOPIC_MAX_IMAGES);
      if (selected.status !== 'selected' || selected.assets.length === 0) {
        if (selected.status === 'denied') {
          setError('Allow photo access to attach images.');
        }
        if (selected.status === 'error') {
          setError('The photo library could not be opened.');
        }
        return;
      }
      setUploadStatus('Uploading images…');
      const files: UploadFile[] = selected.assets.map((asset, index) => ({
        uri: asset.uri,
        name: asset.fileName || `image-${index + 1}.jpg`,
        mimeType: asset.mimeType,
        size: asset.fileSize,
      }));
      const attached = await uploadAgentImages(files);
      setAttachments((current) => [
        ...current.filter((item) => item.id !== attached.id),
        attached,
      ]);
      setActiveAttachmentId(attached.id);
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'The images could not be attached.',
      );
    } finally {
      attachmentOperation.current = false;
      setUploadStatus('');
    }
  };

  const send = async (override?: string) => {
    const message = agentMessageText(override ?? input);
    if (!message || busy) {
      return;
    }
    if (Array.from(message).length > AGENT_MAX_MESSAGE_CHARS) {
      setError(
        `Messages must be ${AGENT_MAX_MESSAGE_CHARS.toLocaleString()} characters or fewer.`,
      );
      return;
    }
    setInput('');
    setError(null);
    addMessage({ role: 'user', text: message });
    setBusy(true);
    try {
      const response = await requestAgentChat(
        message,
        conversationId,
        attachments,
      );
      setConversationId(response.conversation_id);
      addMessage({
        role: 'assistant',
        text: response.reply,
        blocks: response.blocks,
        warnings: agentResponseWarnings(response.status, response.warnings),
      });
    } catch (cause) {
      if (cause instanceof ApiError && cause.code === 'agent_disabled') {
        onAgentDisabled(
          'The study agent is not enabled. Showing the previous workspace.',
        );
        return;
      }
      const expiredConversation = isExpiredAgentConversation(cause);
      setConversationId(conversationIdAfterAgentError(conversationId, cause));
      addMessage({
        role: 'assistant',
        text: expiredConversation
          ? 'This conversation expired. Send another message to start a new one.'
          : agentFailureMessage(cause),
        tone: 'warn',
      });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    list.current?.scrollToEnd({ animated: true });
  }, [messages, busy, uploadStatus]);

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

        {attachments.length > 0 ? (
          <View style={styles.sourceBar}>
            <Text style={styles.sourceBarLabel}>MATERIAL</Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.sourceRow}
              keyboardShouldPersistTaps="handled">
              {attachments.map((item) => {
                const active = item.id === activeAttachment?.id;
                return (
                  <Pressable
                    key={item.id}
                    accessibilityRole="button"
                    accessibilityLabel={`${item.name}${active ? ', active source' : ''}`}
                    onPress={() => setActiveAttachmentId(item.id)}
                    style={[
                      styles.sourceCard,
                      active ? styles.sourceCardActive : null,
                    ]}>
                    <Text
                      numberOfLines={1}
                      style={[
                        styles.sourceName,
                        active ? styles.sourceNameActive : null,
                      ]}>
                      {item.name}
                    </Text>
                    <Text style={styles.sourceMeta}>
                      {item.kind.toUpperCase()}
                    </Text>
                  </Pressable>
                );
              })}
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Attach a PDF, DOCX, or images"
                onPress={() => setAttachmentMenuOpen(true)}
                disabled={busy || !!uploadStatus}
                style={[
                  styles.attachChip,
                  busy || uploadStatus ? styles.disabled : null,
                ]}>
                {uploadStatus ? (
                  <ActivityIndicator size="small" color={palette.accent} />
                ) : (
                  <Text style={styles.attachLabel}>+ Attach</Text>
                )}
              </Pressable>
            </ScrollView>
          </View>
        ) : null}

        <ScrollView
          ref={list}
          style={styles.flex}
          contentContainerStyle={styles.messageList}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
          onContentSizeChange={() =>
            list.current?.scrollToEnd({ animated: true })
          }>
          {messages.length === 0 && !busy && !uploadStatus ? (
            <View style={styles.emptyState}>
              <Text style={styles.emptyTitle}>
                What would you like to study?
              </Text>
              <Text style={styles.emptyCopy}>
                Ask a question or attach your material.
              </Text>
            </View>
          ) : null}

          {messages.map((message) => (
            <View
              key={message.id}
              style={[
                styles.bubble,
                message.role === 'user'
                  ? styles.userBubble
                  : styles.assistantBubble,
              ]}>
              {!!message.text && (
                <Text
                  style={
                    message.role === 'user'
                      ? styles.userText
                      : message.tone === 'warn'
                        ? styles.warnText
                        : styles.body
                  }>
                  {message.text}
                </Text>
              )}
              {message.warnings?.map((warning, index) => (
                <Text
                  key={`${message.id}-warning-${index}`}
                  style={styles.warnText}>
                  {warning}
                </Text>
              ))}
              {message.blocks?.map((block, index) => (
                <RenderedBlock
                  key={`${message.id}-block-${index}`}
                  block={block}
                  onChoice={(choice) => void send(choice)}
                  onError={setError}
                />
              ))}
            </View>
          ))}

          {(busy || !!uploadStatus) && (
            <View
              accessibilityLiveRegion="polite"
              style={[
                styles.bubble,
                styles.assistantBubble,
                styles.pendingBubble,
              ]}>
              <ActivityIndicator size="small" color={palette.accent} />
              <View style={styles.pendingTextWrap}>
                <Text style={styles.bubbleTitle}>
                  {uploadStatus || 'Working…'}
                </Text>
              </View>
            </View>
          )}
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
          {!!error && <Text style={styles.errorText}>{error}</Text>}
          <View style={styles.composer}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Attach a PDF, DOCX, or images"
              onPress={() => setAttachmentMenuOpen(true)}
              disabled={busy || !!uploadStatus}
              style={[
                styles.composerAttach,
                busy || uploadStatus ? styles.disabled : null,
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
              maxLength={AGENT_MAX_MESSAGE_CHARS}
              accessibilityLabel="Study request"
              textAlignVertical="top"
              onFocus={() => {
                requestAnimationFrame(() =>
                  list.current?.scrollToEnd({ animated: true }),
                );
              }}
            />
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Send message"
              disabled={!input.trim() || busy}
              onPress={() => void send()}
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
                void attachDocument();
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
                setAttachmentMenuOpen(false);
                void attachImages();
              }}
              style={styles.menuOption}>
              <Text style={styles.menuOptionIcon}>▧</Text>
              <View style={styles.menuOptionCopy}>
                <Text style={styles.menuOptionTitle}>Add photos of notes</Text>
                <Text style={styles.menuOptionDetail}>
                  {imageSource
                    ? 'Read with OCR and search this document'
                    : 'Choose a source document, then select images'}
                </Text>
              </View>
              <Text style={styles.menuArrow}>›</Text>
            </Pressable>
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
              accessibilityState={{ disabled: busy }}
              disabled={busy}
              onPress={startNewChat}
              style={[
                styles.newChatButton,
                busy ? styles.disabled : null,
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
                  <Text style={styles.historyHint}>
                    Loading study history...
                  </Text>
                </View>
              ) : historyError ? (
                <View style={styles.historyState}>
                  <Text style={styles.historyHint}>{historyError}</Text>
                  <Pressable onPress={() => void loadHistory()}>
                    <Text style={styles.historyRetry}>Try again</Text>
                  </Pressable>
                </View>
              ) : recentSessions.length === 0 ? (
                <Text style={styles.historyHint}>
                  Your saved studies will appear here.
                </Text>
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

function RenderedBlock({
  block,
  onChoice,
  onError,
}: {
  block: AgentBlock;
  onChoice: (choice: string) => void;
  onError: (message: string) => void;
}) {
  const presentation = presentAgentBlock(block);
  const [passages, setPassages] = useState<AgentPassageResult | null>(null);
  const [pageImages, setPageImages] = useState<Record<number, string>>({});
  const [opening, setOpening] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exporting, setExporting] = useState<AgentExportFormat | null>(null);
  const [exportStatus, setExportStatus] = useState('');
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState<AgentExportFormat | null>(null);
  const [ocrPendingPages, setOcrPendingPages] = useState<number[]>([]);
  const [ocrFailedPages, setOcrFailedPages] = useState<number[]>([]);

  useEffect(() => {
    if (block.type !== 'passages' || !block.result_id) {
      return;
    }
    let active = true;
    void loadAgentPassages(block.result_id)
      .then(({ result, pageImages: loadedImages }) => {
        if (!active) {
          return;
        }
        setPassages(result);
        setPageImages(loadedImages);
      })
      .catch((cause) => {
        if (active) {
          onError(
            cause instanceof Error
              ? cause.message
              : 'Passages could not be loaded.',
          );
        }
      });
    return () => {
      active = false;
    };
  }, [block.result_id, block.type, onError]);

  const startExport = async (
    format: AgentExportFormat,
    allowIncompleteOcr = false,
  ) => {
    if (!block.result_id || exporting) {
      return;
    }
    setExporting(format);
    setExportFormat(format);
    setExportError(null);
    setOcrPendingPages([]);
    setOcrFailedPages([]);
    setExportOpen(true);
    setExportStatus('Preparing export…');
    try {
      await exportAgentResult(block.result_id, format, {
        allowIncompleteOcr,
        onStage: (stage) => {
          if (stage === 'running') {
            setExportStatus(
              format === 'pdf' ? 'Building PDF…' : 'Building Word file…',
            );
          } else if (stage === 'queued') {
            setExportStatus('Preparing export…');
          } else {
            setExportStatus('Opening file…');
          }
        },
      });
    } catch (cause) {
      if (cause instanceof AgentExportError && cause.code === 'ocr_incomplete') {
        const pending = Array.isArray(cause.details.pending_pages)
          ? cause.details.pending_pages.filter(
              (page): page is number => typeof page === 'number',
            )
          : [];
        const failed = Array.isArray(cause.details.failed_pages)
          ? cause.details.failed_pages.filter(
              (page): page is number => typeof page === 'number',
            )
          : [];
        setOcrPendingPages(pending);
        setOcrFailedPages(failed);
        const pages = [...pending, ...failed];
        setExportError(
          pages.length
            ? `Page(s) ${pages.join(', ')} are not readable yet. Export only the ready pages, or finish OCR first.`
            : 'Some pages are not readable yet. Export only the ready pages, or finish OCR first.',
        );
      } else {
        setExportError(
          cause instanceof Error
            ? cause.message
            : 'The export could not be generated.',
        );
      }
    } finally {
      setExporting(null);
      setExportStatus('');
    }
  };

  const finishOcr = async () => {
    if (!block.result_id || !exportFormat || exporting) {
      return;
    }
    setExporting(exportFormat);
    setExportError(null);
    setExportStatus('Checking the scanned pages…');
    try {
      await prepareAgentResultOcr(block.result_id, (progress) => {
        setExportStatus(ocrProgressLabel(progress, 'your document'));
      });
      setOcrPendingPages([]);
      setOcrFailedPages([]);
      setExportStatus('OCR is ready. Choose a format again.');
    } catch (cause) {
      setExportError(
        cause instanceof Error
          ? cause.message
          : 'The scanned pages could not be prepared.',
      );
    } finally {
      setExporting(null);
    }
  };

  const openFile = async () => {
    if (!block.file_id) {
      return;
    }
    setOpening(true);
    try {
      await shareAgentFile(
        block.file_id,
        presentation.action?.name || 'study-file',
      );
    } catch (cause) {
      onError(
        cause instanceof Error
          ? cause.message
          : 'The file could not be opened.',
      );
    } finally {
      setOpening(false);
    }
  };

  return (
    <View style={styles.block}>
      <Text style={styles.blockTitle}>{presentation.title}</Text>
      {!!presentation.body && (
        <Text style={styles.blockBody}>{presentation.body}</Text>
      )}
      {!!presentation.basis && (
        <Text style={styles.blockBasis}>Basis: {presentation.basis}</Text>
      )}
      {block.type === 'passages' &&
        passages?.topics.map((topic, topicIndex) => (
          <View
            key={`${topic.topic}-${topicIndex}`}
            style={styles.topicBlock}>
            <Text style={styles.topicTitle}>{topic.topic}</Text>
            {topic.passages.map((passage, passageIndex) => (
              <View
                key={`${passage.page}-${passageIndex}`}
                style={styles.passage}>
                {passage.page && (
                  <Text style={styles.pageLabel}>Page {passage.page}</Text>
                )}
                <Text style={styles.passageText}>{passage.text}</Text>
                {passage.page && pageImages[passage.page] && (
                  <Image
                    source={{ uri: pageImages[passage.page] }}
                    style={styles.pageImage}
                    resizeMode="contain"
                  />
                )}
              </View>
            ))}
          </View>
        ))}
      {presentation.action?.kind === 'file' && (
        <Pressable
          accessibilityRole="button"
          onPress={() => void openFile()}
          style={styles.blockButton}>
          <Text style={styles.blockButtonText}>
            {opening ? 'Opening…' : 'Open or share file'}
          </Text>
        </Pressable>
      )}
      {!!block.result_id &&
        (block.type === 'passages' || block.type === 'summary') && (
          <View style={styles.exportWrap}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Export result"
              accessibilityState={{ expanded: exportOpen, disabled: !!exporting }}
              disabled={!!exporting}
              onPress={() => setExportOpen((current) => !current)}
              style={styles.exportToggle}>
              <Text style={styles.exportToggleLabel}>Export</Text>
              <Text style={styles.exportToggleHint}>
                {exporting ? 'Working…' : exportOpen ? 'Hide' : 'Show'}
              </Text>
            </Pressable>
            {exportOpen && (
              <View style={styles.exportRow}>
                {(['pdf', 'docx'] as const).map((format) => (
                  <Pressable
                    key={format}
                    accessibilityRole="button"
                    accessibilityState={{ disabled: !!exporting }}
                    disabled={!!exporting}
                    onPress={() => void startExport(format)}
                    style={[
                      styles.exportButton,
                      !!exporting && styles.disabled,
                    ]}>
                    <Text style={styles.exportButtonText}>
                      {exporting === format
                        ? 'Preparing…'
                        : format === 'pdf'
                          ? 'PDF'
                          : 'Word (.docx)'}
                    </Text>
                  </Pressable>
                ))}
              </View>
            )}
            {!!exportStatus && (
              <Text accessibilityLiveRegion="polite" style={styles.exportStatus}>
                {exportStatus}
              </Text>
            )}
            {!!exportError && (
              <Text style={styles.exportError}>{exportError}</Text>
            )}
            {!!(ocrPendingPages.length || ocrFailedPages.length) && (
              <View style={styles.exportRow}>
                <Pressable
                  accessibilityRole="button"
                  disabled={!!exporting}
                  onPress={() => void startExport(exportFormat ?? 'pdf', true)}
                  style={[styles.exportButton, !!exporting && styles.disabled]}>
                  <Text style={styles.exportButtonText}>Export ready pages</Text>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  disabled={!!exporting}
                  onPress={() => void finishOcr()}
                  style={[styles.exportButton, !!exporting && styles.disabled]}>
                  <Text style={styles.exportButtonText}>Finish OCR first</Text>
                </Pressable>
              </View>
            )}
          </View>
        )}
      {!!presentation.options.length && (
        <View style={styles.options}>
          {presentation.options.map((option, index) => (
            <Pressable
              key={`${option}-${index}`}
              accessibilityRole="button"
              onPress={() => onChoice(option)}
              style={styles.option}>
              <Text style={styles.optionText}>{option}</Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: palette.canvas,
  },
  flex: {
    flex: 1,
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
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
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
  block: {
    marginTop: spacing.sm,
    padding: spacing.md,
    backgroundColor: palette.canvasDeep,
    borderRadius: radii.md,
    gap: spacing.sm,
  },
  blockTitle: {
    color: palette.ink,
    fontWeight: '700',
    fontSize: 14,
  },
  blockBody: {
    color: palette.inkSoft,
    lineHeight: 21,
  },
  blockBasis: {
    color: palette.muted,
    fontSize: 12,
  },
  blockButton: {
    alignSelf: 'flex-start',
    backgroundColor: palette.accent,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  blockButtonText: {
    color: palette.white,
    fontWeight: '700',
  },
  exportWrap: {
    gap: spacing.xs,
  },
  exportToggle: {
    minHeight: 36,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  exportToggleLabel: {
    color: palette.muted,
    fontSize: 12,
    fontWeight: '600',
    letterSpacing: 0.4,
    textTransform: 'uppercase',
  },
  exportToggleHint: {
    color: palette.accentInk,
    fontSize: 12,
    fontWeight: '600',
  },
  exportRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.sm,
  },
  exportButton: {
    minHeight: 40,
    paddingHorizontal: spacing.md,
    justifyContent: 'center',
    borderRadius: radii.md,
    backgroundColor: palette.accentSoft,
  },
  exportButtonText: {
    color: palette.accentInk,
    fontSize: 13,
    fontWeight: '600',
  },
  exportStatus: {
    color: palette.muted,
    fontSize: 12,
  },
  exportError: {
    color: palette.amber,
    fontSize: 13,
  },
  options: {
    gap: spacing.sm,
  },
  option: {
    padding: spacing.sm,
    borderWidth: 1,
    borderColor: palette.lineStrong,
    borderRadius: radii.sm,
    backgroundColor: palette.surface,
  },
  optionText: {
    color: palette.inkSoft,
    fontSize: 13,
  },
  topicBlock: {
    gap: spacing.xs,
    marginTop: spacing.sm,
  },
  topicTitle: {
    color: palette.ink,
    fontWeight: '700',
  },
  passage: {
    borderLeftWidth: 2,
    borderLeftColor: palette.sage,
    paddingLeft: spacing.sm,
    gap: spacing.xs,
  },
  pageLabel: {
    color: palette.muted,
    fontSize: 12,
    fontWeight: '600',
  },
  passageText: {
    color: palette.inkSoft,
    fontSize: 14,
    lineHeight: 20,
  },
  pageImage: {
    width: '100%',
    height: 220,
    backgroundColor: palette.canvasDeep,
    borderRadius: radii.sm,
  },
  disabled: {
    opacity: 0.5,
  },
});
