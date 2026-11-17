import * as DocumentPicker from 'expo-document-picker';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import {
  AGENT_MAX_ATTACHMENTS,
  AGENT_MAX_MESSAGE_CHARS,
  ApiError,
  requestAgentChat,
  type AgentAttachment,
  type AgentBlock,
  type AgentPassageResult,
  type UploadFile,
} from '@/services/apiClient';
import {
  conversationIdAfterAgentError,
  isExpiredAgentConversation,
  loadAgentPassages,
  shareAgentFile,
  uploadAgentDocument,
  uploadAgentImages,
} from '@/services/agentWorkflow';
import { pickNoteImages } from '@/services/imagePicker';
import { presentAgentBlock } from '@/services/agentPresentation';
import { TOPIC_MAX_IMAGES } from '@/services/topicWorkflow';
import { palette, radii, spacing } from '@/theme';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  blocks?: AgentBlock[];
  warnings?: string[];
};

const ACTIONS = ['Original passages', 'Summary', 'Both', 'Page range'];
const PDF = 'application/pdf';
const DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

export default function AgentWorkspaceScreen() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [attachments, setAttachments] = useState<AgentAttachment[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploadStatus, setUploadStatus] = useState('');
  const [selectedAction, setSelectedAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const list = useRef<ScrollView>(null);
  const nextId = useRef(0);
  const attachmentOperation = useRef(false);

  const addMessage = useCallback((message: Omit<Message, 'id'>) => {
    nextId.current += 1;
    setMessages((current) => [...current, { ...message, id: String(nextId.current) }]);
  }, []);

  const attachDocument = async () => {
    if (attachmentOperation.current) return;
    if (attachments.length >= AGENT_MAX_ATTACHMENTS) {
      setError(`Attach at most ${AGENT_MAX_ATTACHMENTS} items.`);
      return;
    }
    attachmentOperation.current = true;
    setUploadStatus('Choose a file…');
    try {
      const selected = await DocumentPicker.getDocumentAsync({
        type: [PDF, DOCX],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (selected.canceled || !selected.assets[0]) return;
      const asset = selected.assets[0];
      setUploadStatus(`Uploading ${asset.name}…`);
      setError(null);
      const attached = await uploadAgentDocument({
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType,
        size: asset.size,
      }, (progress) => {
        const total = progress.progress.total_units;
        const done = progress.progress.processed_units;
        setUploadStatus(
          total > 0
            ? `Preparing ${asset.name}… ${done} of ${total}`
            : `Preparing ${asset.name}…`,
        );
      });
      setAttachments((current) => [
        ...current.filter((item) => item.id !== attached.id),
        attached,
      ]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The file could not be attached.');
    } finally {
      attachmentOperation.current = false;
      setUploadStatus('');
    }
  };

  const attachImages = async () => {
    if (attachmentOperation.current) return;
    if (attachments.length >= AGENT_MAX_ATTACHMENTS) {
      setError(`Attach at most ${AGENT_MAX_ATTACHMENTS} items.`);
      return;
    }
    attachmentOperation.current = true;
    setUploadStatus('Choose images…');
    try {
      const selected = await pickNoteImages(TOPIC_MAX_IMAGES);
      if (selected.status !== 'selected' || selected.assets.length === 0) {
        if (selected.status === 'denied') setError('Allow photo access to attach images.');
        if (selected.status === 'error') setError('The photo library could not be opened.');
        return;
      }
      setUploadStatus('Uploading images…');
      setError(null);
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
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The images could not be attached.');
    } finally {
      attachmentOperation.current = false;
      setUploadStatus('');
    }
  };

  const send = async (override?: string) => {
    const typed = (override ?? input).trim();
    if (!typed || busy) return;
    const message = selectedAction && !override ? `${selectedAction}: ${typed}` : typed;
    if (Array.from(message).length > AGENT_MAX_MESSAGE_CHARS) {
      setError(`Messages must be ${AGENT_MAX_MESSAGE_CHARS.toLocaleString()} characters or fewer.`);
      return;
    }
    setInput('');
    setSelectedAction(null);
    setError(null);
    addMessage({ role: 'user', text: typed });
    setBusy(true);
    try {
      const response = await requestAgentChat(message, conversationId, attachments);
      setConversationId(response.conversation_id);
      addMessage({
        role: 'assistant',
        text: response.reply,
        blocks: response.blocks,
        warnings: response.warnings,
      });
    } catch (cause) {
      const expiredConversation = isExpiredAgentConversation(cause);
      setConversationId(conversationIdAfterAgentError(conversationId, cause));
      const text = expiredConversation
        ? 'This conversation expired. Send another message to start a new one.'
        : cause instanceof ApiError
          ? cause.message
          : 'The request could not be completed.';
      addMessage({ role: 'assistant', text });
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    list.current?.scrollToEnd({ animated: true });
  }, [messages, busy]);

  return (
    <SafeAreaView style={styles.safe} edges={['top', 'bottom']}>
      <View style={styles.header}><Text style={styles.heading}>Study assistant</Text></View>
      <KeyboardAvoidingView
        style={styles.flex}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView
          ref={list}
          style={styles.flex}
          contentContainerStyle={styles.messages}
          keyboardShouldPersistTaps="handled">
          {messages.length === 0 && (
            <View style={styles.welcome}>
              <Text style={styles.welcomeTitle}>What would you like to study?</Text>
              <Text style={styles.muted}>Attach your material, then write what you need.</Text>
            </View>
          )}
          {messages.map((message) => (
            <View key={message.id} style={message.role === 'user' ? styles.userMessage : styles.assistantMessage}>
              <Text style={[styles.messageText, message.role === 'user' && styles.userMessageText]}>{message.text}</Text>
              {message.warnings?.map((warning, index) => (
                <Text key={`${message.id}-warning-${index}`} style={styles.warning}>{warning}</Text>
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
          {busy && <ActivityIndicator color={palette.accent} style={styles.loader} />}
        </ScrollView>
        {!!attachments.length && (
          <View style={styles.attachmentRow}>
            {attachments.map((item) => (
              <Pressable
                key={item.id}
                accessibilityRole="button"
                accessibilityLabel={`Remove ${item.name}`}
                onPress={() => setAttachments((current) => current.filter((part) => part.id !== item.id))}
                style={styles.attachmentChip}>
                <Text numberOfLines={1} style={styles.attachmentText}>{item.name}  ×</Text>
              </Pressable>
            ))}
          </View>
        )}
        {!!uploadStatus && (
          <View style={styles.uploadStatus}>
            <ActivityIndicator size="small" color={palette.accent} />
            <Text style={styles.muted}>{uploadStatus}</Text>
          </View>
        )}
        {!!error && <Text style={styles.error}>{error}</Text>}
        <View style={styles.composer}>
          <View style={styles.actions}>
            {ACTIONS.map((action) => (
              <Pressable
                key={action}
                accessibilityRole="button"
                onPress={() => setSelectedAction((current) => current === action ? null : action)}
                style={[styles.action, selectedAction === action && styles.actionSelected]}>
                <Text style={styles.actionText}>{action}</Text>
              </Pressable>
            ))}
          </View>
          <View style={styles.attachButtons}>
            <Pressable accessibilityRole="button" disabled={busy || !!uploadStatus || attachments.length >= AGENT_MAX_ATTACHMENTS} onPress={() => void attachDocument()} style={styles.attachButton}>
              <Text style={styles.attachText}>File</Text>
            </Pressable>
            <Pressable accessibilityRole="button" disabled={busy || !!uploadStatus || attachments.length >= AGENT_MAX_ATTACHMENTS} onPress={() => void attachImages()} style={styles.attachButton}>
              <Text style={styles.attachText}>Image</Text>
            </Pressable>
          </View>
          <TextInput
            accessibilityLabel="Message"
            multiline
            value={input}
            onChangeText={setInput}
            maxLength={AGENT_MAX_MESSAGE_CHARS}
            placeholder={selectedAction ? 'Add a topic or page range' : 'Write a message'}
            placeholderTextColor={palette.muted}
            style={styles.input}
            editable={!busy}
            onSubmitEditing={() => void send()}
          />
          <Pressable accessibilityRole="button" disabled={!input.trim() || busy} onPress={() => void send()} style={styles.sendButton}>
            <Text style={styles.sendText}>{busy ? '…' : 'Send'}</Text>
          </Pressable>
        </View>
      </KeyboardAvoidingView>
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

  useEffect(() => {
    if (block.type !== 'passages' || !block.result_id) return;
    let active = true;
    void loadAgentPassages(block.result_id).then(({ result, pageImages: loadedImages }) => {
      if (!active) return;
      setPassages(result);
      setPageImages(loadedImages);
    }).catch((cause) => {
      if (active) onError(cause instanceof Error ? cause.message : 'Passages could not be loaded.');
    });
    return () => { active = false; };
  }, [block.result_id, block.type, onError]);

  const openFile = async () => {
    if (!block.file_id) return;
    setOpening(true);
    try {
      await shareAgentFile(block.file_id, presentation.action?.name || 'study-file');
    } catch (cause) {
      onError(cause instanceof Error ? cause.message : 'The file could not be opened.');
    } finally {
      setOpening(false);
    }
  };

  return (
    <View style={styles.block}>
      <Text style={styles.blockTitle}>{presentation.title}</Text>
      {!!presentation.body && <Text style={styles.blockBody}>{presentation.body}</Text>}
      {!!presentation.basis && (
        <Text style={styles.blockBasis}>Basis: {presentation.basis}</Text>
      )}
      {block.type === 'passages' && passages?.topics.map((topic, topicIndex) => (
        <View key={`${topic.topic}-${topicIndex}`} style={styles.topicBlock}>
          <Text style={styles.topicTitle}>{topic.topic}</Text>
          {topic.passages.map((passage, passageIndex) => (
            <View key={`${passage.page}-${passageIndex}`} style={styles.passage}>
              {passage.page && <Text style={styles.pageLabel}>Page {passage.page}</Text>}
              <Text style={styles.passageText}>{passage.text}</Text>
              {passage.page && pageImages[passage.page] && (
                <Image source={{ uri: pageImages[passage.page] }} style={styles.pageImage} resizeMode="contain" />
              )}
            </View>
          ))}
        </View>
      ))}
      {presentation.action?.kind === 'file' && (
        <Pressable accessibilityRole="button" onPress={() => void openFile()} style={styles.blockButton}>
          <Text style={styles.blockButtonText}>{opening ? 'Opening…' : 'Open or share file'}</Text>
        </Pressable>
      )}
      {!!presentation.options.length && (
        <View style={styles.options}>
          {presentation.options.map((option, index) => (
            <Pressable key={`${option}-${index}`} accessibilityRole="button" onPress={() => onChoice(option)} style={styles.option}>
              <Text style={styles.optionText}>{option}</Text>
            </Pressable>
          ))}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.canvas },
  flex: { flex: 1 },
  header: { paddingHorizontal: spacing.lg, paddingVertical: spacing.md, borderBottomWidth: 1, borderColor: palette.line },
  heading: { color: palette.ink, fontSize: 19, fontWeight: '700' },
  messages: { padding: spacing.lg, gap: spacing.md, flexGrow: 1 },
  welcome: { paddingVertical: spacing.xl, gap: spacing.md },
  welcomeTitle: { color: palette.ink, fontSize: 22, fontWeight: '700' },
  muted: { color: palette.muted, fontSize: 14 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, marginTop: spacing.sm },
  action: { paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: palette.surface, borderWidth: 1, borderColor: palette.line, borderRadius: radii.pill },
  actionSelected: { backgroundColor: palette.accentSoft, borderColor: palette.accent },
  actionText: { color: palette.inkSoft, fontSize: 13, fontWeight: '600' },
  userMessage: { alignSelf: 'flex-end', maxWidth: '88%', backgroundColor: palette.accent, borderRadius: radii.lg, padding: spacing.md },
  assistantMessage: { alignSelf: 'stretch', backgroundColor: palette.surface, borderRadius: radii.lg, padding: spacing.md, gap: spacing.sm },
  messageText: { color: palette.ink, fontSize: 15, lineHeight: 22 },
  userMessageText: { color: palette.white },
  warning: { color: palette.amber, fontSize: 13 },
  loader: { marginVertical: spacing.md },
  attachmentRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.sm, paddingHorizontal: spacing.md },
  attachmentChip: { maxWidth: 220, backgroundColor: palette.accentSoft, borderRadius: radii.pill, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  attachmentText: { color: palette.accentInk, fontSize: 12 },
  uploadStatus: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, padding: spacing.md },
  error: { color: palette.danger, paddingHorizontal: spacing.md, fontSize: 13 },
  composer: { padding: spacing.md, gap: spacing.sm, borderTopWidth: 1, borderColor: palette.line, backgroundColor: palette.canvas },
  attachButtons: { flexDirection: 'row', gap: spacing.sm },
  attachButton: { paddingHorizontal: spacing.md, paddingVertical: spacing.xs, borderRadius: radii.pill, backgroundColor: palette.surfaceMuted },
  attachText: { color: palette.accentInk, fontWeight: '600' },
  input: { minHeight: 46, maxHeight: 120, paddingHorizontal: spacing.md, paddingVertical: spacing.sm, backgroundColor: palette.surface, borderColor: palette.line, borderWidth: 1, borderRadius: radii.md, color: palette.ink },
  sendButton: { alignSelf: 'flex-end', minWidth: 74, alignItems: 'center', paddingHorizontal: spacing.lg, paddingVertical: spacing.sm, backgroundColor: palette.accent, borderRadius: radii.pill },
  sendText: { color: palette.white, fontWeight: '700' },
  block: { marginTop: spacing.sm, padding: spacing.md, backgroundColor: palette.surfaceMuted, borderRadius: radii.md, gap: spacing.sm },
  blockTitle: { color: palette.ink, fontWeight: '700', fontSize: 14 },
  blockBody: { color: palette.inkSoft, lineHeight: 21 },
  blockBasis: { color: palette.muted, fontSize: 12 },
  blockButton: { alignSelf: 'flex-start', backgroundColor: palette.accent, borderRadius: radii.pill, paddingHorizontal: spacing.md, paddingVertical: spacing.sm },
  blockButtonText: { color: palette.white, fontWeight: '700' },
  options: { gap: spacing.sm },
  option: { padding: spacing.sm, borderWidth: 1, borderColor: palette.lineStrong, borderRadius: radii.sm, backgroundColor: palette.surface },
  optionText: { color: palette.inkSoft, fontSize: 13 },
  topicBlock: { gap: spacing.xs, marginTop: spacing.sm },
  topicTitle: { color: palette.ink, fontWeight: '700' },
  passage: { borderLeftWidth: 2, borderLeftColor: palette.sage, paddingLeft: spacing.sm, gap: spacing.xs },
  pageLabel: { color: palette.muted, fontSize: 12, fontWeight: '600' },
  passageText: { color: palette.inkSoft, fontSize: 14, lineHeight: 20 },
  pageImage: { width: '100%', height: 220, backgroundColor: palette.canvasDeep, borderRadius: radii.sm },
});
