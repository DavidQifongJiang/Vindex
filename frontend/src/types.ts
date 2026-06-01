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
