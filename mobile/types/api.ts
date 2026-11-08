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
  ai: AiStatus;
  limits: HealthLimits;
}

export type DocumentFormat = 'pdf' | 'docx';

export type ExtractionStatus = 'ok' | 'no_text' | 'partial';

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
  extraction: ExtractionInfo;
  warnings: ApiWarning[];
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
