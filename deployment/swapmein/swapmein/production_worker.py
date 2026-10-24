"""Private short-render worker; same handlers and guards as the production API."""
from . import app as gateway
from .task_worker import create_task_worker


def context():
    records, cloud, _ = gateway.services()
    return gateway.settings, records, cloud


app = create_task_worker(gateway.app, context)
