from app.db.models import PipelineJob, VideoSearchChunk
from app.db.pipeline_repository import claim_work, fail_work
from app.db.session import create_session
from app.services.processing_errors import StaleProcessingAttempt, classify_processing_exception
from app.services.processing_lease import ProcessingLease


def run_durable_task(task, video_id, stage, processor, *, chunk_index=None):
    model = VideoSearchChunk if chunk_index is not None else PipelineJob
    key = chunk_index if chunk_index is not None else stage
    request = getattr(task, "request", None)
    owner = getattr(request, "hostname", None) or getattr(request, "id", None) or "unknown-worker"
    db = create_session()
    try:
        claimed = claim_work(db, model, video_id, key, owner)
        if claimed is None:
            return {"video_id": video_id, "status": "skipped"}
        token = claimed.processing_token
        db.expunge(claimed)
    finally:
        db.close()

    try:
        with ProcessingLease(model, video_id, key, token) as lease:
            if model is VideoSearchChunk:
                result = processor(video_id, chunk_index, claimed_chunk=claimed, lease=lease)
            else:
                result = processor(video_id, processing_token=token, lease=lease)
            return result if isinstance(result, dict) else {"video_id": video_id, "stage": stage, "status": "processed"}
    except StaleProcessingAttempt:
        return {"video_id": video_id, "status": "stale"}
    except Exception as exc:
        error = classify_processing_exception(exc)
        db = create_session()
        try:
            marked = fail_work(db, model, video_id, key, token, error)
            if marked is None:
                return {"video_id": video_id, "status": "stale"}
            status = marked.status
        finally:
            db.close()
        if status == "failed":
            raise error from exc
        return {"video_id": video_id, "status": "retrying"}
