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
