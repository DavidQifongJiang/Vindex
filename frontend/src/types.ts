export type VideoStatus = {
  video_id: string;
  filename: string;
  status: "uploaded" | "processing" | "processed" | "failed" | string;
  raw_path: string | null;
  processed_path: string | null;
  audio_path: string | null;
  transcript_path: string | null;
  segments_path: string | null;
  embedding_path: string | null;
  audio_status: string;
  transcript_status: string;
  segments_status: string;
  embedding_status: string;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type SearchAlgorithm =
  | "qdrant_embedding"
  | "exact"
  | "overlap"
  | "stopword_overlap";

export type SearchResult = {
  start: number;
  end: number;
  text: string;
  score: number;
  algorithm?: string;
};

export type Segment = {
  id?: number;
  start: number;
  end: number;
  text: string;
};

export type VideoMetrics = {
  video_id: string;
  created_at: string;
  updated_at: string;
  upload?: {
    accepted_at_epoch?: number;
    upload_response_latency_seconds?: number;
    s3_raw_upload_seconds?: number;
    uploaded_bytes?: number;
  };
  processing?: {
    time_to_searchable_seconds?: number | null;
    whisper_transcription_seconds?: number;
    embedding_generation_seconds?: number;
    qdrant_upsert_seconds?: number;
    total_processing_seconds?: number;
  };
  search?: {
    last_query?: string;
    search_latency_seconds?: number;
    query_embedding_seconds?: number;
    qdrant_search_seconds?: number;
    result_count?: number;
    top_score?: number | null;
  };
};
