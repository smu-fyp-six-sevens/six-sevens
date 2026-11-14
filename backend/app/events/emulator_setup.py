"""
Creates the topic, dead-letter topic and one subscription per role on the local Pub/Sub emulator (compose's
pubsub-init). In the cloud, Terraform creates the same from events/types.SUBSCRIPTIONS.

    PUBSUB_EMULATOR_HOST=localhost:8085 GOOGLE_CLOUD_PROJECT=local python -m app.events.emulator_setup

With EVENT_DELIVERY=pull the subscriptions have no push endpoint and the workers pull (events/pull.py). Otherwise
each role pushes to PUSH_ENDPOINT_<ROLE> (e.g. PUSH_ENDPOINT_DETECTION_WORKER), else to
http://<PUSH_HOST_<ROLE> or PUSH_HOST>:<PUSH_PORT_<ROLE> or PUSH_PORT or 8000>/push/<role>.

    python -m app.events.emulator_setup --poison   # publish one malformed message (dead-letter test)
"""

from __future__ import annotations

import logging
import os
import sys
import time

from google.api_core import exceptions
from google.cloud import pubsub_v1

from ..config import settings
from . import types
from .types import SUBSCRIPTIONS, subscription_filter, subscription_id

logger = logging.getLogger("events.emulator_setup")

ACK_DEADLINE_SECONDS = 600
MAX_DELIVERY_ATTEMPTS = 5


def _endpoint(role: str) -> str:
    key = role.upper().replace("-", "_")
    explicit = os.environ.get(f"PUSH_ENDPOINT_{key}")
    if explicit:
        return explicit
    host = os.environ.get(f"PUSH_HOST_{key}") or os.environ.get("PUSH_HOST", "localhost")
    port = os.environ.get(f"PUSH_PORT_{key}") or os.environ.get("PUSH_PORT", "8000")
    return f"http://{host}:{port}/push/{role}"


def _create_topic(publisher: pubsub_v1.PublisherClient, name: str) -> None:
    for attempt in range(60):
        try:
            publisher.create_topic(name=name)
            return
        except exceptions.AlreadyExists:
            return
        except (exceptions.ServiceUnavailable, exceptions.DeadlineExceeded):
            if attempt == 59:
                raise
            time.sleep(1)  # the emulator is still starting


def _create_subscription(subscriber: pubsub_v1.SubscriberClient, request: dict) -> None:
    try:
        subscriber.delete_subscription(subscription=request["name"])
    except exceptions.NotFound:
        pass
    try:
        subscriber.create_subscription(request=request)
    except (exceptions.InvalidArgument, exceptions.MethodNotImplemented) as exc:
        # An emulator without filters or dead-lettering: handlers ignore types they don't handle anyway.
        logger.warning("Emulator refused filter/dead-letter for %s (%s); creating it without", request["name"], exc)
        plain = {k: v for k, v in request.items() if k not in ("filter", "dead_letter_policy")}
        subscriber.create_subscription(request=plain)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not os.environ.get("PUBSUB_EMULATOR_HOST"):
        raise SystemExit("PUBSUB_EMULATOR_HOST is not set; this only sets up the local emulator")
    project = settings.GOOGLE_CLOUD_PROJECT or "local"
    publisher = pubsub_v1.PublisherClient()
    topic = publisher.topic_path(project, settings.PUBSUB_TOPIC)
    if "--poison" in sys.argv:
        # Every handler refuses it, so it is retried and then dead-lettered without blocking anything else.
        publisher.publish(topic, b"not an event", type=types.DOCUMENT_APPROVED).result(timeout=30)
        logger.info("Published a malformed %s message", types.DOCUMENT_APPROVED)
        return
    subscriber = pubsub_v1.SubscriberClient()
    dead_letter = publisher.topic_path(project, f"{settings.PUBSUB_TOPIC}-dead-letter")
    _create_topic(publisher, topic)
    _create_topic(publisher, dead_letter)
    pull = settings.EVENT_DELIVERY == "pull"
    for role, subscribed in SUBSCRIPTIONS.items():
        name = subscriber.subscription_path(project, subscription_id(role))
        request = {
            "name": name,
            "topic": topic,
            "ack_deadline_seconds": ACK_DEADLINE_SECONDS,
            "filter": subscription_filter(subscribed, role),
            "dead_letter_policy": {"dead_letter_topic": dead_letter, "max_delivery_attempts": MAX_DELIVERY_ATTEMPTS},
        }
        if not pull:
            request["push_config"] = {"push_endpoint": _endpoint(role)}
        _create_subscription(subscriber, request)
        logger.info("%s -> %s", name, "pulled" if pull else _endpoint(role))
    # Somewhere to read dead-lettered messages from.
    _create_subscription(subscriber, {
        "name": subscriber.subscription_path(project, f"{settings.PUBSUB_TOPIC}-dead-letter-pull"),
        "topic": dead_letter,
    })
    logger.info("Pub/Sub emulator ready on %s", os.environ["PUBSUB_EMULATOR_HOST"])


if __name__ == "__main__":
    main()
