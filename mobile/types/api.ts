export type HealthStatus = 'ok' | 'degraded';

export type AiProviderStatus = 'configured' | 'missing';

export interface AiStatus {
  provider: AiProviderStatus;
  model: string | null;
  reachable: boolean | null;
}

export interface HealthLimits {
  max_upload_mb: number;
  retention_minutes: number;
}

export interface HealthResponse {
  status: HealthStatus;
  version: string;
  commit: string | null;
  ai: AiStatus;
  limits: HealthLimits;
}

export type DocumentFormat = 'pdf' | 'docx';

export type ExtractionStatus = 'pending' | 'ok' | 'no_text' | 'partial';

export type DocumentIngestStatus = 'processing' | 'ready' | 'failed';

export interface IngestProgress {
  processed_units: number;
  total_units: number;
  label?: string | null;
}

export interface ApiWarning {
  code: string;
  message: string;
}

export interface ExtractionInfo {
  status: ExtractionStatus;
  text_extractable: boolean;
  section_count: number;
  total_chars: number;
}

export interface DocumentResponse {
  document_id: string;
  filename: string;
  format: DocumentFormat;
  size_bytes: number;
  page_count: number | null;
  status: DocumentIngestStatus;
  extraction: ExtractionInfo;
  warnings: ApiWarning[];
  progress: IngestProgress;
  error: { code: string; message: string } | null;
}

export type OcrStatus = 'not_needed' | 'pending' | 'complete' | 'incomplete';

export interface OcrProgressResponse {
  document_id: string;
  format: DocumentFormat;
  page_count: number | null;
  status: OcrStatus;
  total_pages: number;
  needs_ocr_pages: number;
  completed_pages: number;
  pending_pages: number[];
  failed_pages: number[];
  warnings: ApiWarning[];
}

export interface OcrBatchResponse extends OcrProgressResponse {
  attempted_pages: number[];
  completed_this_batch: number[];
  failed_this_batch: number[];
}

export interface DocumentEditResponse {
  job_id: string;
  document_id: string;
  filename: string;
  original_text: string;
  edited_text: string;
  removed_words: string[];
  removed_count: number;
}

export type StudyMode =
  | 'extracted_text_only'
  | 'text_and_summaries'
  | 'summaries_only';

export type StudyStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'partial'
  | 'failed';

export interface SourceRef {
  kind: string;
  page?: number;
  page_index?: number;
  section_label?: string | null;
  paragraph_index?: number;
}

export interface StudyItem {
  index: number;
  source_refs: SourceRef[];
  text: string | null;
  summary: string | null;
  warnings: ApiWarning[];
}

export interface StudyProgress {
  processed_units: number;
  total_units: number;
}

export interface StudyJobError {
  code: string;
  message: string;
}

export interface StudyJobResponse {
  job_id: string;
  document_id: string;
  filename: string;
  format: DocumentFormat;
  mode: StudyMode;
  status: StudyStatus;
  created_at: string;
  updated_at: string;
  page_count: number | null;
  item_count: number;
  items: StudyItem[];
  document_summary: string | null;
  warnings: ApiWarning[];
  error: StudyJobError | null;
  progress: StudyProgress;
  document_available: boolean;
  diagnostics?: Record<string, unknown>;
}

export interface RangeExtractionResponse {
  document_id: string;
  format: DocumentFormat;
  page_count: number | null;
  selector: string;
  sections: Array<{
    index: number;
    anchor: SourceRef;
    raw_text: string;
    normalized_text?: string;
  }>;
  warnings: ApiWarning[];
}

export type ChatCommandAction =
  | 'extract'
  | 'summarize_whole'
  | 'export'
  | 'clarify'
  | 'help';
export type ChatCommandMode =
  | 'source_passages'
  | 'both'
  | 'ai_study_material';
export interface ChatCommand {
  action: ChatCommandAction;
  selector: {
    type: 'topic' | 'pages' | 'headings' | 'items' | 'whole';
    topic: string | null;
    pages: [number, number][];
    chapter: string | null;
    items: [number, number][];
  };
  mode: ChatCommandMode;
  export_format: ExportFormat | null;
  question: string | null;
}

export interface StudyJobSummary {
  job_id: string;
  document_id: string;
  filename: string;
  format: DocumentFormat;
  mode: StudyMode;
  status: StudyStatus;
  created_at: string;
  updated_at: string;
  page_count: number | null;
  item_count: number;
  document_available: boolean;
}

export type ExportFormat = 'pdf' | 'docx';

export type TopicSourceStatus = 'ok' | 'partial' | 'no_text';

export type TopicOcrStatus =
  | 'ok'
  | 'empty'
  | 'timeout'
  | 'unavailable'
  | 'failed'
  | 'skipped'
  | 'manual';

export type TopicTranscriptSource = 'ocr' | 'manual';

export interface TopicSourceDocument {
  document_id: string;
  filename: string;
  format: DocumentFormat;
  page_count: number | null;
}

export interface TopicSourceImage {
  index: number;
  filename: string;
  content_type: 'image/png' | 'image/jpeg';
  size_bytes: number;
  width: number;
  height: number;
  ocr_status: TopicOcrStatus;
  transcript_source: TopicTranscriptSource;
  text: string;
  image_url: string;
  warnings: ApiWarning[];
}

export interface TopicSourceSummary {
  source_id: string;
  status: TopicSourceStatus;
  document_id: string | null;
  filename: string | null;
  format: DocumentFormat | null;
  image_count: number;
  total_chars: number;
  topics: string[];
  created_at: string;
  updated_at: string;
  document_available: boolean;
}

export interface TopicSourceResponse {
  source_id: string;
  status: TopicSourceStatus;
  document: TopicSourceDocument | null;
  document_available: boolean;
  image_count: number;
  jottings_text: string;
  total_chars: number;
  topics: string[];
  images: TopicSourceImage[];
  warnings: ApiWarning[];
  created_at: string;
  updated_at: string;
}

export type TableEngine = 'local' | 'ai' | 'text';
export type TableExportFormat = 'csv' | 'xlsx' | 'docx';

export interface TableResultResponse {
  result_id: string;
  source_id: string;
  image_index: number;
  engine: TableEngine;
  title: string;
  has_header: boolean;
  columns: string[];
  rows: string[][];
  confidence: number[][];
  low_confidence: boolean[][];
  row_count: number;
  column_count: number;
  warnings: ApiWarning[];
  created_at: number;
  expires_at: number;
}

export interface TableExportJob {
  job_id: string;
  result_id: string;
  format: TableExportFormat;
  status: 'queued' | 'running' | 'completed' | 'failed';
  file_id: string | null;
  name: string | null;
  error: {
    code: string;
    message: string;
    details: Record<string, unknown>;
  } | null;
}

export interface OutlineInterpretationResponse {
  topics: string[];
  warnings: ApiWarning[];
}

export type TopicMatchStatus = 'matched' | 'ambiguous' | 'unmatched';

export interface TopicPassageMatch {
  passage_id: string;
  section_index: number;
  anchor: SourceRef;
  passage: string;
  passage_start: number;
  passage_end: number;
  excerpt: string;
  excerpt_start: number;
  excerpt_end: number;
  score: number;
  coverage: number;
  phrase_match: boolean;
  matched_terms: string[];
}

export interface TopicReviewTopic {
  topic_id: string;
  text: string;
  status: TopicMatchStatus;
  confidence: number;
  source_labels: string[];
  matches: TopicPassageMatch[];
  warnings: ApiWarning[];
}

export type TopicSourceMaterialStatus =
  | 'available'
  | 'not_linked'
  | 'unavailable'
  | 'empty';

export interface TopicSourceMaterial {
  status: TopicSourceMaterialStatus;
  section_count: number;
}

export interface TopicReviewSegmentation {
  input: 'transcript' | 'jottings' | 'combined' | 'provided';
  topic_count: number;
  warnings: ApiWarning[];
}

export interface TopicReviewResponse {
  source_id: string;
  document: TopicSourceDocument | null;
  document_available: boolean;
  source_material: TopicSourceMaterial;
  segmentation: TopicReviewSegmentation;
  topics: TopicReviewTopic[];
  warnings: ApiWarning[];
}

export type TopicStudyMode =
  | 'source_passages'
  | 'ai_study_material'
  | 'both';

export type TopicAiScope = 'source_only' | 'source_and_beyond';

export type TopicStudyStatus = 'completed' | 'partial' | 'failed';

export type TopicMaterialStatus =
  | 'not_requested'
  | 'generated'
  | 'skipped_ambiguous'
  | 'skipped_unmatched'
  | 'skipped_no_evidence'
  | 'ai_failed';

export interface TopicSourceGroundedMaterial {
  text: string;
  basis_passage_ids: string[];
  basis_refs: SourceRef[];
}

export interface TopicBeyondSourceMaterial {
  text: string;
  grounded_in_uploaded_material: false;
}

export interface TopicGeneratedStudyMaterial {
  generated: true;
  source_grounded: TopicSourceGroundedMaterial;
  beyond_source: TopicBeyondSourceMaterial | null;
}

export interface TopicStudyOutputTopic {
  topic_id: string;
  text: string;
  match_status: TopicMatchStatus;
  material_status: TopicMaterialStatus;
  source_passages: TopicPassageMatch[];
  study_material: TopicGeneratedStudyMaterial | null;
  warnings: ApiWarning[];
}

export interface TopicStudyAiInfo {
  requested: boolean;
  scope: TopicAiScope;
  configured: boolean;
  generated_count: number;
  failed_count: number;
  skipped_count: number;
  beyond_generated_count: number;
  beyond_failed_count: number;
}

export interface TopicStudyOutputResponse {
  result_id: string | null;
  source_id: string;
  document: TopicSourceDocument | null;
  document_available: boolean;
  source_material: TopicSourceMaterial;
  mode: TopicStudyMode;
  ai_scope: TopicAiScope;
  status: TopicStudyStatus;
  topic_count: number;
  topics: TopicStudyOutputTopic[];
  ai: TopicStudyAiInfo;
  warnings: ApiWarning[];
  error: ApiWarning | null;
  created_at: string | null;
  expires_at: string | null;
  diagnostics?: Record<string, unknown>;
}

export interface TopicStudyResultSummary {
  result_id: string;
  source_id: string;
  document_id: string | null;
  filename: string;
  format: DocumentFormat | null;
  mode: TopicStudyMode;
  ai_scope: TopicAiScope;
  status: TopicStudyStatus;
  topic_count: number;
  created_at: string;
  updated_at: string;
  expires_at: string;
  document_available: boolean;
}

export type ConversionInputKind = 'csv' | 'xlsx' | 'docx' | 'pdf' | 'text';
export type ConversionOutputKind = 'xlsx' | 'docx' | 'pdf' | 'csv';

export interface ConversionOption {
  conversion_id: string;
  input_kind: ConversionInputKind;
  output_kind: ConversionOutputKind;
  label: string;
  description: string;
  needs_ai: boolean;
  input_mode: 'file' | 'text';
}

export interface ConversionInput {
  kind: ConversionInputKind;
  label: string;
  accept: string[];
  supports_text: boolean;
  conversions: ConversionOption[];
}

export interface ConversionLimits {
  max_file_mb: number;
  max_rows: number;
  max_columns: number;
  max_cell_chars: number;
  max_pages: number;
}

export interface ConversionFormatsResponse {
  inputs: ConversionInput[];
  limits: ConversionLimits;
}

export interface ConversionTable {
  title: string;
  columns: string[];
  rows: string[][];
}

export interface ConversionFileInfo {
  name: string;
  format: ConversionOutputKind;
  size_bytes: number;
}

export interface ConversionErrorData {
  code: string;
  message: string;
  details: Record<string, unknown>;
}

export interface ConversionJob {
  job_id: string;
  conversion_id: string;
  input_kind: ConversionInputKind;
  output_kind: ConversionOutputKind;
  status: 'queued' | 'running' | 'completed' | 'failed';
  needs_ai: boolean;
  editable: boolean;
  file: ConversionFileInfo | null;
  table: ConversionTable | null;
  warnings: string[];
  error: ConversionErrorData | null;
  created_at: number;
  expires_at: number;
}
