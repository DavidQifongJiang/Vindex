import logging
from threading import Event, Thread

from app.db.pipeline_repository import LEASE_SECONDS, renew_work
from app.db.session import create_session
from app.services.processing_errors import StaleProcessingAttempt

logger = logging.getLogger(__name__)


class ProcessingLease:
    def __init__(self, model, video_id, key, token):
        self.model, self.video_id, self.key, self.token = model, video_id, key, token
        self.stop = Event()
        self.lost = Event()
        self.thread = Thread(target=self._heartbeat, daemon=True)

    def _heartbeat(self):
        while not self.stop.wait(LEASE_SECONDS / 3):
            db = create_session()
            try:
                renew_work(db, self.model, self.video_id, self.key, self.token)
            except StaleProcessingAttempt:
                self.lost.set()
                return
            except Exception:
                logger.exception("Lease heartbeat failed for %s/%s", self.video_id, self.key)
            finally:
                db.close()

    def check(self):
        if self.lost.is_set():
            raise StaleProcessingAttempt("Processing lease was lost")
        db = create_session()
        try:
            renew_work(db, self.model, self.video_id, self.key, self.token, progress=True)
        finally:
            db.close()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=1)
