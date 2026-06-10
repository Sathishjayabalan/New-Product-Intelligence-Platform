"""Signal Ingestion Hub — all ingestion methods (F-01).

Per PRD F-01 the hub supports 'REST APIs, file upload, CRM/ERP, streaming
feeds' across 10+ source types. This module implements every ingestion
path; each path normalises records into SignalIn-shaped dicts that flow
through the single `listening.ingest_signal` entrypoint (so dedup,
classification and quality scoring apply uniformly).

Methods:
  1. REST single JSON           POST /signals
  2. REST batch JSON            POST /signals/batch
  3. File upload                POST /signals/upload   (.csv .json .jsonl .ndjson .txt)
  4. Generic webhook receiver   POST /webhooks/{source_name}
  5. Streaming (NDJSON body)    POST /signals/stream
  6. CRM / ERP / SaaS connectors POST /connectors/{name}/sync
  7. Raw email                  POST /signals/email
  8. URL feed pull              POST /signals/pull
"""

import csv
import io
import json
from email import message_from_string

# Keys probed (in order) when extracting free-text content from arbitrary
# webhook / connector payloads.
CONTENT_KEYS = [
    "content", "text", "message", "body", "description", "feedback",
    "comment", "verbatim", "note", "summary", "transcript",
]
LIST_KEYS = ["signals", "records", "items", "events", "data", "results", "messages"]


class IngestionError(ValueError):
    pass


def _record(content: str, source_type: str, source_name: str, tags=None) -> dict:
    content = (content or "").strip()
    if len(content) < 3:
        raise IngestionError("Record has no usable text content")
    return {
        "content": content,
        "source_type": source_type,
        "source_name": source_name,
        "tags": tags or [],
    }


# ------------------------------------------------------------ file upload --
def parse_file(filename: str, raw: bytes) -> list[dict]:
    """Method 3: file upload. Supports CSV (a `content` column, else the
    first column), JSON (array or wrapped list), JSONL/NDJSON, and plain
    text (one signal per line)."""
    name = filename.lower()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionError("File must be UTF-8 encoded") from exc

    if name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise IngestionError("CSV file has no header row")
        content_col = next(
            (c for c in reader.fieldnames if c.strip().lower() in CONTENT_KEYS),
            reader.fieldnames[0],
        )
        return [
            _record(
                row.get(content_col, ""),
                row.get("source_type", "file"),
                row.get("source_name", filename),
                [t.strip() for t in row.get("tags", "").split(";") if t.strip()],
            )
            for row in reader
            if (row.get(content_col) or "").strip()
        ]

    if name.endswith((".jsonl", ".ndjson")):
        return parse_ndjson(text, source_name=filename)

    if name.endswith(".json"):
        payload = json.loads(text)
        items = payload if isinstance(payload, list) else _unwrap_list(payload)
        if items is None:
            raise IngestionError("JSON file must be an array or contain a list field")
        return [_coerce_item(item, "file", filename) for item in items]

    if name.endswith(".txt"):
        return [
            _record(line, "file", filename)
            for line in text.splitlines()
            if line.strip()
        ]

    raise IngestionError(
        f"Unsupported file type '{filename}'. Supported: .csv .json .jsonl .ndjson .txt"
    )


# -------------------------------------------------------------- streaming --
def parse_ndjson(body: str, source_name: str = "stream") -> list[dict]:
    """Method 5: streaming feeds — newline-delimited JSON, one event per
    line (the shape produced by Kafka/Kinesis sink connectors)."""
    records = []
    for i, line in enumerate(body.splitlines()):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IngestionError(f"Line {i + 1} is not valid JSON") from exc
        records.append(_coerce_item(item, "stream", source_name))
    if not records:
        raise IngestionError("Stream body contained no events")
    return records


# ---------------------------------------------------------------- webhook --
def map_webhook(payload, source_name: str) -> list[dict]:
    """Method 4: generic webhook receiver. Accepts a single object, a list,
    or a wrapped list, and probes common field names for text content."""
    if isinstance(payload, list):
        return [_coerce_item(p, "webhook", source_name) for p in payload]
    if isinstance(payload, dict):
        items = _unwrap_list(payload)
        if items is not None:
            return [_coerce_item(p, "webhook", source_name) for p in items]
        return [_coerce_item(payload, "webhook", source_name)]
    raise IngestionError("Webhook payload must be a JSON object or array")


def _unwrap_list(payload: dict):
    for key in LIST_KEYS:
        if isinstance(payload.get(key), list):
            return payload[key]
    return None


def _coerce_item(item, default_source_type: str, source_name: str) -> dict:
    if isinstance(item, str):
        return _record(item, default_source_type, source_name)
    if not isinstance(item, dict):
        raise IngestionError(f"Cannot ingest record of type {type(item).__name__}")
    content = next(
        (item[k] for k in CONTENT_KEYS if isinstance(item.get(k), str) and item[k].strip()),
        None,
    )
    if content is None:
        # one level of nesting (e.g. {"event": {"text": ...}})
        for v in item.values():
            if isinstance(v, dict):
                inner = next(
                    (v[k] for k in CONTENT_KEYS if isinstance(v.get(k), str) and v[k].strip()),
                    None,
                )
                if inner:
                    content = inner
                    break
    if content is None:
        raise IngestionError(
            f"No text content found in record; probed keys: {CONTENT_KEYS}"
        )
    return _record(
        content,
        item.get("source_type", default_source_type),
        item.get("source_name", source_name),
        item.get("tags") or [],
    )


# ------------------------------------------------------------- connectors --
def _salesforce(payload: dict) -> list[dict]:
    return [
        _record(
            " — ".join(filter(None, [r.get("Subject"), r.get("Description")])),
            "crm",
            "salesforce",
            [r["Type"].lower()] if r.get("Type") else [],
        )
        for r in payload.get("records", [])
    ]


def _hubspot(payload: dict) -> list[dict]:
    out = []
    for r in payload.get("results", []):
        props = r.get("properties", {})
        text = props.get("content") or props.get("hs_note_body") or ""
        out.append(_record(text, "crm", "hubspot"))
    return out


def _zendesk(payload: dict) -> list[dict]:
    return [
        _record(
            " — ".join(filter(None, [t.get("subject"), t.get("description")])),
            "support_ticket",
            "zendesk",
        )
        for t in payload.get("tickets", [])
    ]


def _intercom(payload: dict) -> list[dict]:
    return [
        _record((c.get("source") or {}).get("body", ""), "support_ticket", "intercom")
        for c in payload.get("conversations", [])
    ]


def _sap_erp(payload: dict) -> list[dict]:
    return [
        _record(
            i.get("LongText", ""), "erp", "sap",
            [i["DocumentNo"]] if i.get("DocumentNo") else [],
        )
        for i in payload.get("items", [])
    ]


def _kafka(payload: dict) -> list[dict]:
    out = []
    for m in payload.get("messages", []):
        value = m.get("value", "")
        if isinstance(value, dict):
            out.append(_coerce_item(value, "stream", "kafka"))
        else:
            out.append(_record(str(value), "stream", "kafka"))
    return out


CONNECTORS = {
    "salesforce": {"extract": _salesforce, "kind": "CRM", "list_key": "records"},
    "hubspot": {"extract": _hubspot, "kind": "CRM", "list_key": "results"},
    "zendesk": {"extract": _zendesk, "kind": "Support", "list_key": "tickets"},
    "intercom": {"extract": _intercom, "kind": "Support", "list_key": "conversations"},
    "sap_erp": {"extract": _sap_erp, "kind": "ERP", "list_key": "items"},
    "kafka": {"extract": _kafka, "kind": "Streaming", "list_key": "messages"},
}


def run_connector(name: str, payload: dict) -> list[dict]:
    """Method 6: CRM / ERP / SaaS connector sync — accepts each system's
    native export shape and maps it to the unified signal schema."""
    if name not in CONNECTORS:
        raise IngestionError(
            f"Unknown connector '{name}'. Available: {sorted(CONNECTORS)}"
        )
    records = CONNECTORS[name]["extract"](payload)
    if not records:
        raise IngestionError(
            f"Connector payload contained no records "
            f"(expected list under '{CONNECTORS[name]['list_key']}')"
        )
    return records


# ------------------------------------------------------------------ email --
def parse_email(raw: str) -> dict:
    """Method 7: raw RFC-822 email (e.g. a VoC inbox forward). Subject and
    plain-text body become the signal."""
    msg = message_from_string(raw)
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                body = payload.decode("utf-8", "replace") if payload else ""
                break
    else:
        payload = msg.get_payload(decode=True)
        body = (
            payload.decode("utf-8", "replace")
            if isinstance(payload, bytes)
            else str(msg.get_payload())
        )
    subject = msg.get("Subject", "")
    content = " — ".join(filter(None, [subject.strip(), body.strip()]))
    sender = msg.get("From", "email")
    return _record(content, "email", sender)


# --------------------------------------------------------------- URL pull --
def parse_feed(text: str, fmt: str, source_name: str) -> list[dict]:
    """Method 8: pull-based feed ingestion (market feeds, exported reports
    hosted at a URL). The fetched body is parsed as json | ndjson | txt."""
    if fmt == "json":
        payload = json.loads(text)
        items = payload if isinstance(payload, list) else _unwrap_list(payload)
        if items is None:
            raise IngestionError("Feed JSON must be an array or contain a list field")
        return [_coerce_item(i, "market_feed", source_name) for i in items]
    if fmt == "ndjson":
        records = parse_ndjson(text, source_name)
        for r in records:
            r["source_type"] = "market_feed"
        return records
    if fmt == "txt":
        return [
            _record(line, "market_feed", source_name)
            for line in text.splitlines()
            if line.strip()
        ]
    raise IngestionError("Feed format must be one of: json, ndjson, txt")
