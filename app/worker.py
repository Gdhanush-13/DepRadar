from celery import Celery

from .config import settings

celery_app = Celery("depradar", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.beat_schedule = {
    "nightly-rescan": {"task": "app.worker.nightly_rescan", "schedule": 86400}
}


@celery_app.task
def nightly_rescan() -> str:
    # Production deployments can call the same async scan service from this task.
    return "nightly scan scheduled"
