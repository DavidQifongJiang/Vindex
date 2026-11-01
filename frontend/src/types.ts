export type VideoStatus = {
  keyword_search_enabled?: boolean;
  video_id: string;
  title: string | null;
  visibility: "private" | "public" | string | null;
  filename: string;
  status: "uploaded" | "processing" | "processed" | "failed" | string;
  audio_status: string;
  transcript_status: string;
  segments_status: string;
  embedding_status: string;
  playable: boolean;
  is_owner: boolean;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type UserProfile = {
  auth_required: boolean;
  user_id: string;
  email: string | null;
  name: string | null;
  picture_url: string | null;
  created_at?: string;
  updated_at?: string;
};

export type SearchAlgorithm =
  | "qdrant_embedding"
  | "bm25"
  | "hybrid"
  | "elasticsearch_bm25"
  | "elasticsearch_hybrid"
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

export type LibrarySearchHit = {
  video_id: string;
  revision_id: string;
  passage_id: string;
  title: string | null;
  start_seconds: number;
  end_seconds: number;
  text: string;
  matched_by: string[];
};

export type LibrarySearchResponse = {
  request_id: string;
  interaction_enabled?: boolean;
  degraded: boolean;
  effective_mode: string;
  notices: string[];
  coverage: {
    complete: boolean;
    total_videos: number;
    processing_videos: number;
    indexing_failed_videos: number;
    eligible_videos: { semantic: number; lexical: number; either: number };
  };
  results: LibrarySearchHit[];
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
    enqueued_at_epoch?: number;
    upload_response_latency_seconds?: number;
    s3_raw_upload_seconds?: number;
    celery_enqueue_seconds?: number;
    uploaded_bytes?: number;
  };
  processing?: {
    started_at_epoch?: number;
    completed_at_epoch?: number;
    queue_wait_seconds?: number | null;
    time_to_searchable_seconds?: number | null;
    ffmpeg_preset?: string;
    s3_raw_download_seconds?: number;
    video_transcode_seconds?: number;
    audio_extraction_seconds?: number;
    whisper_model_load_or_get_seconds?: number;
    whisper_transcription_seconds?: number;
    embedding_model_load_or_get_seconds?: number;
    embedding_generation_seconds?: number;
    qdrant_upsert_seconds?: number;
    thumbnail_generation_seconds?: number | null;
    s3_processed_upload_seconds?: number;
    s3_audio_upload_seconds?: number;
    s3_thumbnail_upload_seconds?: number | null;
    s3_embedding_write_seconds?: number;
    s3_transcript_write_seconds?: number;
    s3_segments_write_seconds?: number;
    s3_artifact_write_seconds?: number;
    segment_count?: number;
    transcript_character_count?: number;
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
