"""Firehose stream service.

Based on https://github.com/MarshalX/bluesky-feed-generator/blob/main/server/data_stream.py
"""  # noqa

import time
from typing import Optional

from atproto import (
    AtUri,
    CAR,
    firehose_models,
    FirehoseSubscribeReposClient,
    models,
    parse_subscribe_repos_message,
)
from atproto.exceptions import FirehoseError

from lib.db.bluesky_models.raw import FirehoseSubscriptionStateCursorModel
from lib.logger import get_logger
from lib.helper import ThreadSafeCounter

from sync.sync_db import load_cursor_state_from_db

# number of events to stream before exiting
# (should be ~4,000 posts, a 1:5 ratio of posts:events)
cursor_update_frequency = 20000

logger = get_logger(__name__)


def _get_ops_by_type(commit: models.ComAtprotoSyncSubscribeRepos.Commit) -> dict:  # noqa
    operation_by_type = {
        "posts": {"created": [], "deleted": []},
        # NOTE: is it possible to track reposts?
        "reposts": {"created": [], "deleted": []},
        "likes": {"created": [], "deleted": []},
        "follows": {"created": [], "deleted": []},
    }

    car = CAR.from_bytes(commit.blocks)
    for op in commit.ops:
        uri = AtUri.from_str(f"at://{commit.repo}/{op.path}")

        if op.action == "update":
            # not supported yet
            continue

        if op.action == "create":
            if not op.cid:
                continue

            create_info = {"uri": str(uri), "cid": str(op.cid), "author": commit.repo}

            record_raw_data = car.blocks.get(op.cid)
            if not record_raw_data:
                continue

            record = models.get_or_create(record_raw_data, strict=False)
            if uri.collection == models.ids.AppBskyFeedLike and models.is_record_type(
                record, models.AppBskyFeedLike
            ):
                operation_by_type["likes"]["created"].append(
                    {"record": record, **create_info}
                )
            elif uri.collection == models.ids.AppBskyFeedPost and models.is_record_type(
                record, models.AppBskyFeedPost
            ):
                operation_by_type["posts"]["created"].append(
                    {"record": record, **create_info}
                )
            elif (
                uri.collection == models.ids.AppBskyGraphFollow
                and models.is_record_type(record, models.AppBskyGraphFollow)
            ):
                operation_by_type["follows"]["created"].append(
                    {"record": record, **create_info}
                )

        if op.action == "delete":
            if uri.collection == models.ids.AppBskyFeedLike:
                operation_by_type["likes"]["deleted"].append({"uri": str(uri)})
            if uri.collection == models.ids.AppBskyFeedPost:
                operation_by_type["posts"]["deleted"].append({"uri": str(uri)})
            if uri.collection == models.ids.AppBskyGraphFollow:
                operation_by_type["follows"]["deleted"].append({"uri": str(uri)})

    return operation_by_type


def run(
    name, operations_callback, stream_stop_event=None, restart_cursor: bool = False
):
    while stream_stop_event is None or not stream_stop_event.is_set():
        try:
            _run(
                name=name,
                operations_callback=operations_callback,
                stream_stop_event=stream_stop_event,
                restart_cursor=restart_cursor,
            )
        except FirehoseError as e:
            # here we can handle different errors to reconnect to firehose
            raise e


def _run(
    name, operations_callback, stream_stop_event=None, restart_cursor: bool = False
):
    """Run firehose stream."""
    if not restart_cursor:
        state: Optional[FirehoseSubscriptionStateCursorModel] = load_cursor_state_from_db(
            service_name=name
        )
        if state:
            logger.info(f"Restarting cursor state with cursor={state.cursor}...")
    else:
        state = None

    if state:
        params = models.ComAtprotoSyncSubscribeRepos.Params(cursor=state.cursor)  # noqa
    else:
        params = None

    firehose_client = FirehoseSubscribeReposClient(params)

    counter = ThreadSafeCounter()

    def on_message_handler(message: firehose_models.MessageFrame) -> None:
        # stop on next message if requested
        if stream_stop_event and stream_stop_event.is_set():
            firehose_client.stop()
            return

        # possible types of messages: https://github.com/bluesky-social/atproto/blob/main/packages/api/src/client/lexicons.ts#L3545 # noqa
        if message.type == "#identity":
            return
        commit = parse_subscribe_repos_message(message)
        if not isinstance(commit, models.ComAtprotoSyncSubscribeRepos.Commit):
            return

        if not commit.blocks:
            return

        ops_by_type: dict = _get_ops_by_type(commit)

        has_written_data: dict = operations_callback(ops_by_type)

        # we assume that the write to DB has succeeded, though we may want to
        # validate this check (i.e., has_written_data is always True, but
        # we may want to see if this is actually the case.)
        if has_written_data:
            counter.increment()
            counter_value = counter.get_value()
            if counter_value % cursor_update_frequency == 0:
                logger.info(f"Counter: {counter_value}")

    try:
        firehose_client.start(on_message_handler)
    except FirehoseError as e:
        logger.error(f"FirehoseError: {e}. Restarting...")
        return _run(name, operations_callback, stream_stop_event, restart_cursor)
    except Exception as e:
        logger.error(f"Error in firehose client: {e}. Restarting...")
        logger.info("Sleeping for 20 seconds before retrying...")
        time.sleep(20)
        logger.info("Retrying firehose client...")
        return _run(name, operations_callback, stream_stop_event, restart_cursor)
