import logging
import sys
import time
from datetime import datetime

import httpx

from backend.app.config import settings
from backend.app.legacy_sync import fetch_order_rows, get_last_sync_timestamp, group_orders

logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def emit_payload(orders: list[dict]):
    serialized = []
    for order in orders:
        serialized.append(
            {**order, "order_date": order["order_date"].isoformat()}
        )
    payload = {"orders": serialized, "triggered_at": datetime.utcnow().isoformat()}
    headers = {"X-Internal-Token": settings.internal_sync_token}
    with httpx.Client(timeout=15.0) as client:
        response = client.post(settings.internal_sync_url, json=payload, headers=headers)
        response.raise_for_status()


def main():
    last_sync = get_last_sync_timestamp()
    logging.info("Starting poller (interval=%s)", settings.poll_interval_seconds)
    while True:
        try:
            rows, cart_options = fetch_order_rows(last_sync)
            # Post every cycle, even with no new orders, so the app can retry shipped
            # orders that were still waiting on a ShipStation label.
            orders = group_orders(rows, cart_options) if rows else []
            emit_payload(orders)
            if rows:
                # Advance using the legacy DB's own timestamps. Using utcnow() here skipped
                # orders whenever the legacy server's clock/timezone differed from UTC.
                last_sync = max(row["order_date"] for row in rows)
                logging.info("Synced %d orders", len(orders))
            else:
                logging.debug("No new orders since %s", last_sync)
        except httpx.HTTPError as exc:
            logging.warning("Sync endpoint request failed: %s", exc)
        except Exception as exc:  # pylint: disable=broad-except
            logging.exception("Poller error: %s", exc)
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
