import * as DocumentPicker from 'expo-document-picker';
import * as Sharing from 'expo-sharing';
import { useRouter } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
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
  downloadConversionFile,
  exportConversionTable,
  getConversionFormats,
  getConversionJob,
  startConversion,
  type UploadFile,
} from '@/services/apiClient';
import { displayFont, palette, radii, spacing } from '@/theme';
import {
  ConversionInputKind,
  ConversionJob,
  ConversionOption,
  ConversionFormatsResponse,
} from '@/types/api';

const POLL_MS = 800;
const MAX_POLLS = 300;
const CELL_WIDTH = 148;

const FILE_MIME: Record<Exclude<ConversionInputKind, 'text'>, string[]> = {
  csv: [
    'text/csv',
    'text/plain',
    'text/tab-separated-values',
    'text/comma-separated-values',
    'application/csv',
  ],
  xlsx: [
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  ],
  docx: [
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  ],
  pdf: ['application/pdf'],
};

type Phase = 'idle' | 'starting' | 'running' | 'editing' | 'exporting' | 'done';
type PendingInput = {
  conversionId: string;
  file: UploadFile | null;
  text: string | null;
  sheet: string | null;
  useAi: boolean;
};

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatBytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FileConverterScreen() {
  const router = useRouter();
  const runToken = useRef(0);
  const [formats, setFormats] = useState<ConversionFormatsResponse | null>(null);
  const [formatsError, setFormatsError] = useState<string | null>(null);
  const [inputKind, setInputKind] = useState<ConversionInputKind>('pdf');
  const [selected, setSelected] = useState<ConversionOption | null>(null);
  const [file, setFile] = useState<UploadFile | null>(null);
  const [text, setText] = useState('');
  const [sheet, setSheet] = useState<string | null>(null);
  const [aiConsent, setAiConsent] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [job, setJob] = useState<ConversionJob | null>(null);
  const [columns, setColumns] = useState<string[]>([]);
  const [rows, setRows] = useState<string[][]>([]);
  const [tableTitle, setTableTitle] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sheetPrompt, setSheetPrompt] = useState<{
    sheets: string[];
    pending: PendingInput;
  } | null>(null);

  useEffect(() => {
    let active = true;
    void getConversionFormats()
      .then((next) => {
        if (!active) return;
        setFormats(next);
        const preferred =
          next.inputs.find((item) => item.kind === 'pdf') ?? next.inputs[0];
        if (preferred) {
          setInputKind(preferred.kind);
          setSelected(preferred.conversions[0] ?? null);
        }
      })
      .catch((err) => {
        if (active) {
          setFormatsError(
            err instanceof ApiError
              ? err.message
              : 'The converter options could not be loaded.',
          );
        }
      });
    return () => {
      active = false;
      runToken.current += 1;
    };
  }, []);

  const currentInput = useMemo(
    () => formats?.inputs.find((item) => item.kind === inputKind) ?? null,
    [formats, inputKind],
  );

  const resetOutput = () => {
    runToken.current += 1;
    setJob(null);
    setColumns([]);
    setRows([]);
    setTableTitle('');
    setMessage(null);
    setError(null);
    setPhase('idle');
  };

  const selectInputKind = (kind: ConversionInputKind) => {
    const next = formats?.inputs.find((item) => item.kind === kind);
    setInputKind(kind);
    setSelected(next?.conversions[0] ?? null);
    setFile(null);
    setSheet(null);
    setAiConsent(false);
    resetOutput();
  };

  const selectConversion = (option: ConversionOption) => {
    setSelected(option);
    setAiConsent(false);
    setSheet(null);
    resetOutput();
  };

  const chooseFile = async () => {
    setError(null);
    if (inputKind === 'text') return;
    try {
      const result = await DocumentPicker.getDocumentAsync({
        type: FILE_MIME[inputKind],
        copyToCacheDirectory: true,
        multiple: false,
      });
      if (result.canceled) return;
      const asset = result.assets?.[0];
      if (!asset) return;
      setFile({
        uri: asset.uri,
        name: asset.name,
        mimeType: asset.mimeType,
        size: asset.size,
      });
      setSheet(null);
      resetOutput();
    } catch {
      setError('The file could not be selected.');
    }
  };

  const applyJob = (next: ConversionJob, pending: PendingInput) => {
    setJob(next);
    if (next.status === 'failed') {
      if (
        next.error?.code === 'sheet_choice_required' &&
        Array.isArray(next.error.details.sheets)
      ) {
        setSheetPrompt({
          sheets: next.error.details.sheets.map(String),
          pending,
        });
        setPhase('idle');
        return;
      }
      setError(next.error?.message ?? 'The file could not be converted.');
      setPhase('idle');
      return;
    }
    if (next.status !== 'completed') return;
    setSheet(null);
    if (next.table) {
      setColumns(next.table.columns);
      setRows(next.table.rows.map((row) => [...row]));
      setTableTitle(next.table.title);
      setMessage(
        next.needs_ai
          ? 'AI-generated summary. Review and edit every cell, then export the table.'
          : null,
      );
      setPhase('editing');
      return;
    }
    setPhase('done');
  };

  const waitForJob = async (
    jobId: string,
    pending: PendingInput,
    token: number,
  ) => {
    for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
      await sleep(POLL_MS);
      if (runToken.current !== token) return;
      const next = await getConversionJob(jobId);
      if (runToken.current !== token) return;
      setJob(next);
      if (next.status === 'completed' || next.status === 'failed') {
        applyJob(next, pending);
        return;
      }
    }
    throw new ApiError(
      'The conversion is taking longer than expected. Please try again.',
      'conversion_timeout',
      0,
    );
  };

  const run = async (pending: PendingInput, sheetOverride?: string | null) => {
    const token = runToken.current + 1;
    runToken.current = token;
    setError(null);
    setMessage(null);
    setJob(null);
    setColumns([]);
    setRows([]);
    setTableTitle('');
    setPhase('starting');
    const request = {
      ...pending,
      sheet: sheetOverride === undefined ? pending.sheet : sheetOverride,
    };
    try {
      const started = await startConversion({
        conversionId: request.conversionId,
        file: request.file,
        text: request.text,
        sheet: request.sheet,
        useAi: request.useAi,
      });
      if (runToken.current !== token) return;
      setJob(started);
      setPhase('running');
      await waitForJob(started.job_id, request, token);
    } catch (err) {
      if (runToken.current !== token) return;
      setError(
        err instanceof ApiError
          ? err.message
          : 'The file could not be converted.',
      );
      setPhase('idle');
    }
  };

  const startSelected = () => {
    if (!selected) {
      setError('Choose a conversion first.');
      return;
    }
    if (selected.input_mode === 'text' && !text.trim()) {
      setError('Paste some text first.');
      return;
    }
    if (selected.input_mode === 'file' && !file) {
      setError('Choose a file first.');
      return;
    }
    if (selected.needs_ai && !aiConsent) {
      setError('Confirm the AI notice before creating the summary table.');
      return;
    }
    const pending: PendingInput = {
      conversionId: selected.conversion_id,
      file: selected.input_mode === 'file' ? file : null,
      text: selected.input_mode === 'text' ? text : null,
      sheet,
      useAi: selected.needs_ai && aiConsent,
    };
    void run(pending);
  };

  const chooseSheet = (name: string) => {
    if (!sheetPrompt) return;
    const pending = sheetPrompt.pending;
    setSheetPrompt(null);
    setSheet(name);
    void run(pending, name);
  };

  const shareJobFile = async (target: ConversionJob) => {
    if (!target.file) return;
    try {
      const uri = await downloadConversionFile(target.job_id, target.file.name);
      if (!(await Sharing.isAvailableAsync())) {
        Alert.alert('Sharing unavailable', 'The file could not be shared.');
        return;
      }
      await Sharing.shareAsync(uri);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'The file could not be opened.',
      );
    }
  };

  const exportEditedTable = async () => {
    if (!job || !job.table) return;
    if (columns.length === 0) {
      setError('A table needs at least one column.');
      return;
    }
    if (rows.some((row) => row.length !== columns.length)) {
      setError('Every row needs the same number of cells as the columns.');
      return;
    }
    const format = job.output_kind === 'docx' ? 'docx' : 'xlsx';
    setPhase('exporting');
    setError(null);
    try {
      const next = await exportConversionTable(
        job.job_id,
        format,
        columns,
        rows,
        tableTitle,
      );
      setJob(next);
      setMessage('Table exported. Open or share the file below.');
      setPhase('done');
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : 'The table could not be exported.',
      );
      setPhase('editing');
    }
  };

  const updateColumn = (index: number, value: string) => {
    setColumns((current) =>
      current.map((cell, position) => (position === index ? value : cell)),
    );
  };

  const updateCell = (rowIndex: number, columnIndex: number, value: string) => {
    setRows((current) =>
      current.map((row, index) =>
        index === rowIndex
          ? row.map((cell, position) =>
              position === columnIndex ? value : cell,
            )
          : row,
      ),
    );
  };

  const busy =
    phase === 'starting' || phase === 'running' || phase === 'exporting';
  const activeOption = currentInput?.conversions.find(
    (item) => item.conversion_id === selected?.conversion_id,
  );

  return (
    <SafeAreaView style={styles.safe} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.eyebrow}>Convert</Text>
        <Text style={styles.title}>Turn material into a usable file</Text>
        <Text style={styles.lead}>
          Convert supported files, or turn a photo or pasted text into an
          editable table first.
        </Text>

        <Pressable
          accessibilityRole="button"
          onPress={() => router.push('/table-converter')}
          style={({ pressed }) => [
            styles.photoCard,
            pressed ? styles.photoCardPressed : null,
          ]}>
          <View style={styles.photoCopy}>
            <Text style={styles.photoTitle}>Photo or pasted text to table</Text>
            <Text style={styles.photoText}>
              Read a table from a photo or typed text, review the cells, then
              export Excel, Word or CSV.
            </Text>
          </View>
          <Text style={styles.chevron}>›</Text>
        </Pressable>

        {formatsError ? <Text style={styles.error}>{formatsError}</Text> : null}
        {!formats && !formatsError ? (
          <View style={styles.loadingRow}>
            <ActivityIndicator color={palette.accent} />
            <Text style={styles.loadingText}>Loading converter options…</Text>
          </View>
        ) : null}

        {formats ? (
          <>
            <Text style={styles.sectionTitle}>1. Choose the input</Text>
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.chips}>
              {formats.inputs.map((item) => {
                const active = item.kind === inputKind;
                return (
                  <Pressable
                    key={item.kind}
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    disabled={busy}
                    onPress={() => selectInputKind(item.kind)}
                    style={[styles.chip, active ? styles.chipActive : null]}>
                    <Text
                      style={[
                        styles.chipLabel,
                        active ? styles.chipLabelActive : null,
                      ]}>
                      {item.label}
                    </Text>
                  </Pressable>
                );
              })}
            </ScrollView>

            {inputKind === 'text' ? (
              <TextInput
                multiline
                editable={!busy}
                maxLength={200000}
                value={text}
                onChangeText={(value) => {
                  setText(value);
                  resetOutput();
                }}
                placeholder="Paste text here…"
                placeholderTextColor={palette.muted}
                textAlignVertical="top"
                style={styles.textArea}
              />
            ) : (
              <Pressable
                accessibilityRole="button"
                disabled={busy}
                onPress={() => void chooseFile()}
                style={({ pressed }) => [
                  styles.filePicker,
                  pressed && !busy ? styles.filePickerPressed : null,
                ]}>
                {file ? (
                  <>
                    <Text style={styles.fileName} numberOfLines={2}>
                      {file.name}
                    </Text>
                    <Text style={styles.fileMeta}>
                      {file.size ? formatBytes(file.size) : 'Selected file'}
                      {' · Tap to replace'}
                    </Text>
                  </>
                ) : (
                  <>
                    <Text style={styles.fileTitle}>
                      Choose a {currentInput?.label ?? 'file'}
                    </Text>
                    <Text style={styles.fileMeta}>
                      {currentInput?.accept.join(', ') || 'Supported file'}
                    </Text>
                  </>
                )}
              </Pressable>
            )}

            <Text style={styles.sectionTitle}>2. Choose the output</Text>
            <View style={styles.options}>
              {currentInput?.conversions.map((option) => {
                const active = option.conversion_id === selected?.conversion_id;
                return (
                  <Pressable
                    key={option.conversion_id}
                    accessibilityRole="button"
                    accessibilityState={{ selected: active }}
                    disabled={busy}
                    onPress={() => selectConversion(option)}
                    style={[
                      styles.option,
                      active ? styles.optionActive : null,
                    ]}>
                    <View style={styles.optionHeader}>
                      <Text
                        style={[
                          styles.optionLabel,
                          active ? styles.optionLabelActive : null,
                        ]}>
                        {option.label}
                      </Text>
                      {option.needs_ai ? (
                        <View style={styles.aiBadge}>
                          <Text style={styles.aiBadgeText}>AI</Text>
                        </View>
                      ) : null}
                    </View>
                    <Text style={styles.optionText}>{option.description}</Text>
                  </Pressable>
                );
              })}
            </View>

            {activeOption?.needs_ai ? (
              <View style={styles.consentCard}>
                <View style={styles.consentCopy}>
                  <Text style={styles.consentTitle}>AI summary table</Text>
                  <Text style={styles.consentText}>
                    The source text is sent to the configured AI provider to
                    create a short table. The result is AI-written, not source
                    text. Review and edit every cell before export.
                  </Text>
                </View>
                <Switch
                  value={aiConsent}
                  disabled={busy}
                  onValueChange={setAiConsent}
                  trackColor={{ false: palette.lineStrong, true: palette.sage }}
                  thumbColor={aiConsent ? palette.surface : palette.surfaceMuted}
                />
              </View>
            ) : null}

            <PrimaryButton
              label={
                phase === 'starting'
                  ? 'Starting…'
                  : phase === 'running'
                    ? 'Converting…'
                    : 'Convert'
              }
              onPress={startSelected}
              disabled={
                busy ||
                !selected ||
                (selected.input_mode === 'file' && !file) ||
                (selected.input_mode === 'text' && !text.trim()) ||
                (selected.needs_ai && !aiConsent)
              }
            />

            {phase === 'running' ? (
              <View style={styles.statusRow}>
                <ActivityIndicator color={palette.accent} />
                <Text style={styles.statusText}>
                  Working on this conversion…
                </Text>
              </View>
            ) : null}

            {error ? <Text style={styles.error}>{error}</Text> : null}
            {message ? <Text style={styles.message}>{message}</Text> : null}

            {job?.warnings.length ? (
              <View style={styles.warningCard}>
                {job.warnings.map((warning, index) => (
                  <Text key={`${warning}-${index}`} style={styles.warningText}>
                    {warning}
                  </Text>
                ))}
              </View>
            ) : null}

            {(phase === 'editing' || phase === 'exporting') && job?.table ? (
              <View style={styles.previewSection}>
                <Text style={styles.sectionTitle}>3. Review the table</Text>
                <TextInput
                  value={tableTitle}
                  onChangeText={setTableTitle}
                  editable={!busy}
                  maxLength={200}
                  placeholder="Table title"
                  placeholderTextColor={palette.muted}
                  style={styles.titleInput}
                />
                <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                  <View>
                    <View style={styles.tableRow}>
                      {columns.map((column, index) => (
                        <TextInput
                          key={`heading-${index}`}
                          value={column}
                          onChangeText={(value) => updateColumn(index, value)}
                          maxLength={5000}
                          placeholder={`Column ${index + 1}`}
                          placeholderTextColor={palette.muted}
                          style={[styles.tableCell, styles.tableHeaderCell]}
                        />
                      ))}
                    </View>
                    <ScrollView
                      style={styles.tableBody}
                      nestedScrollEnabled
                      showsVerticalScrollIndicator>
                      {rows.map((row, rowIndex) => (
                        <View key={`row-${rowIndex}`} style={styles.tableRow}>
                          {row.map((cell, columnIndex) => (
                            <TextInput
                              key={`cell-${rowIndex}-${columnIndex}`}
                              value={cell}
                              onChangeText={(value) =>
                                updateCell(rowIndex, columnIndex, value)
                              }
                              maxLength={5000}
                              multiline
                              style={styles.tableCell}
                            />
                          ))}
                        </View>
                      ))}
                    </ScrollView>
                  </View>
                </ScrollView>
                <Text style={styles.tableHint}>
                  {rows.length} rows · {columns.length} columns
                </Text>
                <PrimaryButton
                  label={
                    phase === 'exporting'
                      ? 'Exporting…'
                      : `Export ${job.output_kind === 'docx' ? 'Word' : 'Excel'}`
                  }
                  onPress={() => void exportEditedTable()}
                  disabled={busy}
                />
              </View>
            ) : null}

            {job?.file && phase !== 'editing' ? (
              <View style={styles.fileResult}>
                <Text style={styles.resultTitle}>Conversion complete</Text>
                <Text style={styles.resultName}>{job.file.name}</Text>
                <Text style={styles.resultMeta}>
                  {formatBytes(job.file.size_bytes)}
                </Text>
                <PrimaryButton
                  label="Open or share file"
                  onPress={() => void shareJobFile(job)}
                  disabled={busy}
                />
              </View>
            ) : null}

            <Text style={styles.footnote}>
              Up to {formats.limits.max_file_mb} MB,{' '}
              {formats.limits.max_rows.toLocaleString()} rows,{' '}
              {formats.limits.max_columns} columns, and{' '}
              {formats.limits.max_pages} pages per file.
            </Text>
            <Text style={styles.footnote}>
              Converted files are temporary. AI is only used when you confirm
              the notice for a summary table.
            </Text>
          </>
        ) : null}
      </ScrollView>

      <Modal
        transparent
        visible={sheetPrompt !== null}
        animationType="fade"
        onRequestClose={() => {
          setSheetPrompt(null);
          setError('Choose a sheet to continue.');
        }}>
        <View style={styles.modalBackdrop}>
          <View style={styles.modalCard}>
            <Text style={styles.modalTitle}>Choose a sheet</Text>
            <Text style={styles.modalText}>
              This workbook has more than one sheet. Choose the sheet to
              convert.
            </Text>
            <ScrollView style={styles.sheetList}>
              {sheetPrompt?.sheets.map((name) => (
                <Pressable
                  key={name}
                  accessibilityRole="button"
                  onPress={() => chooseSheet(name)}
                  style={({ pressed }) => [
                    styles.sheetButton,
                    pressed ? styles.sheetButtonPressed : null,
                  ]}>
                  <Text style={styles.sheetName}>{name}</Text>
                </Pressable>
              ))}
            </ScrollView>
            <PrimaryButton
              label="Cancel"
              variant="secondary"
              onPress={() => {
                setSheetPrompt(null);
                setError('Choose a sheet to continue.');
              }}
            />
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: palette.canvas },
  content: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xxl,
    gap: spacing.md,
  },
  eyebrow: {
    color: palette.sage,
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1.6,
    textTransform: 'uppercase',
    marginTop: spacing.sm,
  },
  title: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 30,
    lineHeight: 36,
  },
  lead: {
    color: palette.muted,
    fontSize: 15,
    lineHeight: 22,
  },
  photoCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: palette.accentSoft,
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.lg,
    padding: spacing.lg,
  },
  photoCardPressed: { backgroundColor: palette.canvasDeep },
  photoCopy: { flex: 1, gap: spacing.xs },
  photoTitle: {
    color: palette.accentInk,
    fontSize: 17,
    fontWeight: '700',
  },
  photoText: { color: palette.inkSoft, fontSize: 13, lineHeight: 19 },
  chevron: { color: palette.accent, fontSize: 30, lineHeight: 30 },
  loadingRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    padding: spacing.md,
  },
  loadingText: { color: palette.muted, fontSize: 14 },
  sectionTitle: {
    color: palette.ink,
    fontSize: 16,
    fontWeight: '700',
    marginTop: spacing.sm,
  },
  chips: { gap: spacing.sm, paddingVertical: spacing.xs },
  chip: {
    borderWidth: 1,
    borderColor: palette.line,
    backgroundColor: palette.surface,
    borderRadius: radii.pill,
    paddingVertical: 9,
    paddingHorizontal: spacing.lg,
  },
  chipActive: {
    backgroundColor: palette.accent,
    borderColor: palette.accent,
  },
  chipLabel: { color: palette.inkSoft, fontSize: 14, fontWeight: '600' },
  chipLabelActive: { color: palette.surface },
  textArea: {
    minHeight: 140,
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.md,
    backgroundColor: palette.surface,
    padding: spacing.md,
    color: palette.ink,
    fontSize: 15,
    lineHeight: 21,
  },
  filePicker: {
    borderWidth: 1,
    borderStyle: 'dashed',
    borderColor: palette.lineStrong,
    borderRadius: radii.md,
    backgroundColor: palette.surface,
    padding: spacing.lg,
    gap: spacing.xs,
  },
  filePickerPressed: { backgroundColor: palette.surfaceMuted },
  fileTitle: { color: palette.ink, fontSize: 16, fontWeight: '600' },
  fileName: { color: palette.ink, fontSize: 16, fontWeight: '600' },
  fileMeta: { color: palette.muted, fontSize: 13 },
  options: { gap: spacing.sm },
  option: {
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.md,
    backgroundColor: palette.surface,
    padding: spacing.md,
    gap: spacing.xs,
  },
  optionActive: {
    borderColor: palette.accent,
    backgroundColor: palette.accentSoft,
  },
  optionHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: spacing.sm,
  },
  optionLabel: { color: palette.ink, fontSize: 15, fontWeight: '700' },
  optionLabelActive: { color: palette.accentInk },
  optionText: { color: palette.muted, fontSize: 13, lineHeight: 18 },
  aiBadge: {
    backgroundColor: palette.amberSoft,
    borderRadius: radii.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
  aiBadgeText: { color: palette.amber, fontSize: 11, fontWeight: '800' },
  consentCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    backgroundColor: palette.amberSoft,
    borderRadius: radii.md,
    padding: spacing.md,
  },
  consentCopy: { flex: 1, gap: spacing.xs },
  consentTitle: { color: palette.ink, fontSize: 14, fontWeight: '700' },
  consentText: { color: palette.inkSoft, fontSize: 12, lineHeight: 18 },
  statusRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.sm,
  },
  statusText: { color: palette.muted, fontSize: 14 },
  error: { color: palette.danger, fontSize: 14, lineHeight: 20 },
  message: { color: palette.accentInk, fontSize: 14, lineHeight: 20 },
  warningCard: {
    backgroundColor: palette.amberSoft,
    borderRadius: radii.md,
    padding: spacing.md,
    gap: spacing.xs,
  },
  warningText: { color: palette.amber, fontSize: 13, lineHeight: 18 },
  previewSection: { gap: spacing.sm },
  titleInput: {
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.sm,
    backgroundColor: palette.surface,
    paddingHorizontal: spacing.md,
    paddingVertical: 10,
    color: palette.ink,
    fontSize: 15,
  },
  tableBody: { maxHeight: 340 },
  tableRow: { flexDirection: 'row' },
  tableCell: {
    width: CELL_WIDTH,
    minHeight: 44,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: palette.lineStrong,
    backgroundColor: palette.surface,
    paddingHorizontal: spacing.sm,
    paddingVertical: 8,
    color: palette.ink,
    fontSize: 13,
  },
  tableHeaderCell: {
    backgroundColor: palette.surfaceMuted,
    fontWeight: '700',
  },
  tableHint: { color: palette.muted, fontSize: 12 },
  fileResult: {
    backgroundColor: palette.surface,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.sm,
    borderWidth: 1,
    borderColor: palette.line,
  },
  resultTitle: { color: palette.ink, fontSize: 17, fontWeight: '700' },
  resultName: { color: palette.accentInk, fontSize: 15, fontWeight: '600' },
  resultMeta: { color: palette.muted, fontSize: 13 },
  footnote: {
    color: palette.faint,
    fontSize: 12,
    lineHeight: 18,
    marginTop: spacing.sm,
  },
  modalBackdrop: {
    flex: 1,
    backgroundColor: 'rgba(28, 42, 34, 0.45)',
    justifyContent: 'center',
    padding: spacing.xl,
  },
  modalCard: {
    backgroundColor: palette.surface,
    borderRadius: radii.lg,
    padding: spacing.lg,
    gap: spacing.md,
    maxHeight: '80%',
  },
  modalTitle: {
    color: palette.ink,
    fontFamily: displayFont,
    fontSize: 23,
  },
  modalText: { color: palette.muted, fontSize: 14, lineHeight: 20 },
  sheetList: { maxHeight: 300 },
  sheetButton: {
    borderWidth: 1,
    borderColor: palette.line,
    borderRadius: radii.sm,
    padding: spacing.md,
    marginBottom: spacing.sm,
  },
  sheetButtonPressed: { backgroundColor: palette.accentSoft },
  sheetName: { color: palette.ink, fontSize: 15, fontWeight: '600' },
});
