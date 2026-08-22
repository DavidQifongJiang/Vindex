import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.models import Base, Video
from app.db.video_repository import (
    claim_video_for_processing,
    claim_search_chunk,
    create_search_chunks,
    mark_video_processed_if_ready,
    record_processing_failure,
    record_processing_retry,
    record_processing_success,
    record_search_chunk_failure,
    record_search_chunk_retry,
    record_search_chunk_success,
    search_chunk_counts,
    update_video,
)


def make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)()


def add_video(db, *, status="uploaded", attempt_count=0):
    video = Video(
        video_id="video-1",
        owner_user_id="user-1",
        title="Test video",
        visibility="private",
        filename="video-1.mp4",
        status=status,
        raw_path="raw_videos/video-1.mp4",
        audio_status="not_started",
        transcript_status="not_started",
        segments_status="not_started",
        embedding_status="not_started",
        processing_attempt_count=attempt_count,
        max_processing_attempts=3,
    )
    db.add(video)
    db.commit()
    return video


def add_retrying_video(db, *, next_retry_at):
    video = Video(
        video_id="video-1",
        owner_user_id="user-1",
        title="Test video",
        visibility="private",
        filename="video-1.mp4",
        status="retrying",
        raw_path="raw_videos/video-1.mp4",
        audio_status="not_started",
        transcript_status="not_started",
        segments_status="not_started",
        embedding_status="not_started",
        processing_attempt_count=1,
        max_processing_attempts=3,
        next_retry_at=next_retry_at,
    )
    db.add(video)
    db.commit()
    return video


class VideoRepositoryRetryStateTest(unittest.TestCase):
    def test_claim_from_uploaded_sets_processing_token_and_attempt(self):
        db = make_session()
        add_video(db)

        claimed = claim_video_for_processing(db, "video-1", "worker-1", 3)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, "processing")
        self.assertEqual(claimed.processing_attempt_count, 1)
        self.assertEqual(claimed.processing_owner, "worker-1")
        self.assertTrue(claimed.processing_token)

    def test_duplicate_claim_does_not_process_same_video_twice(self):
        db = make_session()
        add_video(db)

        first = claim_video_for_processing(db, "video-1", "worker-1", 3)
        second = claim_video_for_processing(db, "video-1", "worker-2", 3)

        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_retrying_video_can_be_claimed_again(self):
        db = make_session()
        add_video(db)
        first = claim_video_for_processing(db, "video-1", "worker-1", 3)
        first_token = first.processing_token

        retry_at = datetime.utcnow() - timedelta(seconds=1)
        retrying = record_processing_retry(
            db,
            "video-1",
            first.processing_token,
            error_code="s3_unavailable",
            error_type="TemporaryStorageError",
            error_message="S3 was temporarily unavailable",
            next_retry_at=retry_at,
        )
        self.assertEqual(retrying.status, "retrying")
        self.assertIsNone(retrying.processing_token)

        second = claim_video_for_processing(db, "video-1", "worker-2", 3)

        self.assertEqual(second.status, "processing")
        self.assertEqual(second.processing_attempt_count, 2)
        self.assertNotEqual(first_token, second.processing_token)

    def test_retrying_video_with_future_next_retry_at_cannot_be_claimed(self):
        db = make_session()
        add_retrying_video(db, next_retry_at=datetime.utcnow() + timedelta(minutes=5))

        claimed = claim_video_for_processing(db, "video-1", "worker-1", 3)

        self.assertIsNone(claimed)

    def test_retrying_video_with_due_next_retry_at_can_be_claimed(self):
        db = make_session()
        add_retrying_video(db, next_retry_at=datetime.utcnow() - timedelta(seconds=1))

        claimed = claim_video_for_processing(db, "video-1", "worker-1", 3)

        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, "processing")
        self.assertEqual(claimed.processing_attempt_count, 2)

    def test_retrying_video_with_null_next_retry_at_cannot_be_claimed(self):
        db = make_session()
        add_retrying_video(db, next_retry_at=None)

        claimed = claim_video_for_processing(db, "video-1", "worker-1", 3)

        self.assertIsNone(claimed)

    def test_failed_video_is_terminal_for_automatic_claims(self):
        db = make_session()
        add_video(db, status="failed", attempt_count=3)

        claimed = claim_video_for_processing(db, "video-1", "worker-1", 3)

        self.assertIsNone(claimed)

    def test_stale_token_cannot_mark_failure(self):
        db = make_session()
        add_video(db)
        first = claim_video_for_processing(db, "video-1", "worker-1", 3)
        old_token = first.processing_token

        db.query(Video).filter(Video.video_id == "video-1").update(
            {
                Video.processing_token: "newer-token",
                Video.processing_owner: "worker-2",
            },
            synchronize_session=False,
        )
        db.commit()

        marked = record_processing_failure(
            db,
            "video-1",
            old_token,
            error_code="unsupported_or_corrupt_media",
            error_type="PermanentMediaError",
            error_message="FFmpeg failed",
        )
        video = db.query(Video).filter(Video.video_id == "video-1").first()

        self.assertIsNone(marked)
        self.assertEqual(video.status, "processing")
        self.assertEqual(video.processing_token, "newer-token")

    def test_success_clears_processing_owner_and_token(self):
        db = make_session()
        add_video(db)
        claimed = claim_video_for_processing(db, "video-1", "worker-1", 3)

        processed = record_processing_success(
            db,
            "video-1",
            claimed.processing_token,
            {
                "processed_path": "processed_videos/video-1.mp4",
                "audio_path": "audio/video-1.wav",
                "transcript_path": "transcripts/video-1.txt",
                "segments_path": "transcripts/video-1_segments.json",
                "embedding_path": "embeddings/video-1_embeddings.json",
                "audio_status": "extracted",
                "transcript_status": "completed",
                "segments_status": "completed",
                "embedding_status": "completed",
            },
        )

        self.assertEqual(processed.status, "processed")
        self.assertIsNone(processed.processing_owner)
        self.assertIsNone(processed.processing_token)

    def test_search_completion_alone_does_not_mark_video_processed(self):
        db = make_session()
        add_video(db, status="processing")
        update_video(db, "video-1", {
            "transcript_status": "completed",
            "segments_status": "completed",
            "embedding_status": "completed",
            "embedding_path": "embeddings/video-1_embeddings.json",
        })

        video = mark_video_processed_if_ready(db, "video-1")

        self.assertEqual(video.status, "processing")

    def test_search_and_playback_completion_marks_video_processed(self):
        db = make_session()
        add_video(db, status="processing")
        update_video(db, "video-1", {
            "transcript_status": "completed",
            "segments_status": "completed",
            "embedding_status": "completed",
            "embedding_path": "embeddings/video-1_embeddings.json",
            "processed_path": "processed_videos/video-1.mp4",
        })

        video = mark_video_processed_if_ready(db, "video-1")

        self.assertEqual(video.status, "processed")

    def test_search_chunk_claim_and_success_updates_counts(self):
        db = make_session()
        add_video(db, status="processing")
        create_search_chunks(db, "video-1", [
            {
                "chunk_index": 0,
                "start_seconds": 0,
                "end_seconds": 300,
                "audio_start_seconds": 0,
                "audio_end_seconds": 305,
                "audio_path": "audio_chunks/video-1/0000.wav",
            },
            {
                "chunk_index": 1,
                "start_seconds": 300,
                "end_seconds": 600,
                "audio_start_seconds": 295,
                "audio_end_seconds": 600,
                "audio_path": "audio_chunks/video-1/0001.wav",
            },
        ])

        claimed = claim_search_chunk(db, "video-1", 0, "worker-1", 3)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, "processing")
        self.assertEqual(claimed.processing_attempt_count, 1)

        duplicate = claim_search_chunk(db, "video-1", 0, "worker-2", 3)
        self.assertIsNone(duplicate)

        completed = record_search_chunk_success(
            db,
            "video-1",
            0,
            {
                "transcript_path": "transcripts/chunks/video-1/0000.txt",
                "segments_path": "transcripts/chunks/video-1/0000_segments.json",
                "embedding_path": "embeddings/chunks/video-1/0000_embeddings.json",
                "segment_count": 2,
                "transcript_character_count": 20,
            },
        )

        self.assertEqual(completed.status, "completed")
        self.assertEqual(search_chunk_counts(db, "video-1")["completed"], 1)
        self.assertEqual(search_chunk_counts(db, "video-1")["pending"], 1)

    def test_search_chunk_retry_then_failure_is_independent(self):
        db = make_session()
        add_video(db, status="processing")
        create_search_chunks(db, "video-1", [
            {
                "chunk_index": 0,
                "start_seconds": 0,
                "end_seconds": 300,
                "audio_start_seconds": 0,
                "audio_end_seconds": 300,
                "audio_path": "audio_chunks/video-1/0000.wav",
            },
        ])

        claim_search_chunk(db, "video-1", 0, "worker-1", 3)
        retrying = record_search_chunk_retry(
            db,
            "video-1",
            0,
            error_code="temporary",
            error_type="TemporaryProcessingError",
            error_message="temporary failure",
            next_retry_at=datetime.utcnow() - timedelta(seconds=1),
        )
        self.assertEqual(retrying.status, "retrying")

        claimed_again = claim_search_chunk(db, "video-1", 0, "worker-2", 3)
        self.assertEqual(claimed_again.status, "processing")
        self.assertEqual(claimed_again.processing_attempt_count, 2)

        failed = record_search_chunk_failure(
            db,
            "video-1",
            0,
            error_code="permanent",
            error_type="PermanentProcessingError",
            error_message="permanent failure",
        )
        self.assertEqual(failed.status, "failed")
        self.assertEqual(search_chunk_counts(db, "video-1")["failed"], 1)


if __name__ == "__main__":
    unittest.main()
