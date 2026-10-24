"""Private worker for the isolated minute pipeline; same durable handlers."""
from . import long_render_app
from .task_worker import create_task_worker


def context():
    settings, records, cloud, _ = long_render_app.services()
    return settings, records, cloud


app = create_task_worker(long_render_app.app, context)
