import * as ImagePicker from 'expo-image-picker';
import * as Sharing from 'expo-sharing';
import { Image } from 'expo-image';
import { useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PrimaryButton } from '@/components/primary-button';
import {
  ApiError,
  createTopicSource,
  deleteTableResult,
  downloadTableFile,
  getTableExport,
  readTableImages,
  readTableText,
  startTableExport,
  type UploadFile,
} from '@/services/apiClient';
import { displayFont, palette, radii, spacing } from '@/theme';
import {
  TableEngine,
  TableExportFormat,
  TableResultResponse,
} from '@/types/api';

const UNSUPPORTED_IMAGE_MESSAGE =
  'Only PNG and JPEG photos are supported. Convert HEIC/HEIF photos to JPEG first.';
const POLL_MS = 800;
const MAX_POLLS = 120;
const CELL_WIDTH = 132;

type Busy = 'picking' | 'reading' | 'exporting' | null;

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
  index = 0,
): UploadFile {
  const fallbackName = `table-photo-${Date.now()}-${index + 1}.jpg`;
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

function emptyRow(width: number): string[] {
  return Array.from({ length: width }, () => '');
}

function emptyFlags(width: number): boolean[] {
  return Array.from({ length: width }, () => false);
}

function placeholderColumns(width: number): string[] {
  return Array.from({ length: width }, (_, index) => `Column ${index + 1}`);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function TableConverterScreen() {
  const params = useLocalSearchParams<{
    sourceId?: string;
    imageIndex?: string;
  }>();
  const initialSourceId =
    typeof params.sourceId === 'string' ? params.sourceId : null;
  const initialImageIndex =
    typeof params.imageIndex === 'string' && !Number.isNaN(Number(params.imageIndex))
      ? Number(params.imageIndex)
      : 0;

  const [previewUri, setPreviewUri] = useState<string | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(initialSourceId);
  const [imageIndices, setImageIndices] = useState<number[]>([
    initialImageIndex,
  ]);
  const [result, setResult] = useState<TableResultResponse | null>(null);
  const [title, setTitle] = useState('');
  const [columns, setColumns] = useState<string[]>([]);
  const [rows, setRows] = useState<string[][]>([]);
  const [lowConfidence, setLowConfidence] = useState<boolean[][]>([]);
  const [hasHeader, setHasHeader] = useState(true);
  const [engine, setEngine] = useState<TableEngine>('local');
  const [typedText, setTypedText] = useState('');
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [tablePreviewOpen, setTablePreviewOpen] = useState(false);

  const lowCount = useMemo(
    () => lowConfidence.reduce((total, row) => total + row.filter(Boolean).length, 0),
    [lowConfidence],
  );

  const applyResult = (next: TableResultResponse) => {
    setResult(next);
    setTitle(next.title ?? '');
    setColumns(next.columns);
    setRows(next.rows);
    setLowConfidence(
      next.low_confidence.length > 0
        ? next.low_confidence
        : next.rows.map((row) => emptyFlags(row.length)),
    );
    setHasHeader(next.has_header ?? true);
    setEngine(next.engine);
  };

  const resetTable = () => {
    setResult(null);
    setTitle('');
    setSourceId(null);
    setImageIndices([]);
    setPreviewUri(null);
    setExportStatus(null);
  };

  const selectAsset = async (useCamera: boolean) => {
    setError(null);
    setBusy('picking');
    try {
      if (useCamera) {
        const permission = await ImagePicker.requestCameraPermissionsAsync();
        if (!permission.granted) {
          setError('Camera permission is needed to photograph a table.');
          return;
        }
        const picked = await ImagePicker.launchCameraAsync({
          mediaTypes: ['images'],
          quality: 0.9,
        });
        if (picked.canceled || !picked.assets?.[0]) {
          return;
        }
        await readAssets(picked.assets.slice(0, 1), 'local');
        return;
      }
      const picked = await ImagePicker.launchImageLibraryAsync({
        mediaTypes: ['images'],
        allowsMultipleSelection: true,
        quality: 0.9,
      });
      if (picked.canceled || !picked.assets?.length) {
        return;
      }
      await readAssets(picked.assets, 'local');
    } catch {
      setError('The photo could not be selected.');
      setBusy(null);
    }
  };

  const readAssets = async (
    assets: ImagePicker.ImagePickerAsset[],
    nextEngine: TableEngine,
  ) => {
    if (assets.some((asset) => !isSupportedImage(asset))) {
      setError(UNSUPPORTED_IMAGE_MESSAGE);
      setBusy(null);
      return;
    }
    setBusy('reading');
    setResult(null);
    setExportStatus(null);
    setPreviewUri(assets[0]?.uri ?? null);
    try {
      const ownedSourceId = (
        await createTopicSource(
          null,
          assets.map((asset, index) => assetToUpload(asset, index)),
          '',
          true,
          'table',
        )
      ).source_id;
      setSourceId(ownedSourceId);
      const indices = assets.map((_, index) => index);
      setImageIndices(indices);
      const table = await readTableImages(ownedSourceId, indices, nextEngine);
      applyResult(table);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'The table could not be read from these photos.',
      );
    } finally {
      setBusy(null);
    }
  };

  const readTypedText = async () => {
    const value = typedText.trim();
    if (!value) {
      setError('Type or paste table text first.');
      return;
    }
    setBusy('reading');
    setError(null);
    setResult(null);
    setSourceId(null);
    setImageIndices([]);
    setPreviewUri(null);
    setExportStatus(null);
    try {
      applyResult(await readTableText(value));
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'The typed text could not be split into a table.',
      );
    } finally {
      setBusy(null);
    }
  };

  const readWithAi = () => {
    if (!sourceId || imageIndices.length === 0) {
      setError('Choose a photo first.');
      return;
    }
    Alert.alert(
      'Read with AI?',
      'The selected photos will be sent to Google Gemini so it can read the table. Table contents leave your server. Local reading does not send the photos to a provider. The current edited grid will be replaced.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Send photos',
          style: 'destructive',
          onPress: () => {
            setBusy('reading');
            setError(null);
            setExportStatus(null);
            readTableImages(sourceId, imageIndices, 'ai')
              .then(applyResult)
              .catch((err) =>
                setError(
                  err instanceof ApiError
                    ? err.message
                    : 'The AI provider could not read these photos.',
                ),
              )
              .finally(() => setBusy(null));
          },
        },
      ],
    );
  };

  const updateCell = (rowIndex: number, columnIndex: number, value: string) => {
    setRows((current) =>
      current.map((row, index) =>
        index === rowIndex
          ? row.map((cell, cellIndex) =>
              cellIndex === columnIndex ? value : cell,
            )
          : row,
      ),
    );
    setLowConfidence((current) =>
      current.map((row, index) =>
        index === rowIndex
          ? row.map((flag, cellIndex) =>
              cellIndex === columnIndex ? false : flag,
            )
          : row,
      ),
    );
  };

  const updateColumn = (columnIndex: number, value: string) => {
    setColumns((current) =>
      current.map((column, index) => (index === columnIndex ? value : column)),
    );
  };

  const setHeaderMode = (next: boolean) => {
    if (next === hasHeader) {
      return;
    }
    if (next) {
      if (rows.length === 0) {
        setError('Add a row before making it the heading.');
        return;
      }
      setColumns(rows[0]);
      setRows(rows.slice(1));
      setLowConfidence(lowConfidence.slice(1));
    } else {
      setRows([columns, ...rows]);
      setLowConfidence([emptyFlags(columns.length), ...lowConfidence]);
      setColumns(placeholderColumns(columns.length));
    }
    setHasHeader(next);
  };

  const addRow = () => {
    setRows((current) => [...current, emptyRow(columns.length)]);
    setLowConfidence((current) => [...current, emptyFlags(columns.length)]);
  };

  const removeRow = () => {
    setRows((current) => current.slice(0, -1));
    setLowConfidence((current) => current.slice(0, -1));
  };

  const addColumn = () => {
    setColumns((current) => [
      ...current,
      `Column ${current.length + 1}`,
    ]);
    setRows((current) => current.map((row) => [...row, '']));
    setLowConfidence((current) => current.map((row) => [...row, false]));
  };

  const removeColumn = () => {
    if (columns.length <= 1) {
      setError('A table needs at least one column.');
      return;
    }
    setColumns((current) => current.slice(0, -1));
    setRows((current) => current.map((row) => row.slice(0, -1)));
    setLowConfidence((current) => current.map((row) => row.slice(0, -1)));
  };

  const exportTable = async (format: TableExportFormat) => {
    if (!result) {
      return;
    }
    setBusy('exporting');
    setExportOpen(false);
    setError(null);
    setExportStatus('Preparing export...');
    try {
      let job = await startTableExport(
        result.result_id,
        format,
        columns,
        rows,
        title,
      );
      for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
        if (job.status === 'completed') {
          break;
        }
        if (job.status === 'failed') {
          throw new ApiError(
            job.error?.message ?? 'The export could not be generated.',
            job.error?.code ?? 'table_export_failed',
            0,
          );
        }
        await sleep(POLL_MS);
        job = await getTableExport(job.job_id);
      }
      if (job.status !== 'completed') {
        job = await getTableExport(job.job_id);
      }
      if (job.status !== 'completed' || !job.file_id) {
        throw new ApiError(
          job.status === 'failed'
            ? job.error?.message ?? 'The export could not be generated.'
            : 'The export is still being prepared. Please try again.',
          job.status === 'failed'
            ? job.error?.code ?? 'table_export_failed'
            : 'table_export_timeout',
          0,
        );
      }
      setExportStatus('Downloading file...');
      const uri = await downloadTableFile(
        job.file_id,
        job.name ?? `table.${format}`,
      );
      if (await Sharing.isAvailableAsync()) {
        await Sharing.shareAsync(uri, {
          mimeType:
            format === 'csv'
              ? 'text/csv'
              : format === 'xlsx'
                ? 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
          dialogTitle: 'Share table export',
          UTI:
            format === 'csv'
              ? 'public.comma-separated-values-text'
              : format === 'xlsx'
                ? 'org.openxmlformats.spreadsheetml.sheet'
                : 'org.openxmlformats.wordprocessingml.document',
        });
        setExportStatus('File ready.');
      } else {
        setExportStatus(`Saved to ${uri}`);
      }
    } catch (err) {
      setExportStatus(null);
      setError(
        err instanceof ApiError
          ? err.message
          : 'The export could not be prepared.',
      );
    } finally {
      setBusy(null);
    }
  };

  const reading = busy === 'reading';
  const picking = busy === 'picking';

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <View style={styles.header}>
        <Text style={styles.title}>Photo to table</Text>
        <Text style={styles.subtitle}>
          Read a table from photos or typed text, check every cell, then export
          it. Local reading does not send photos to an AI provider.
        </Text>
      </View>

      {error ? (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      ) : null}

      {!result ? (
        <ScrollView
          contentContainerStyle={styles.emptyContent}
          keyboardShouldPersistTaps="handled">
          {previewUri ? (
            <Image source={{ uri: previewUri }} style={styles.preview} contentFit="contain" />
          ) : (
            <View style={styles.previewPlaceholder}>
              <Text style={styles.previewPlaceholderText}>No photo selected</Text>
            </View>
          )}
          {reading ? (
            <View style={styles.readingBox}>
              <ActivityIndicator color={palette.accent} />
              <Text style={styles.readingText}>
                Reading the table on the server. Long lists can take a while.
              </Text>
            </View>
          ) : (
            <>
              <PrimaryButton
                label="Choose photos"
                disabled={picking}
                onPress={() => void selectAsset(false)}
              />
              <PrimaryButton
                label="Take a photo"
                variant="secondary"
                disabled={picking}
                onPress={() => void selectAsset(true)}
              />
              {sourceId ? (
                <PrimaryButton
                  label="Read with AI"
                  variant="secondary"
                  disabled={picking}
                  onPress={readWithAi}
                />
              ) : null}
              {picking ? <ActivityIndicator color={palette.accent} /> : null}
              <View style={styles.textEntry}>
                <Text style={styles.textEntryLabel}>Or paste table text</Text>
                <TextInput
                  value={typedText}
                  onChangeText={setTypedText}
                  style={styles.textEntryInput}
                  multiline
                  textAlignVertical="top"
                  placeholder="One row per line. Separate columns with tabs, commas, or spaces."
                  accessibilityLabel="Table text"
                />
                <PrimaryButton
                  label="Use typed text"
                  variant="secondary"
                  disabled={picking || reading}
                  onPress={() => void readTypedText()}
                />
              </View>
            </>
          )}
        </ScrollView>
      ) : (
        <>
          <View style={styles.metaRow}>
            <View style={styles.metaCopy}>
              <Text style={styles.metaTitle}>
                {rows.length} {rows.length === 1 ? 'row' : 'rows'} ·{' '}
                {columns.length}{' '}
                {columns.length === 1 ? 'column' : 'columns'}
              </Text>
              <Text style={styles.metaNote}>
                {engine === 'text'
                  ? 'Split from typed or pasted text. Review every cell.'
                  : 'Read from an image, not copied source text. Review every cell.'}
              </Text>
              {lowCount > 0 ? (
                <Text style={styles.lowNote}>
                  {lowCount} {lowCount === 1 ? 'cell needs' : 'cells need'} a
                  check (marked).
                </Text>
              ) : null}
              <Text style={styles.engineNote}>
                {engine === 'text'
                  ? 'Typed or pasted text'
                  : engine === 'ai'
                    ? 'Read with AI'
                    : 'Read locally with OCR'}
              </Text>
            </View>
            <View style={styles.metaActions}>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Choose a different table photo"
                onPress={resetTable}
                style={styles.changeButton}>
                <Text style={styles.changeLabel}>Change</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Delete this table result"
                onPress={() => {
                  const resultId = result.result_id;
                  resetTable();
                  void deleteTableResult(resultId).catch(() => undefined);
                }}
                style={styles.changeButton}>
                <Text style={styles.deleteLabel}>Delete</Text>
              </Pressable>
            </View>
          </View>

          <View style={styles.tableOptions}>
            <TextInput
              value={title}
              onChangeText={setTitle}
              style={styles.titleInput}
              placeholder="Table title (optional)"
              accessibilityLabel="Table title"
            />
            <View style={styles.headerToggle}>
              <Text style={styles.headerToggleLabel}>First row is a heading</Text>
              <Switch
                value={hasHeader}
                onValueChange={setHeaderMode}
                accessibilityLabel="First row is a heading"
              />
            </View>
          </View>

          <ScrollView
            horizontal
            style={styles.gridHorizontal}
            contentContainerStyle={styles.gridHorizontalContent}
            keyboardShouldPersistTaps="handled">
            <View>
              <View style={styles.gridRow}>
                <View style={[styles.rowNumberCell, styles.headerCell]}>
                  <Text style={styles.headerText}>#</Text>
                </View>
                {columns.map((column, columnIndex) => (
                  <View key={`heading-${columnIndex}`} style={[styles.cell, styles.headerCell]}>
                    <TextInput
                      value={column}
                      onChangeText={(value) => updateColumn(columnIndex, value)}
                      style={styles.headerInput}
                      selectTextOnFocus
                      accessibilityLabel={`Column ${columnIndex + 1} heading`}
                    />
                  </View>
                ))}
              </View>
              <ScrollView
                nestedScrollEnabled
                style={styles.gridVertical}
                keyboardShouldPersistTaps="handled">
                {rows.map((row, rowIndex) => (
                  <View key={`row-${rowIndex}`} style={styles.gridRow}>
                    <View style={styles.rowNumberCell}>
                      <Text style={styles.rowNumber}>{rowIndex + 1}</Text>
                    </View>
                    {row.map((cell, columnIndex) => {
                      const low = lowConfidence[rowIndex]?.[columnIndex];
                      return (
                        <View
                          key={`cell-${rowIndex}-${columnIndex}`}
                          style={[styles.cell, low ? styles.lowCell : null]}>
                          <TextInput
                            value={cell}
                            onChangeText={(value) =>
                              updateCell(rowIndex, columnIndex, value)
                            }
                            style={styles.cellInput}
                            multiline
                            selectTextOnFocus
                            accessibilityLabel={`Row ${rowIndex + 1}, column ${
                              columns[columnIndex] || columnIndex + 1
                            }${low ? ', low confidence' : ''}`}
                          />
                        </View>
                      );
                    })}
                  </View>
                ))}
                {rows.length === 0 ? (
                  <Text style={styles.noRows}>No data rows. Add one below.</Text>
                ) : null}
              </ScrollView>
            </View>
          </ScrollView>

          <View style={styles.gridActions}>
            <Pressable onPress={addRow} style={styles.smallButton}>
              <Text style={styles.smallButtonText}>+ Row</Text>
            </Pressable>
            <Pressable onPress={removeRow} style={styles.smallButton}>
              <Text style={styles.smallButtonText}>- Row</Text>
            </Pressable>
            <Pressable onPress={addColumn} style={styles.smallButton}>
              <Text style={styles.smallButtonText}>+ Column</Text>
            </Pressable>
            <Pressable onPress={removeColumn} style={styles.smallButton}>
              <Text style={styles.smallButtonText}>- Column</Text>
            </Pressable>
            {sourceId ? (
              <Pressable
                onPress={readWithAi}
                disabled={busy !== null}
                style={[styles.smallButton, busy ? styles.disabled : null]}>
                <Text style={styles.smallButtonText}>Read with AI</Text>
              </Pressable>
            ) : null}
          </View>

          {exportStatus ? (
            <Text style={styles.exportStatus}>{exportStatus}</Text>
          ) : null}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Preview table before export"
            onPress={() => setTablePreviewOpen(true)}
            style={styles.previewTableButton}>
            <Text style={styles.previewTableButtonText}>Preview table</Text>
          </Pressable>
          <View style={styles.exportArea}>
            {exportOpen ? (
              <View style={styles.exportChoices}>
                {(['xlsx', 'docx', 'csv'] as const).map((format) => (
                  <Pressable
                    key={format}
                    disabled={busy !== null}
                    onPress={() => void exportTable(format)}
                    style={[
                      styles.exportChoice,
                      busy ? styles.disabled : null,
                    ]}>
                    <Text style={styles.exportChoiceText}>
                      {format === 'xlsx'
                        ? 'Excel'
                        : format === 'docx'
                          ? 'Word table'
                          : 'CSV'}
                    </Text>
                  </Pressable>
                ))}
              </View>
            ) : null}
            <PrimaryButton
              label={busy === 'exporting' ? 'Preparing export...' : 'Export'}
              disabled={busy !== null}
              onPress={() => setExportOpen((open) => !open)}
            />
          </View>
        </>
      )}
      <Modal
        visible={tablePreviewOpen}
        transparent
        animationType="slide"
        onRequestClose={() => setTablePreviewOpen(false)}>
        <View style={styles.previewOverlay}>
          <SafeAreaView style={styles.previewSheet}>
            <View style={styles.previewHeader}>
              <Text style={styles.previewTitle}>Table preview</Text>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Close table preview"
                onPress={() => setTablePreviewOpen(false)}
                style={styles.previewClose}>
                <Text style={styles.previewCloseText}>Close</Text>
              </Pressable>
            </View>
            {title.trim() ? (
              <Text style={styles.previewTableTitle}>{title}</Text>
            ) : null}
            <Text style={styles.previewMeta}>
              {columns.length} {columns.length === 1 ? 'column' : 'columns'} ·{' '}
              {rows.length} {rows.length === 1 ? 'row' : 'rows'}
            </Text>
            <ScrollView horizontal contentContainerStyle={styles.previewTableScroll}>
              <View>
                <View style={[styles.previewTableRow, styles.previewTableHead]}>
                  {columns.map((column, index) => (
                    <Text
                      key={`preview-head-${index}`}
                      style={[styles.previewCell, styles.previewHeadCell]}
                      numberOfLines={2}>
                      {column}
                    </Text>
                  ))}
                </View>
                {rows.map((row, rowIndex) => (
                  <View
                    key={`preview-row-${rowIndex}`}
                    style={styles.previewTableRow}>
                    {row.map((cell, cellIndex) => (
                      <Text
                        key={`preview-cell-${rowIndex}-${cellIndex}`}
                        style={styles.previewCell}
                        numberOfLines={4}>
                        {cell}
                      </Text>
                    ))}
                  </View>
                ))}
              </View>
            </ScrollView>
            <Text style={styles.previewNote}>
              This preview uses the table content currently on this screen.
            </Text>
          </SafeAreaView>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: palette.canvas,
  },
  header: {
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.lg,
    paddingBottom: spacing.md,
    gap: spacing.xs,
  },
  title: {
    fontFamily: displayFont,
    fontSize: 25,
    fontWeight: '600',
    color: palette.ink,
  },
  subtitle: {
    color: palette.inkSoft,
    fontSize: 14,
    lineHeight: 20,
  },
  errorBox: {
    marginHorizontal: spacing.lg,
    marginBottom: spacing.md,
    backgroundColor: palette.dangerSoft,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  errorText: {
    color: palette.danger,
    fontSize: 14,
    lineHeight: 20,
  },
  emptyContent: {
    padding: spacing.lg,
    gap: spacing.md,
  },
  preview: {
    width: '100%',
    height: 210,
    borderRadius: radii.lg,
    backgroundColor: palette.surfaceMuted,
  },
  previewPlaceholder: {
    width: '100%',
    height: 210,
    borderRadius: radii.lg,
    borderWidth: 1,
    borderColor: palette.line,
    backgroundColor: palette.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  previewPlaceholderText: {
    color: palette.muted,
    fontSize: 14,
  },
  readingBox: {
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.lg,
  },
  readingText: {
    color: palette.inkSoft,
    textAlign: 'center',
    lineHeight: 20,
  },
  textEntry: {
    gap: spacing.sm,
    paddingTop: spacing.md,
    borderTopWidth: 1,
    borderTopColor: palette.line,
  },
  textEntryLabel: {
    color: palette.inkSoft,
    fontSize: 14,
    fontWeight: '600',
  },
  textEntryInput: {
    minHeight: 110,
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.md,
    backgroundColor: palette.surface,
    color: palette.ink,
    fontSize: 14,
    lineHeight: 20,
    padding: spacing.md,
  },
  tableOptions: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.sm,
    gap: spacing.sm,
  },
  titleInput: {
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.md,
    backgroundColor: palette.surface,
    color: palette.ink,
    fontSize: 15,
    fontWeight: '600',
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.sm,
  },
  headerToggle: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  headerToggleLabel: {
    color: palette.inkSoft,
    fontSize: 13,
  },
  metaRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.sm,
    gap: spacing.sm,
  },
  metaCopy: {
    flex: 1,
    gap: 2,
  },
  metaTitle: {
    fontWeight: '600',
    fontSize: 15,
    color: palette.ink,
  },
  metaNote: {
    fontSize: 12,
    lineHeight: 17,
    color: palette.muted,
  },
  lowNote: {
    fontSize: 12,
    lineHeight: 17,
    color: palette.amber,
    fontWeight: '600',
  },
  engineNote: {
    fontSize: 11,
    color: palette.faint,
  },
  metaActions: {
    gap: spacing.xs,
    alignItems: 'stretch',
  },
  changeButton: {
    borderRadius: radii.pill,
    borderWidth: 1,
    borderColor: palette.lineStrong,
    paddingHorizontal: spacing.md,
    paddingVertical: spacing.xs,
  },
  changeLabel: {
    fontSize: 13,
    color: palette.accentInk,
    fontWeight: '600',
    textAlign: 'center',
  },
  deleteLabel: {
    fontSize: 13,
    color: palette.danger,
    fontWeight: '600',
    textAlign: 'center',
  },
  gridHorizontal: {
    flex: 1,
    marginHorizontal: spacing.lg,
    borderWidth: 1,
    borderColor: palette.lineStrong,
    borderRadius: radii.md,
    backgroundColor: palette.surface,
  },
  gridHorizontalContent: {
    flexGrow: 1,
  },
  gridVertical: {
    maxHeight: 420,
  },
  gridRow: {
    flexDirection: 'row',
  },
  rowNumberCell: {
    width: 38,
    minHeight: 48,
    borderRightWidth: 1,
    borderBottomWidth: 1,
    borderColor: palette.line,
    backgroundColor: palette.surfaceMuted,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rowNumber: {
    fontSize: 12,
    color: palette.muted,
  },
  cell: {
    width: CELL_WIDTH,
    minHeight: 48,
    borderRightWidth: 1,
    borderBottomWidth: 1,
    borderColor: palette.line,
  },
  headerCell: {
    backgroundColor: palette.accentSoft,
  },
  headerText: {
    fontSize: 12,
    color: palette.accentInk,
    fontWeight: '700',
  },
  headerInput: {
    minHeight: 48,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
    fontSize: 13,
    fontWeight: '700',
    color: palette.accentInk,
  },
  cellInput: {
    minHeight: 48,
    minWidth: CELL_WIDTH - 2,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.sm,
    fontSize: 14,
    color: palette.ink,
  },
  lowCell: {
    backgroundColor: palette.amberSoft,
  },
  noRows: {
    padding: spacing.md,
    color: palette.muted,
    fontSize: 13,
  },
  gridActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
    paddingHorizontal: spacing.lg,
    paddingVertical: spacing.sm,
  },
  smallButton: {
    borderWidth: 1,
    borderColor: palette.lineStrong,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.md,
    paddingVertical: 6,
  },
  smallButtonText: {
    fontSize: 12,
    color: palette.accentInk,
    fontWeight: '600',
  },
  disabled: {
    opacity: 0.45,
  },
  exportStatus: {
    paddingHorizontal: spacing.lg,
    color: palette.inkSoft,
    fontSize: 12,
  },
  exportArea: {
    padding: spacing.lg,
    gap: spacing.sm,
  },
  exportChoices: {
    flexDirection: 'row',
    gap: spacing.xs,
  },
  exportChoice: {
    flex: 1,
    borderWidth: 1,
    borderColor: palette.lineStrong,
    borderRadius: radii.md,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    backgroundColor: palette.surface,
  },
  exportChoiceText: {
    fontSize: 13,
    fontWeight: '600',
    color: palette.accentInk,
  },
  previewTableButton: {
    marginHorizontal: spacing.lg,
    marginBottom: spacing.sm,
    minHeight: 44,
    justifyContent: 'center',
    alignItems: 'center',
    borderRadius: radii.md,
    borderWidth: 1,
    borderColor: palette.lineStrong,
    backgroundColor: palette.surface,
  },
  previewTableButtonText: {
    color: palette.inkSoft,
    fontSize: 13,
    fontWeight: '700',
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
  previewTableTitle: {
    color: palette.ink,
    fontSize: 16,
    fontWeight: '700',
  },
  previewMeta: {
    color: palette.muted,
    fontSize: 11,
  },
  previewTableScroll: {
    paddingVertical: spacing.sm,
  },
  previewTableRow: {
    flexDirection: 'row',
  },
  previewTableHead: {
    backgroundColor: palette.accentSoft,
  },
  previewCell: {
    width: 132,
    minHeight: 36,
    padding: spacing.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.line,
    color: palette.inkSoft,
    fontSize: 12,
    lineHeight: 17,
  },
  previewHeadCell: {
    color: palette.accentInk,
    fontWeight: '700',
  },
  previewNote: {
    color: palette.muted,
    fontSize: 11,
    lineHeight: 16,
  },
});
