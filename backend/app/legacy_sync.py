from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

import pymysql
from pymysql.cursors import DictCursor

from .config import settings

SYNC_QUERY = """
SELECT
    l.order_id,
    l.update_date AS order_date,
    l.order_open_status AS order_open_status,
    c.cart_id,
    c.cart_sku,
    c.cart_qty
FROM ms_order_status_logs l
JOIN ms_cart c ON l.order_id = c.cart_order
WHERE l.order_open_status IN (39, 40)
    AND l.update_date > %s
ORDER BY l.update_date ASC;
"""


def get_last_sync_timestamp() -> datetime:
    connection = pymysql.connect(
        host=settings.app_mysql_host,
        port=settings.app_mysql_port,
        user=settings.app_mysql_user,
        password=settings.app_mysql_password,
        database=settings.app_mysql_database,
        cursorclass=DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT MAX(timestamp) AS last_ts FROM sync_logs;")
            row = cursor.fetchone()
            if row and row["last_ts"]:
                return row["last_ts"]
    except pymysql.err.ProgrammingError:
        # sync_logs table may not exist yet; fall back to a short window
        return datetime.utcnow() - timedelta(minutes=5)
    finally:
        connection.close()
    return datetime.utcnow() - timedelta(minutes=5)


def fetch_order_rows(since: datetime) -> tuple[list[dict], dict[int, list[str]]]:
    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        cursorclass=DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(SYNC_QUERY, (since,))
            rows = cursor.fetchall()
        cart_ids = {row["cart_id"] for row in rows if row.get("cart_id")}
        options = _fetch_cart_options(cart_ids, connection)
        return rows, options
    finally:
        connection.close()


def _fetch_cart_options(cart_ids: set[int], connection) -> dict[int, list[str]]:
    if not cart_ids:
        return {}
    placeholders = ",".join(["%s"] * len(cart_ids))
    query = f"""
        SELECT co_cart_id, TRIM(co_opt_name) AS add_on_name
        FROM ms_cart_options
        WHERE co_cart_id IN ({placeholders});
    """
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(cart_ids))
        rows = cursor.fetchall()
    result: dict[int, list[str]] = defaultdict(list)
    for row in rows:
        name = row.get("add_on_name")
        if name:
            result[int(row["co_cart_id"])].append(name)
    return result


def group_orders(rows: list[dict], cart_options: dict[int, list[str]]) -> list[dict]:
    grouped: dict[int, dict] = {}
    for row in rows:
        order_id = int(row["order_id"])
        if order_id not in grouped:
            grouped[order_id] = {
                "order_id": order_id,
                "order_date": row["order_date"],
                "order_open_status": int(row["order_open_status"]),
                "line_items": [],
            }
        add_ons = cart_options.get(row.get("cart_id"), [])
        grouped[order_id]["line_items"].append(
            {
                "cart_sku": row["cart_sku"],
                "cart_qty": float(row["cart_qty"]),
                "cart_id": row.get("cart_id"),
                "add_ons": add_ons,
            }
        )
    return list(grouped.values())


def get_legacy_product_name(cart_sku: str) -> str | None:
    if not cart_sku:
        return None
    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        cursorclass=DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pp_name FROM ms_photo_products WHERE pp_internal_name = %s LIMIT 1;",
                (cart_sku,),
            )
            row = cursor.fetchone()
            if row:
                return row.get("pp_name")
    finally:
        connection.close()
    return None


def get_order_employee(order_id: int, update_date: datetime) -> str | None:
    if not update_date:
        return None
    lookup_date = update_date
    if isinstance(update_date, datetime):
        lookup_date = update_date.replace(tzinfo=None)
    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        cursorclass=DictCursor,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT employee FROM ms_order_status_logs "
                "WHERE order_id = %s AND update_date = %s LIMIT 1; ",
                (order_id, lookup_date),
            )
            row = cursor.fetchone()
            if row:
                return row.get("employee")
    finally:
        connection.close()
    return None
