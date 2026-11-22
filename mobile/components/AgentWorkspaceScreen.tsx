import * as DocumentPicker from 'expo-document-picker';
import { useRouter } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
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
  previewAgentResult,
  downloadDocumentPageImage,
  updateDocumentPageText,
  requestAgentChat,
  requestAgentResume,
  type AgentAiReadDecision,
  type AgentAttachment,
  type AgentBlock,
  type AgentExportFormat,
  type AgentPassageResult,
  type AgentPreviewBlock,
  type AgentResultEdits,
  type UploadFile,
} from '@/services/apiClient';
import { ChatMarkdown } from '@/components/ChatMarkdown';
import {
  agentChatTitle,
  deleteAgentChat,
  deleteAllAgentChats,
  emptyAgentChatList,
  loadAgentChat,
  loadAgentChats,
  newAgentChat,
  renameAgentChat,
  saveAgentChat,
  summarizeAgentChat,
  type AgentChatList,
  type AgentChatMessage,
  type AgentChatRecord,
  type SavedAgentChat,
} from '@/services/agentChatHistory';
import { presentAgentBlock } from '@/services/agentPresentation';
import {
  AGENT_CONTINUE_PROMPT,
  agentCanContinue,
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
import { TOPIC_MAX_IMAGES } from '@/services/topicWorkflow';
import { displayFont, palette, radii, spacing } from '@/theme';

type Message = AgentChatMessage;

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
  const [chatList, setChatList] = useState<AgentChatList>(emptyAgentChatList);
  const chatListRef = useRef<AgentChatList>(emptyAgentChatList());
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [chatTitle, setChatTitle] = useState('New chat');
  const [renameChat, setRenameChat] = useState<SavedAgentChat | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [renameSaving, setRenameSaving] = useState(false);
  const [chatActionError, setChatActionError] = useState<string | null>(null);
  const [attachmentMenuOpen, setAttachmentMenuOpen] = useState(false);
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const list = useRef<ScrollView>(null);
  const nextId = useRef(0);
  const attachmentOperation = useRef(false);
  const [chatHistoryReady, setChatHistoryReady] = useState(false);

  useEffect(() => {
    chatListRef.current = chatList;
  }, [chatList]);

  const applyChatRecord = (record: AgentChatRecord | null) => {
    setMessages(record?.messages ?? []);
    setConversationId(record?.conversationId ?? null);
    setAttachments(record?.attachments ?? []);
    setActiveAttachmentId(record?.activeAttachmentId ?? null);
    setChatTitle(record?.title ?? 'New chat');
    nextId.current = (record?.messages ?? []).reduce(
      (highest, message) => Math.max(highest, Number(message.id) || 0),
      0,
    );
  };

  useEffect(() => {
    let active = true;
    loadAgentChats().then(({ list: loadedList, active: loadedChat }) => {
      if (!active) {
        return;
      }
      if (loadedChat) {
        setChatList(loadedList);
        setActiveChatId(loadedChat.id);
        applyChatRecord(loadedChat);
        return;
      }
      const fresh = newAgentChat();
      const nextList: AgentChatList = {
        version: 2,
        activeChatId: fresh.id,
        chats: [summarizeAgentChat(fresh)],
      };
      setChatList(nextList);
      setActiveChatId(fresh.id);
      applyChatRecord(fresh);
    }).finally(() => {
      if (active) {
        setChatHistoryReady(true);
      }
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!chatHistoryReady || !activeChatId) {
      return;
    }
    const currentList = chatListRef.current;
    const saved = currentList.chats.find((chat) => chat.id === activeChatId);
    if (!saved) {
      return;
    }
    const now = Date.now();
    saveAgentChat(
      {
        version: 2,
        id: activeChatId,
        title: chatTitle,
        createdAt: saved.createdAt,
        updatedAt: now,
        conversationId,
        messages,
        attachments,
        activeAttachmentId,
      },
      currentList,
    )
      .then(setChatList)
      .catch(() => {
        // A local write failure must not interrupt the chat.
      });
  }, [
    chatHistoryReady,
    activeChatId,
    chatTitle,
    conversationId,
    messages,
    attachments,
    activeAttachmentId,
  ]);

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

  const persistActiveChat = async (): Promise<AgentChatList> => {
    if (!activeChatId) {
      return chatList;
    }
    const saved = chatList.chats.find((chat) => chat.id === activeChatId);
    if (!saved) {
      return chatList;
    }
    return saveAgentChat(
      {
        version: 2,
        id: activeChatId,
        title: chatTitle,
        createdAt: saved.createdAt,
        updatedAt: Date.now(),
        conversationId,
        messages,
        attachments,
        activeAttachmentId,
      },
      chatList,
    );
  };

  const openSavedChat = async (chat: SavedAgentChat) => {
    if (busy) {
      return;
    }
    setHistoryLoading(true);
    setChatActionError(null);
    try {
      const loaded = await loadAgentChat(chat.id);
      if (!loaded) {
        const next = {
          ...chatList,
          chats: chatList.chats.filter((item) => item.id !== chat.id),
        };
        setChatList(next);
        setChatActionError('That chat could not be opened and was removed.');
        return;
      }
      setChatList({ ...chatList, activeChatId: loaded.id });
      setActiveChatId(loaded.id);
      applyChatRecord(loaded);
      setHistoryOpen(false);
    } catch {
      setChatActionError('That chat could not be opened.');
    } finally {
      setHistoryLoading(false);
    }
  };

  const startNewChat = async () => {
    if (busy) {
      return;
    }
    setChatActionError(null);
    try {
      const savedList = await persistActiveChat();
      const fresh = newAgentChat();
      const nextList = await saveAgentChat(fresh, savedList);
      setChatList(nextList);
      setActiveChatId(fresh.id);
      applyChatRecord(fresh);
      setHistoryOpen(false);
      setAttachmentMenuOpen(false);
      setInput('');
      setBusy(false);
      setUploadStatus('');
      setError(null);
      router.replace('/');
    } catch {
      setChatActionError('A new chat could not be started.');
    }
  };

  const performDeleteChat = async (chat: SavedAgentChat) => {
    setChatActionError(null);
    try {
      const nextList = await deleteAgentChat(chat.id, chatList);
      setChatList(nextList);
      if (chat.id !== activeChatId) {
        return;
      }
      if (nextList.activeChatId) {
        const next = await loadAgentChat(nextList.activeChatId);
        if (next) {
          setActiveChatId(next.id);
          applyChatRecord(next);
          return;
        }
      }
      const fresh = newAgentChat();
      const withFresh = await saveAgentChat(fresh, nextList);
      setChatList(withFresh);
      setActiveChatId(fresh.id);
      applyChatRecord(fresh);
    } catch {
      setChatActionError('That chat could not be deleted.');
    }
  };

  const confirmDeleteChat = (chat: SavedAgentChat) => {
    Alert.alert(
      'Delete this chat?',
      'The messages and source list on this device will be removed.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete',
          style: 'destructive',
          onPress: () => {
            void performDeleteChat(chat);
          },
        },
      ],
    );
  };

  const deleteAllChats = () => {
    Alert.alert(
      'Delete all saved chats?',
      'Every saved chat on this device will be removed.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Delete all',
          style: 'destructive',
          onPress: () => {
            void (async () => {
              setChatActionError(null);
              try {
                const cleared = await deleteAllAgentChats(chatList);
                const fresh = newAgentChat();
                const nextList = await saveAgentChat(fresh, cleared);
                setChatList(nextList);
                setActiveChatId(fresh.id);
                applyChatRecord(fresh);
              } catch {
                setChatActionError('Saved chats could not be deleted.');
              }
            })();
          },
        },
      ],
    );
  };

  const beginRename = (chat: SavedAgentChat) => {
    setRenameDraft(chat.title);
    setRenameChat(chat);
    setChatActionError(null);
  };

  const saveRename = async () => {
    if (!renameChat || renameSaving) {
      return;
    }
    setRenameSaving(true);
    setChatActionError(null);
    try {
      const nextList = await renameAgentChat(
        renameChat.id,
        renameDraft,
        chatList,
      );
      setChatList(nextList);
      if (renameChat.id === activeChatId) {
        const renamed = nextList.chats.find((chat) => chat.id === activeChatId);
        setChatTitle(renamed?.title ?? 'New chat');
      }
      setRenameChat(null);
    } catch {
      setChatActionError('That chat could not be renamed.');
    } finally {
      setRenameSaving(false);
    }
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
    if (chatTitle === 'New chat') {
      setChatTitle(
        agentChatTitle([
          ...messages,
          { id: 'pending', role: 'user', text: message },
        ]),
      );
    }
    setBusy(true);
    try {
      const response = await requestAgentChat(
        message,
        conversationId,
        attachments,
        activeAttachment
          ? { id: activeAttachment.id, kind: activeAttachment.kind }
          : null,
      );
      setConversationId(response.conversation_id);
      addMessage({
        role: 'assistant',
        text: response.reply,
        blocks: response.blocks,
        warnings: agentResponseWarnings(response.status, response.warnings),
        canContinue: agentCanContinue(response.status),
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

  const resumeApproval = async (
    pendingId: string,
    decision: AgentAiReadDecision,
  ) => {
    if (busy) {
      return;
    }
    setError(null);
    addMessage({
      role: 'user',
      text:
        decision === 'deny'
          ? "Don't allow AI reading"
          : decision === 'remember'
            ? 'Allow and remember AI reading'
            : 'Allow AI reading this time',
    });
    setBusy(true);
    try {
      const response = await requestAgentResume(pendingId, decision);
      setConversationId(response.conversation_id);
      setMessages((current) =>
        current.map((message) => {
          if (
            !message.blocks?.some(
              (block) =>
                block.type === 'approval' && block.pending_id === pendingId,
            )
          ) {
            return message;
          }
          const blocks = (message.blocks ?? []).filter(
            (block) =>
              !(block.type === 'approval' && block.pending_id === pendingId),
          );
          return { ...message, blocks: blocks.length > 0 ? blocks : undefined };
        }),
      );
      addMessage({
        role: 'assistant',
        text: response.reply,
        blocks: response.blocks,
        warnings: agentResponseWarnings(response.status, response.warnings),
        canContinue: agentCanContinue(response.status),
      });
    } catch (cause) {
      addMessage({
        role: 'assistant',
        text: agentFailureMessage(cause),
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
            accessibilityLabel="Open saved chats"
            accessibilityState={{ expanded: historyOpen }}
            onPress={() => {
              setChatActionError(null);
              setHistoryOpen(true);
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
              {!!message.text &&
                (message.role === 'user' ? (
                  <Text style={styles.userText}>{message.text}</Text>
                ) : (
                  <ChatMarkdown
                    text={message.text}
                    style={message.tone === 'warn' ? styles.warnText : styles.body}
                  />
                ))}
              {message.warnings?.map((warning, index) => (
                <Text
                  key={`${message.id}-warning-${index}`}
                  style={styles.warnText}>
                  {warning}
                </Text>
              ))}
              {message.canContinue ? (
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Continue the cut-off reply"
                  disabled={busy}
                  onPress={() => void send(AGENT_CONTINUE_PROMPT)}
                  style={[
                    styles.continueButton,
                    busy ? styles.disabled : null,
                  ]}>
                  <Text style={styles.continueLabel}>Continue</Text>
                </Pressable>
              ) : null}
              {message.blocks?.map((block, index) => (
                <RenderedBlock
                  key={`${message.id}-block-${index}`}
                  block={block}
                  onChoice={(choice) => void send(choice)}
                  onApproval={(pendingId, decision) =>
                    void resumeApproval(pendingId, decision)
                  }
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
            accessibilityLabel="Open file converter"
            onPress={() => router.push('/file-converter')}
            style={styles.converterButton}>
            <Text style={styles.converterLabel}>File Converter</Text>
            <Text style={styles.converterSoon}>Excel, Word, PDF, CSV</Text>
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
            accessibilityLabel="Close saved chats"
            onPress={() => setHistoryOpen(false)}
            style={styles.drawerScrim}
          />
          <SafeAreaView style={styles.historyPanel} edges={['top', 'bottom']}>
            <View style={styles.historyHeader}>
              <Text style={styles.historyTitle}>Chats</Text>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Close chats"
                onPress={() => setHistoryOpen(false)}
                style={styles.closeButton}>
                <Text style={styles.closeLabel}>×</Text>
              </Pressable>
            </View>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Start a new chat"
              accessibilityState={{ disabled: busy || historyLoading }}
              disabled={busy || historyLoading}
              onPress={() => void startNewChat()}
              style={[
                styles.newChatButton,
                busy || historyLoading ? styles.disabled : null,
              ]}>
              <Text style={styles.newChatPlus}>+</Text>
              <Text style={styles.newChatLabel}>New Chat</Text>
            </Pressable>
            {!!chatActionError && (
              <Text style={styles.chatActionError}>{chatActionError}</Text>
            )}
            <ScrollView
              style={styles.historyList}
              contentContainerStyle={styles.historyListContent}>
              {historyLoading ? (
                <View style={styles.historyState}>
                  <ActivityIndicator color={palette.accent} />
                  <Text style={styles.historyHint}>Opening chat...</Text>
                </View>
              ) : chatList.chats.length === 0 ? (
                <Text style={styles.historyHint}>
                  Saved chats will appear here.
                </Text>
              ) : (
                chatList.chats.map((chat) => {
                  const current = chat.id === activeChatId;
                  return (
                    <View
                      key={chat.id}
                      style={[
                        styles.historyItem,
                        current ? styles.historyItemActive : null,
                      ]}>
                      <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={`Open chat ${chat.title}`}
                        accessibilityState={{ selected: current }}
                        disabled={busy}
                        onPress={() => void openSavedChat(chat)}
                        style={styles.historyOpen}>
                        <Text
                          style={styles.historyItemTitle}
                          numberOfLines={2}>
                          {chat.title}
                        </Text>
                        <Text
                          style={styles.historyItemMeta}
                          numberOfLines={1}>
                          {chat.messageCount}{' '}
                          {chat.messageCount === 1 ? 'message' : 'messages'} ·{' '}
                          {new Date(chat.updatedAt).toLocaleDateString()}
                        </Text>
                      </Pressable>
                      <View style={styles.historyActions}>
                        <Pressable
                          accessibilityRole="button"
                          accessibilityLabel={`Rename ${chat.title}`}
                          onPress={() => beginRename(chat)}
                          style={styles.historyAction}>
                          <Text style={styles.historyActionText}>Rename</Text>
                        </Pressable>
                        <Pressable
                          accessibilityRole="button"
                          accessibilityLabel={`Delete ${chat.title}`}
                          onPress={() => confirmDeleteChat(chat)}
                          style={styles.historyAction}>
                          <Text style={styles.historyActionDelete}>Delete</Text>
                        </Pressable>
                      </View>
                    </View>
                  );
                })
              )}
            </ScrollView>
            {chatList.chats.length > 0 ? (
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Delete all saved chats"
                disabled={busy}
                onPress={deleteAllChats}
                style={styles.deleteAllButton}>
                <Text style={styles.deleteAllText}>Delete all chats</Text>
              </Pressable>
            ) : null}
          </SafeAreaView>
        </View>
      </Modal>

      <Modal
        visible={!!renameChat}
        transparent
        animationType="fade"
        statusBarTranslucent
        onRequestClose={() => setRenameChat(null)}>
        <View style={styles.renameRoot}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Cancel renaming chat"
            onPress={() => setRenameChat(null)}
            style={styles.renameScrim}
          />
          <View style={styles.renamePanel}>
            <Text style={styles.renameTitle}>Rename chat</Text>
            <TextInput
              accessibilityLabel="Chat title"
              autoFocus
              maxLength={80}
              onChangeText={setRenameDraft}
              onSubmitEditing={() => void saveRename()}
              placeholder="Chat title"
              placeholderTextColor={palette.muted}
              returnKeyType="done"
              style={styles.renameInput}
              value={renameDraft}
            />
            <View style={styles.renameActions}>
              <Pressable
                accessibilityRole="button"
                onPress={() => setRenameChat(null)}
                style={styles.renameCancel}>
                <Text style={styles.cancelLabel}>Cancel</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ disabled: renameSaving }}
                disabled={renameSaving}
                onPress={() => void saveRename()}
                style={[
                  styles.renameSave,
                  renameSaving ? styles.disabled : null,
                ]}>
                <Text style={styles.renameSaveText}>
                  {renameSaving ? 'Saving...' : 'Save'}
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

function RenderedBlock({
  block,
  onChoice,
  onApproval,
  onError,
}: {
  block: AgentBlock;
  onChoice: (choice: string) => void;
  onApproval: (pendingId: string, decision: AgentAiReadDecision) => void;
  onError: (message: string) => void;
}) {
  const presentation = presentAgentBlock(block);
  const [passages, setPassages] = useState<AgentPassageResult | null>(null);
  const [expired, setExpired] = useState(false);
  const [pageImages, setPageImages] = useState<Record<number, string>>({});
  const [opening, setOpening] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [exporting, setExporting] = useState<AgentExportFormat | null>(null);
  const [exportStatus, setExportStatus] = useState('');
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportFormat, setExportFormat] = useState<AgentExportFormat | null>(null);
  const [ocrPendingPages, setOcrPendingPages] = useState<number[]>([]);
  const [ocrFailedPages, setOcrFailedPages] = useState<number[]>([]);
  const [summaryDraft, setSummaryDraft] = useState('');
  const [removedPassageIds, setRemovedPassageIds] = useState<string[]>([]);
  const [orderedPassageIds, setOrderedPassageIds] = useState<string[]>([]);
  const [orderDirty, setOrderDirty] = useState(false);
  const [excludedPages, setExcludedPages] = useState<number[]>([]);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewBlocks, setPreviewBlocks] = useState<AgentPreviewBlock[]>([]);
  const [previewTotal, setPreviewTotal] = useState(0);
  const [previewHasMore, setPreviewHasMore] = useState(false);
  const [aiReadDraft, setAiReadDraft] = useState(block.text || '');
  const [aiReadImageUri, setAiReadImageUri] = useState<string | null>(null);
  const [aiReadSaving, setAiReadSaving] = useState(false);
  const [aiReadStatus, setAiReadStatus] = useState('');
  const [approvalChoice, setApprovalChoice] = useState<
    AgentAiReadDecision | null
  >(null);
  const approvalPendingId =
    block.type === 'approval' ? block.pending_id : undefined;

  useEffect(() => {
    if (
      block.type !== 'ai_read' ||
      !block.document_id ||
      !block.page
    ) {
      return;
    }
    let active = true;
    void downloadDocumentPageImage(block.document_id, block.page)
      .then((uri) => {
        if (active) setAiReadImageUri(uri);
      })
      .catch(() => {
        if (active) setAiReadImageUri(null);
      });
    return () => {
      active = false;
    };
  }, [block.document_id, block.page, block.type]);

  useEffect(() => {
    if (
      (block.type !== 'passages' && block.type !== 'summary') ||
      !block.result_id
    ) {
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
        setSummaryDraft(result.summary ?? '');
        setRemovedPassageIds([]);
        setOrderedPassageIds(
          result.topics.flatMap((topic) =>
            topic.passages.map((passage) => passage.passage_id),
          ),
        );
        setOrderDirty(false);
        setExcludedPages([]);
      })
      .catch((cause) => {
        if (!active) {
          return;
        }
        if (
          cause instanceof ApiError &&
          cause.code === 'agent_result_not_found'
        ) {
          setExpired(true);
          return;
        }
        onError(
          cause instanceof Error
            ? cause.message
            : 'Passages could not be loaded.',
        );
      });
    return () => {
      active = false;
    };
  }, [block.result_id, block.type, onError]);

  const allPassages =
    passages?.topics.flatMap((topic) => topic.passages) ?? [];
  const passageById = new Map(
    allPassages.map((passage) => [passage.passage_id, passage]),
  );
  const passagePages = [
    ...new Set(
      allPassages
        .map((passage) => passage.page)
        .filter((page): page is number => typeof page === 'number'),
    ),
  ].sort((a, b) => a - b);
  const activePassageIds = orderedPassageIds.filter(
    (id) => !removedPassageIds.includes(id),
  );

  const editsForRequest = (): AgentResultEdits | undefined => {
    const edits: AgentResultEdits = {};
    if (
      block.type === 'summary' &&
      summaryDraft !== (passages?.summary ?? '')
    ) {
      edits.summary = summaryDraft;
    }
    if (removedPassageIds.length) {
      edits.removed_passage_ids = removedPassageIds;
    }
    if (orderDirty) {
      edits.ordered_passage_ids = orderedPassageIds;
    }
    if (excludedPages.length) {
      edits.included_pages = passagePages.filter(
        (page) => !excludedPages.includes(page),
      );
    }
    return Object.keys(edits).length ? edits : undefined;
  };

  const toggleRemoved = (passageId: string) => {
    setRemovedPassageIds((current) =>
      current.includes(passageId)
        ? current.filter((id) => id !== passageId)
        : [...current, passageId],
    );
  };

  const togglePage = (page: number) => {
    setExcludedPages((current) =>
      current.includes(page)
        ? current.filter((value) => value !== page)
        : [...current, page],
    );
  };

  const movePassage = (passageId: string, direction: -1 | 1) => {
    const targetTopic = passages?.topics.find((topic) =>
      topic.passages.some((passage) => passage.passage_id === passageId),
    );
    const topicIds = new Set(
      targetTopic?.passages.map((passage) => passage.passage_id) ?? [],
    );
    const topicOrder = orderedPassageIds.filter((id) => topicIds.has(id));
    const activeOrder = topicOrder.filter(
      (id) => !removedPassageIds.includes(id),
    );
    const index = activeOrder.indexOf(passageId);
    const swapId = activeOrder[index + direction];
    if (!swapId) {
      return;
    }
    const first = orderedPassageIds.indexOf(passageId);
    const second = orderedPassageIds.indexOf(swapId);
    const next = [...orderedPassageIds];
    [next[first], next[second]] = [next[second], next[first]];
    setOrderedPassageIds(next);
    setOrderDirty(true);
  };

  const openPreview = async () => {
    if (!block.result_id || previewLoading) {
      return;
    }
    setPreviewOpen(true);
    setPreviewLoading(true);
    setPreviewError(null);
    setPreviewBlocks([]);
    try {
      const preview = await previewAgentResult(
        block.result_id,
        editsForRequest(),
      );
      setPreviewBlocks(preview.blocks);
      setPreviewTotal(preview.total_blocks);
      setPreviewHasMore(preview.has_more);
    } catch (cause) {
      setPreviewError(
        cause instanceof Error
          ? cause.message
          : 'The preview could not be prepared.',
      );
    } finally {
      setPreviewLoading(false);
    }
  };

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
        edits: editsForRequest(),
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
      if (
        cause instanceof ApiError &&
        cause.code === 'agent_file_not_found'
      ) {
        setExpired(true);
        return;
      }
      onError(
        cause instanceof Error
          ? cause.message
          : 'The file could not be opened.',
      );
    } finally {
      setOpening(false);
    }
  };

  const saveAiReadText = async () => {
    if (block.type !== 'ai_read' || !block.document_id || !block.page) return;
    setAiReadSaving(true);
    setAiReadStatus('');
    try {
      await updateDocumentPageText(
        block.document_id,
        block.page,
        aiReadDraft,
      );
      setAiReadStatus('Saved. Search, quotes, and exports will use this corrected text.');
    } catch (cause) {
      setAiReadStatus(
        cause instanceof Error ? cause.message : 'Page text could not be saved.',
      );
    } finally {
      setAiReadSaving(false);
    }
  };

  return (
    <View style={styles.block}>
      <Text style={styles.blockTitle}>{presentation.title}</Text>
      {!!presentation.body && block.type !== 'summary' && block.type !== 'ai_read' && (
        <Text style={styles.blockBody}>{presentation.body}</Text>
      )}
      {block.type === 'ai_read' && (
        <View style={styles.editBlock}>
          <Text style={styles.pageLabel}>AI-read text · Page {block.page}</Text>
          {aiReadImageUri ? (
            <Image source={{ uri: aiReadImageUri }} style={styles.pageImage} />
          ) : null}
          <TextInput
            accessibilityLabel={`Edit AI-read text for page ${block.page}`}
            multiline
            onChangeText={setAiReadDraft}
            placeholder="AI-read page text"
            placeholderTextColor={palette.muted}
            style={styles.summaryInput}
            textAlignVertical="top"
            value={aiReadDraft}
          />
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: aiReadSaving }}
            disabled={aiReadSaving}
            onPress={() => void saveAiReadText()}
            style={styles.blockButton}>
            <Text style={styles.blockButtonText}>
              {aiReadSaving ? 'Saving…' : 'Save corrected text'}
            </Text>
          </Pressable>
          {!!aiReadStatus && <Text style={styles.exportStatus}>{aiReadStatus}</Text>}
        </View>
      )}
      {!expired && block.type === 'summary' && !!passages && (
        <View style={styles.editBlock}>
          <Text style={styles.editLabel}>AI summary, not source text</Text>
          <TextInput
            accessibilityLabel="Edit summary before export"
            multiline
            onChangeText={setSummaryDraft}
            placeholder="Write or edit the summary before export."
            placeholderTextColor={palette.muted}
            style={styles.summaryInput}
            textAlignVertical="top"
            value={summaryDraft}
          />
          <Text style={styles.editHint}>
            Changes stay on this device until you preview or export.
          </Text>
        </View>
      )}
      {!!presentation.basis && (
        <Text style={styles.blockBasis}>Basis: {presentation.basis}</Text>
      )}
      {expired && (
        <Text style={styles.blockExpired}>
          {block.type === 'file'
            ? 'This exported file has expired. Generate it again from a saved result.'
            : 'This result has expired. Its stored passages are no longer available.'}
        </Text>
      )}
      {!expired && block.type === 'passages' && passages && (
        <View style={styles.editBlock}>
          <Text style={styles.editHint}>
            Remove, reorder, or leave out pages before export. Source text
            stays read-only.
          </Text>
          {passagePages.length > 1 && (
            <View style={styles.pageChips}>
              {passagePages.map((page) => {
                const included = !excludedPages.includes(page);
                return (
                  <Pressable
                    key={page}
                    accessibilityRole="button"
                    accessibilityLabel={`${included ? 'Leave out' : 'Include'} page ${page}`}
                    accessibilityState={{ selected: included }}
                    onPress={() => togglePage(page)}
                    style={[
                      styles.pageChip,
                      included && styles.pageChipActive,
                    ]}>
                    <Text
                      style={[
                        styles.pageChipText,
                        included && styles.pageChipTextActive,
                      ]}>
                      Page {page}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          )}
        </View>
      )}
      {!expired && block.type === 'passages' &&
        passages?.topics.map((topic, topicIndex) => {
          const topicIds = new Set(
            topic.passages.map((passage) => passage.passage_id),
          );
          const activeIds = activePassageIds.filter((id) => topicIds.has(id));
          return (
            <View
              key={`${topic.topic}-${topicIndex}`}
              style={styles.topicBlock}>
              <Text style={styles.topicTitle}>{topic.topic}</Text>
              {activeIds.map((passageId) => {
                const passage = passageById.get(passageId);
                if (!passage) {
                  return null;
                }
                const position = activeIds.indexOf(passageId);
                const hiddenByPage =
                  typeof passage.page === 'number' &&
                  excludedPages.includes(passage.page);
                return (
                  <View
                    key={passageId}
                    style={[styles.passage, hiddenByPage && styles.passageMuted]}>
                    <View style={styles.passageMetaRow}>
                      {passage.page && (
                        <Text style={styles.pageLabel}>Page {passage.page}</Text>
                      )}
                      {hiddenByPage && (
                        <Text style={styles.excludedLabel}>Page left out</Text>
                      )}
                    </View>
                    <Text style={styles.passageText}>{passage.text}</Text>
                    {passage.page && pageImages[passage.page] && (
                      <Image
                        source={{ uri: pageImages[passage.page] }}
                        style={styles.pageImage}
                        resizeMode="contain"
                      />
                    )}
                    <View style={styles.passageActions}>
                      <Pressable
                        accessibilityRole="button"
                        accessibilityLabel="Move passage up"
                        accessibilityState={{ disabled: position === 0 }}
                        disabled={position === 0}
                        onPress={() => movePassage(passageId, -1)}
                        style={[
                          styles.smallButton,
                          position === 0 && styles.disabled,
                        ]}>
                        <Text style={styles.smallButtonText}>Up</Text>
                      </Pressable>
                      <Pressable
                        accessibilityRole="button"
                        accessibilityLabel="Move passage down"
                        accessibilityState={{
                          disabled: position === activeIds.length - 1,
                        }}
                        disabled={position === activeIds.length - 1}
                        onPress={() => movePassage(passageId, 1)}
                        style={[
                          styles.smallButton,
                          position === activeIds.length - 1 && styles.disabled,
                        ]}>
                        <Text style={styles.smallButtonText}>Down</Text>
                      </Pressable>
                      <Pressable
                        accessibilityRole="button"
                        accessibilityLabel="Remove passage from export"
                        onPress={() => toggleRemoved(passageId)}
                        style={styles.smallButton}>
                        <Text style={styles.smallButtonText}>Remove</Text>
                      </Pressable>
                    </View>
                  </View>
                );
              })}
              {removedPassageIds.some((id) => topicIds.has(id)) && (
                <View style={styles.removedBox}>
                  <Text style={styles.removedTitle}>Removed from export</Text>
                  {removedPassageIds
                    .filter((id) => topicIds.has(id))
                    .map((id) => {
                      const passage = passageById.get(id);
                      if (!passage) {
                        return null;
                      }
                      return (
                        <View key={id} style={styles.removedRow}>
                          <Text style={styles.removedText} numberOfLines={2}>
                            {passage.text || 'Page with no readable text'}
                          </Text>
                          <Pressable
                            accessibilityRole="button"
                            accessibilityLabel="Restore passage"
                            onPress={() => toggleRemoved(id)}
                            style={styles.smallButton}>
                            <Text style={styles.smallButtonText}>Restore</Text>
                          </Pressable>
                        </View>
                      );
                    })}
                </View>
              )}
            </View>
          );
        })}
      {!expired && presentation.action?.kind === 'file' && (
        <Pressable
          accessibilityRole="button"
          onPress={() => void openFile()}
          style={styles.blockButton}>
          <Text style={styles.blockButtonText}>
            {opening ? 'Opening…' : 'Open or share file'}
          </Text>
        </Pressable>
      )}
      {!expired &&
        !!block.result_id &&
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
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Preview result before export"
                  disabled={previewLoading || !!exporting}
                  onPress={() => void openPreview()}
                  style={[
                    styles.exportButton,
                    (previewLoading || !!exporting) && styles.disabled,
                  ]}>
                  <Text style={styles.exportButtonText}>
                    {previewLoading ? 'Previewing…' : 'Preview'}
                  </Text>
                </Pressable>
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
      {!!approvalPendingId && (
        <View style={styles.options}>
          {[
            { label: 'Allow this time', decision: 'allow_once' as const },
            {
              label: 'Allow and remember my choice',
              decision: 'remember' as const,
            },
            { label: "Don't allow", decision: 'deny' as const },
          ].map((item) => (
            <Pressable
              key={item.decision}
              accessibilityRole="button"
              accessibilityState={{ disabled: approvalChoice !== null }}
              disabled={approvalChoice !== null}
              onPress={() => {
                if (approvalChoice) return;
                setApprovalChoice(item.decision);
                onApproval(approvalPendingId, item.decision);
              }}
              style={styles.option}>
              <Text style={styles.optionText}>{item.label}</Text>
            </Pressable>
          ))}
          <Text style={styles.editHint}>
            {block.pages && block.pages.length > 0
              ? `Page${block.pages.length === 1 ? '' : 's'} ${block.pages.join(
                  ', ',
                )}. `
              : ''}
            The page image is sent to{' '}
            {block.provider || 'the configured AI provider'} only if you allow
            it.
          </Text>
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
      <Modal
        visible={previewOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setPreviewOpen(false)}>
        <View style={styles.previewOverlay}>
          <SafeAreaView style={styles.previewSheet}>
            <View style={styles.previewHeader}>
              <Text style={styles.previewTitle}>Preview before export</Text>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Close preview"
                onPress={() => setPreviewOpen(false)}
                style={styles.previewClose}>
                <Text style={styles.previewCloseText}>Close</Text>
              </Pressable>
            </View>
            <Text style={styles.editHint}>
              This is the document content your current edits will produce.
            </Text>
            {previewLoading && (
              <ActivityIndicator style={styles.previewSpinner} />
            )}
            {!!previewError && (
              <Text style={styles.previewError}>{previewError}</Text>
            )}
            <ScrollView contentContainerStyle={styles.previewBody}>
              {previewBlocks.map((previewBlock, index) =>
                previewBlock.rows ? (
                  <ScrollView
                    key={`${previewBlock.type}-${index}`}
                    horizontal
                    showsHorizontalScrollIndicator={false}>
                    <View style={styles.previewTable}>
                      {previewBlock.rows.map((row, rowIndex) => (
                        <View key={rowIndex} style={styles.previewTableRow}>
                          {row.map((cell, cellIndex) => (
                            <View
                              key={cellIndex}
                              style={[
                                styles.previewTableCell,
                                rowIndex === 0
                                  ? styles.previewTableHeaderCell
                                  : null,
                              ]}>
                              <Text
                                style={[
                                  styles.previewTableText,
                                  rowIndex === 0
                                    ? styles.previewTableHeaderText
                                    : null,
                                ]}>
                                {cell}
                              </Text>
                            </View>
                          ))}
                        </View>
                      ))}
                    </View>
                  </ScrollView>
                ) : (
                  <Text
                    key={`${previewBlock.type}-${index}`}
                    style={[
                      styles.previewBlockText,
                      (previewBlock.type === 'h1' ||
                        previewBlock.type === 'h2') &&
                        styles.previewHeading,
                    ]}>
                    {previewBlock.text}
                  </Text>
                ),
              )}
            </ScrollView>
            {!previewLoading && previewBlocks.length > 0 && (
              <Text style={styles.previewMeta}>
                {previewHasMore
                  ? `Showing the first ${previewBlocks.length} of ${previewTotal} blocks.`
                  : `${previewTotal} blocks in the export.`}
              </Text>
            )}
          </SafeAreaView>
        </View>
      </Modal>
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
  continueButton: {
    alignSelf: 'flex-start',
    marginTop: 8,
    paddingHorizontal: 12,
    paddingVertical: 7,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: palette.accent,
  },
  continueLabel: {
    color: palette.accent,
    fontSize: 12,
    fontWeight: '600',
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
    minHeight: 72,
    flexDirection: 'row',
    alignItems: 'center',
    paddingLeft: spacing.md,
    paddingRight: spacing.xs,
    paddingVertical: spacing.sm,
    borderBottomWidth: 1,
    borderBottomColor: palette.line,
  },
  historyItemActive: {
    backgroundColor: palette.accentSoft,
    borderRadius: radii.md,
  },
  historyOpen: {
    flex: 1,
    minWidth: 0,
    minHeight: 48,
    justifyContent: 'center',
    gap: 3,
  },
  historyActions: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  historyAction: {
    minHeight: 44,
    minWidth: 44,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: spacing.xs,
  },
  historyActionText: {
    color: palette.accent,
    fontSize: 10,
    fontWeight: '700',
  },
  historyActionDelete: {
    color: palette.danger ?? '#A33A32',
    fontSize: 10,
    fontWeight: '700',
  },
  chatActionError: {
    color: palette.danger ?? '#A33A32',
    fontSize: 12,
    lineHeight: 18,
    paddingTop: spacing.sm,
  },
  deleteAllButton: {
    minHeight: 48,
    alignItems: 'center',
    justifyContent: 'center',
    marginTop: spacing.sm,
  },
  deleteAllText: {
    color: palette.danger ?? '#A33A32',
    fontSize: 12,
    fontWeight: '700',
  },
  renameRoot: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.lg,
  },
  renameScrim: {
    ...StyleSheet.absoluteFill,
    backgroundColor: 'rgba(18, 29, 22, 0.38)',
  },
  renamePanel: {
    width: '100%',
    maxWidth: 420,
    gap: spacing.md,
    padding: spacing.lg,
    borderRadius: radii.lg,
    backgroundColor: palette.canvas,
  },
  renameTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 18,
    fontWeight: '600',
  },
  renameInput: {
    minHeight: 46,
    paddingHorizontal: spacing.md,
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.md,
    color: palette.ink,
    fontSize: 14,
    backgroundColor: palette.surface,
  },
  renameActions: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: spacing.sm,
  },
  renameCancel: {
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.md,
  },
  renameSave: {
    minHeight: 44,
    justifyContent: 'center',
    paddingHorizontal: spacing.lg,
    borderRadius: radii.md,
    backgroundColor: palette.accent,
  },
  renameSaveText: {
    color: palette.canvas,
    fontSize: 13,
    fontWeight: '700',
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
  blockExpired: {
    color: palette.amber,
    fontSize: 13,
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
  editBlock: {
    gap: spacing.xs,
    marginTop: spacing.xs,
  },
  editLabel: {
    color: palette.sage,
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 0.6,
  },
  editHint: {
    color: palette.muted,
    fontSize: 12,
    lineHeight: 18,
  },
  summaryInput: {
    minHeight: 120,
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.md,
    backgroundColor: palette.surface,
    padding: spacing.md,
    color: palette.ink,
    fontSize: 14,
    lineHeight: 21,
  },
  pageChips: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  pageChip: {
    minHeight: 34,
    paddingHorizontal: spacing.sm + 2,
    justifyContent: 'center',
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: palette.line,
    backgroundColor: palette.surfaceMuted,
  },
  pageChipActive: {
    borderColor: palette.accent,
    backgroundColor: palette.accentSoft,
  },
  pageChipText: {
    color: palette.muted,
    fontSize: 12,
    fontWeight: '600',
  },
  pageChipTextActive: {
    color: palette.accentInk,
  },
  passageMetaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  excludedLabel: {
    color: palette.amber,
    fontSize: 11,
    fontWeight: '700',
  },
  passageMuted: {
    opacity: 0.45,
  },
  passageActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  smallButton: {
    minHeight: 36,
    paddingHorizontal: spacing.sm + 2,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radii.sm,
    borderWidth: 1,
    borderColor: palette.lineStrong,
    backgroundColor: palette.surface,
  },
  smallButtonText: {
    color: palette.inkSoft,
    fontSize: 12,
    fontWeight: '700',
  },
  removedBox: {
    gap: spacing.xs,
    padding: spacing.sm,
    borderRadius: radii.sm,
    backgroundColor: palette.surfaceMuted,
  },
  removedTitle: {
    color: palette.muted,
    fontSize: 11,
    fontWeight: '700',
  },
  removedRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
  },
  removedText: {
    flex: 1,
    color: palette.muted,
    fontSize: 12,
    lineHeight: 17,
  },
  previewOverlay: {
    flex: 1,
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(28, 42, 34, 0.45)',
  },
  previewSheet: {
    maxHeight: '90%',
    padding: spacing.lg,
    gap: spacing.sm,
    borderTopLeftRadius: radii.lg,
    borderTopRightRadius: radii.lg,
    backgroundColor: palette.surface,
  },
  previewHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.md,
  },
  previewTitle: {
    flex: 1,
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 19,
    fontWeight: '600',
  },
  previewClose: {
    minHeight: 44,
    paddingHorizontal: spacing.sm,
    justifyContent: 'center',
  },
  previewCloseText: {
    color: palette.accent,
    fontSize: 13,
    fontWeight: '700',
  },
  previewSpinner: {
    paddingVertical: spacing.lg,
  },
  previewError: {
    color: palette.danger,
    fontSize: 13,
    lineHeight: 19,
  },
  previewBody: {
    gap: spacing.sm,
    paddingVertical: spacing.sm,
  },
  previewTable: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.lineStrong,
    alignSelf: 'flex-start',
  },
  previewTableRow: { flexDirection: 'row' },
  previewTableCell: {
    width: 132,
    minHeight: 34,
    paddingHorizontal: spacing.sm,
    paddingVertical: 6,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.line,
    backgroundColor: palette.surface,
  },
  previewTableHeaderCell: { backgroundColor: palette.surfaceMuted },
  previewTableText: { color: palette.inkSoft, fontSize: 12, lineHeight: 17 },
  previewTableHeaderText: { color: palette.ink, fontWeight: '700' },
  previewBlockText: {
    color: palette.inkSoft,
    fontSize: 13,
    lineHeight: 20,
  },
  previewHeading: {
    color: palette.ink,
    fontSize: 15,
    fontWeight: '700',
  },
  previewMeta: {
    color: palette.muted,
    fontSize: 11,
  },
  disabled: {
    opacity: 0.5,
  },
});
