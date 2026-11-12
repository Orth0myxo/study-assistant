import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { Image } from 'expo-image';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { Card, PrimaryButton } from '@/components/primary-button';
import { Screen } from '@/components/screen';
import {
  ApiError,
  createTopicSource,
  getTopicSource,
  updateTopicSource,
  uploadDocument,
  waitForDocument,
  type UploadFile,
} from '@/services/apiClient';
import {
  TOPIC_MAX_IMAGES,
  TOPIC_MAX_JOTTINGS_CHARS,
  validateJottingInput,
} from '@/services/topicWorkflow';
import { pickNoteImages } from '@/services/imagePicker';
import { TopicSourceResponse } from '@/types/api';

const PDF = 'application/pdf';
const DOCX =
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document';

type Busy = 'picking' | 'creating' | 'saving' | null;

const UNSUPPORTED_IMAGE_MESSAGE =
  'Only PNG and JPEG photos are supported. HEIC/HEIF photos must be converted to JPEG first.';

function isSupportedImage(asset: ImagePicker.ImagePickerAsset): boolean {
  const name = asset.fileName ?? asset.uri ?? '';
  const mime = (asset.mimeType ?? '').toLowerCase();
  if (mime === 'image/png' || mime === 'image/jpeg') {
    return true;
  }
  if (mime === 'image/heic' || mime === 'image/heif') {
    return false;
  }
  return /\.(png|jpe?g)$/i.test(name);
}

function assetToUpload(
  asset: ImagePicker.ImagePickerAsset,
  index: number,
): UploadFile {
  const fallbackName = `jotting-${Date.now()}-${index + 1}.jpg`;
  const name = asset.fileName || fallbackName;
  const mimeType =
    asset.mimeType || (/\.png$/i.test(name) ? 'image/png' : 'image/jpeg');
  return {
    uri: asset.uri,
    name,
    mimeType,
    size: asset.fileSize,
  };
}

function formatSize(bytes?: number): string {
  if (bytes === undefined) {
    return '';
  }
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function PhotoNotesScreen() {
  const router = useRouter();
  const params = useLocalSearchParams<{
    documentId?: string;
    sourceId?: string;
  }>();
  const initialDocumentId =
    typeof params.documentId === 'string' ? params.documentId : null;
  const sourceId = typeof params.sourceId === 'string' ? params.sourceId : null;

  const [documentId, setDocumentId] = useState<string | null>(
    initialDocumentId,
  );
  const [documentName, setDocumentName] = useState<string | null>(null);
  const [pendingDocument, setPendingDocument] = useState<UploadFile | null>(
    null,
  );
  const [source, setSource] = useState<TopicSourceResponse | null>(null);
  const [jottingsText, setJottingsText] = useState('');
  const [photos, setPhotos] = useState<UploadFile[]>([]);
  const [transcripts, setTranscripts] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<Busy>(null);
  const [loadingSource, setLoadingSource] = useState(Boolean(sourceId));
  const [error, setError] = useState<string | null>(null);

  const applySource = useCallback((next: TopicSourceResponse) => {
    setSource(next);
    setJottingsText(next.jottings_text ?? '');
    setTranscripts(
      Object.fromEntries(next.images.map((image) => [image.index, image.text])),
    );
    if (next.document) {
      setDocumentId(next.document.document_id);
      setDocumentName(next.document.filename);
    } else {
      setDocumentId(null);
      setDocumentName(null);
    }
    setPendingDocument(null);
  }, []);

  const loadSource = useCallback(async () => {
    if (!sourceId) {
      return;
    }
    try {
      applySource(await getTopicSource(sourceId));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'The saved jottings could not be loaded.',
      );
    } finally {
      setLoadingSource(false);
    }
  }, [applySource, sourceId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadSource();
    }, 0);
    return () => clearTimeout(timer);
  }, [loadSource]);

  const pickDocument = async () => {
    setError(null);
    setBusy('picking');
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
      setPendingDocument({
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType,
        size: asset.size,
      });
      setDocumentId(null);
      setDocumentName(null);
    } catch {
      setError('The source document could not be selected.');
    } finally {
      setBusy(null);
    }
  };

  const addAssets = useCallback(
    (assets: ImagePicker.ImagePickerAsset[]) => {
      if (assets.length === 0) {
        return;
      }
      setError(null);
      const unsupported = assets.filter((asset) => !isSupportedImage(asset));
      if (unsupported.length > 0) {
        setError(UNSUPPORTED_IMAGE_MESSAGE);
      }
      const supported = assets.filter(isSupportedImage);
      if (supported.length === 0) {
        return;
      }
      setPhotos((current) => {
        const remaining = TOPIC_MAX_IMAGES - current.length;
        if (remaining <= 0) {
          setError(`You can add up to ${TOPIC_MAX_IMAGES} photos.`);
          return current;
        }
        const existing = new Set(current.map((photo) => photo.uri));
        const next = [...current];
        for (const asset of supported) {
          if (next.length >= TOPIC_MAX_IMAGES) {
            break;
          }
          if (!existing.has(asset.uri)) {
            next.push(assetToUpload(asset, next.length));
            existing.add(asset.uri);
          }
        }
        return next;
      });
    },
    [],
  );

  const choosePhotos = async () => {
    setError(null);
    const remaining = TOPIC_MAX_IMAGES - photos.length;
    if (remaining <= 0) {
      setError(`You can add up to ${TOPIC_MAX_IMAGES} photos.`);
      return;
    }
    const result = await pickNoteImages(remaining);
    if (result.status === 'denied') {
      setError('Photo access is needed to choose lecture-note photos.');
    } else if (result.status === 'error') {
      setError('The photo picker could not be opened.');
    } else if (result.status === 'selected') {
      addAssets(result.assets);
    }
  };

  const takePhoto = async () => {
    setError(null);
    try {
      const permission = await ImagePicker.requestCameraPermissionsAsync();
      if (!permission.granted) {
        setError('Camera access is needed to photograph lecture notes.');
        return;
      }
      if (photos.length >= TOPIC_MAX_IMAGES) {
        setError(`You can add up to ${TOPIC_MAX_IMAGES} photos.`);
        return;
      }
      const result = await ImagePicker.launchCameraAsync({
        mediaTypes: ['images'],
        cameraType: ImagePicker.CameraType.back,
        quality: 0.9,
      });
      if (!result.canceled) {
        addAssets(result.assets);
      }
    } catch {
      setError('The camera could not be opened.');
    }
  };

  const removePhoto = (uri: string) => {
    setPhotos((current) => current.filter((photo) => photo.uri !== uri));
  };

  const createJottings = async () => {
    const validation = validateJottingInput(
      jottingsText,
      photos.length,
      Boolean(documentId || pendingDocument),
    );
    if (validation) {
      setError(validation);
      return;
    }

    setBusy('creating');
    setError(null);
    try {
      let activeDocumentId = documentId;
      if (!activeDocumentId) {
        if (!pendingDocument) {
          setError('Choose a PDF or DOCX source document.');
          return;
        }
        const queued = await uploadDocument(pendingDocument);
        const uploaded = await waitForDocument(queued.document_id);
        activeDocumentId = uploaded.document_id;
        setDocumentId(activeDocumentId);
        setDocumentName(uploaded.filename);
        setPendingDocument(null);
      }
      const created = await createTopicSource(
        activeDocumentId,
        photos,
        jottingsText,
      );
      applySource(created);
      setPhotos([]);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.code === 'unsupported_image_type'
            ? UNSUPPORTED_IMAGE_MESSAGE
            : err.message
          : 'The jottings could not be processed.',
      );
    } finally {
      setBusy(null);
    }
  };

  const changedTranscripts = useMemo(() => {
    if (!source) {
      return [];
    }
    return source.images
      .filter((image) => (transcripts[image.index] ?? '') !== image.text)
      .map((image) => ({
        index: image.index,
        text: transcripts[image.index] ?? '',
      }));
  }, [source, transcripts]);

  const jottingsChanged = Boolean(
    source && jottingsText !== source.jottings_text,
  );
  const hasInput =
    jottingsText.trim().length > 0 ||
    Object.values(transcripts).some((text) => text.trim().length > 0);

  const continueToTopics = async () => {
    if (!source) {
      return;
    }
    if (!hasInput) {
      setError('Add typed jottings or correct at least one photo transcript.');
      return;
    }
    setBusy('saving');
    setError(null);
    try {
      let latest = source;
      const update: {
        jottings_text?: string;
        transcripts?: { index: number; text: string }[];
      } = {};
      if (jottingsChanged) {
        update.jottings_text = jottingsText;
      }
      if (changedTranscripts.length > 0) {
        update.transcripts = changedTranscripts;
      }
      if (Object.keys(update).length > 0) {
        latest = await updateTopicSource(source.source_id, update);
        applySource(latest);
      }
      router.push({
        pathname: '/photo-topics',
        params: { sourceId: latest.source_id },
      });
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'The corrected jottings could not be saved.',
      );
    } finally {
      setBusy(null);
    }
  };

  if (loadingSource) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>Loading jottings...</Text>
          <ActivityIndicator style={styles.spinner} />
        </Card>
      </Screen>
    );
  }

  if (sourceId && !source) {
    return (
      <Screen>
        <Card>
          <Text style={styles.title}>Could not load jottings</Text>
          <Text style={styles.error}>{error ?? 'The source was not found.'}</Text>
          <PrimaryButton
            label="Try again"
            onPress={() => {
              setLoadingSource(true);
              setError(null);
              void loadSource();
            }}
          />
          <PrimaryButton
            label="Start a new set"
            onPress={() => router.replace('/photo-notes')}
          />
        </Card>
      </Screen>
    );
  }

  const sourceLabel =
    source?.document?.filename ??
    documentName ??
    (documentId ? 'Document attached' : null);

  return (
    <Screen>
      <Card>
        <Text style={styles.title}>
          {source ? sourceLabel : 'Jottings and source'}
        </Text>
        <Text style={styles.muted}>
          {source
            ? 'Review the typed jottings and photo transcripts, then continue to topic review.'
            : 'Add typed jottings, optional photos, and the PDF or DOCX they refer to. Nothing is uploaded until you create the jottings.'}
        </Text>
        {source && !source.document_available && (
          <Text style={styles.warn}>
            The source document is no longer available. You can still review the
            jottings, but topic matching will not find source passages.
          </Text>
        )}
      </Card>

      {error && (
        <Card>
          <Text style={styles.error}>{error}</Text>
        </Card>
      )}

      {!source && (
        <>
          <Card>
            <Text style={styles.sectionTitle}>Source PDF or DOCX</Text>
            <Text style={styles.muted}>
              {pendingDocument
                ? `${pendingDocument.name}${
                    pendingDocument.size
                      ? ` (${formatSize(pendingDocument.size)})`
                      : ''
                  }`
                : sourceLabel ?? 'No source document selected yet.'}
            </Text>
            <PrimaryButton
              label={
                busy === 'picking'
                  ? 'Opening picker...'
                  : pendingDocument || documentId
                    ? 'Choose a different source'
                    : 'Choose source document'
              }
              onPress={() => void pickDocument()}
              disabled={busy !== null}
            />
          </Card>

          <Card>
            <Text style={styles.sectionTitle}>Typed jottings</Text>
            <Text style={styles.muted}>
              Type or paste the topics and questions you want the source to
              explain.
            </Text>
            <TextInput
              multiline
              value={jottingsText}
              onChangeText={setJottingsText}
              placeholder="Example: Calvin cycle; light reactions; role of RuBP"
              placeholderTextColor="#647168"
              style={styles.transcriptInput}
              textAlignVertical="top"
              maxLength={TOPIC_MAX_JOTTINGS_CHARS}
            />
            <Text style={styles.meta}>
              {jottingsText.length} / {TOPIC_MAX_JOTTINGS_CHARS} characters
            </Text>
          </Card>

          <Card>
            <Text style={styles.sectionTitle}>
              Photos ({photos.length}/{TOPIC_MAX_IMAGES})
            </Text>
            <Text style={styles.muted}>
              Optional. Add PNG or JPEG photos of handwritten or printed lecture
              notes. HEIC photos must be converted to JPEG first.
            </Text>
            {photos.length === 0 ? (
              <Text style={styles.muted}>No photos selected yet.</Text>
            ) : (
              photos.map((photo, index) => (
                <View key={photo.uri} style={styles.photoRow}>
                  <Image
                    source={{ uri: photo.uri }}
                    style={styles.thumbnail}
                    contentFit="cover"
                  />
                  <View style={styles.photoText}>
                    <Text style={styles.photoName} numberOfLines={2}>
                      {photo.name}
                    </Text>
                    <Text style={styles.meta}>
                      Photo {index + 1}
                      {photo.size ? ` | ${formatSize(photo.size)}` : ''}
                    </Text>
                  </View>
                  <Pressable
                    accessibilityRole="button"
                    onPress={() => removePhoto(photo.uri)}
                    disabled={busy !== null}>
                    <Text style={styles.remove}>Remove</Text>
                  </Pressable>
                </View>
              ))
            )}
            <PrimaryButton
              label="Choose photos"
              onPress={() => void choosePhotos()}
              disabled={busy !== null}
            />
            <PrimaryButton
              label="Take a photo"
              onPress={() => void takePhoto()}
              disabled={busy !== null}
            />
          </Card>

          <PrimaryButton
            label={busy === 'creating' ? 'Reading jottings...' : 'Create jottings'}
            onPress={() => void createJottings()}
            disabled={busy !== null}
          />
        </>
      )}

      {source && (
        <>
          <Card>
            <Text style={styles.sectionTitle}>Typed jottings</Text>
            <TextInput
              multiline
              value={jottingsText}
              onChangeText={setJottingsText}
              placeholder="Type or paste the topics you want the source to explain"
              placeholderTextColor="#647168"
              style={styles.transcriptInput}
              textAlignVertical="top"
              maxLength={TOPIC_MAX_JOTTINGS_CHARS}
            />
            <Text style={styles.meta}>
              {jottingsText.length} / {TOPIC_MAX_JOTTINGS_CHARS} characters
            </Text>
          </Card>

          {source.images.length === 0 ? (
            <Card>
              <Text style={styles.sectionTitle}>Photo transcripts</Text>
              <Text style={styles.muted}>
                No photos are attached. The typed jottings will be used.
              </Text>
            </Card>
          ) : (
            source.images.map((image) => (
              <Card key={image.index}>
                <Text style={styles.photoName}>
                  Photo {image.index + 1}: {image.filename}
                </Text>
                <Text style={styles.meta}>
                  OCR: {image.ocr_status.replace(/_/g, ' ')} |{' '}
                  {image.transcript_source === 'manual'
                    ? 'edited by you'
                    : 'OCR text'}
                </Text>
                <TextInput
                  multiline
                  value={transcripts[image.index] ?? ''}
                  onChangeText={(text) =>
                    setTranscripts((current) => ({
                      ...current,
                      [image.index]: text,
                    }))
                  }
                  placeholder="Type or correct this transcript"
                  placeholderTextColor="#647168"
                  style={styles.transcriptInput}
                  textAlignVertical="top"
                />
                <Text style={styles.meta}>
                  {(transcripts[image.index] ?? '').length} / 50000 characters
                </Text>
                {image.warnings.map((warning, index) => (
                  <Text key={`${warning.code}-${index}`} style={styles.warn}>
                    {warning.message}
                  </Text>
                ))}
              </Card>
            ))
          )}

          {source.warnings.map((warning, index) => (
            <Card key={`${warning.code}-${index}`}>
              <Text style={styles.warn}>{warning.message}</Text>
            </Card>
          ))}

          <PrimaryButton
            label={
              busy === 'saving'
                ? 'Saving jottings...'
                : 'Continue to topic review'
            }
            onPress={() => void continueToTopics()}
            disabled={busy !== null || !hasInput}
          />
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  title: {
    fontSize: 16,
    fontWeight: '600',
    color: '#1C2A22',
  },
  sectionTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: '#1C2A22',
  },
  muted: {
    color: '#5E6B61',
    fontSize: 14,
    lineHeight: 20,
  },
  meta: {
    color: '#5E6B61',
    fontSize: 12,
    lineHeight: 17,
  },
  error: {
    color: '#9B3F34',
    fontSize: 14,
    lineHeight: 20,
  },
  warn: {
    color: '#94601F',
    fontSize: 13,
    lineHeight: 19,
  },
  spinner: {
    marginTop: 4,
  },
  photoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    paddingVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#DFD8C8',
  },
  thumbnail: {
    width: 54,
    height: 54,
    borderRadius: 8,
    backgroundColor: '#e8eef5',
  },
  photoText: {
    flex: 1,
    gap: 2,
  },
  photoName: {
    color: '#1C2A22',
    fontSize: 14,
    fontWeight: '600',
  },
  remove: {
    color: '#9B3F34',
    fontSize: 13,
    fontWeight: '600',
    padding: 6,
  },
  transcriptInput: {
    minHeight: 130,
    borderWidth: 1,
    borderColor: '#cfd9e4',
    borderRadius: 8,
    padding: 10,
    color: '#1C2A22',
    fontSize: 14,
    lineHeight: 20,
    backgroundColor: '#fbfcfe',
  },
});
