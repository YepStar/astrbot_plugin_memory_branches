"""Compatibility facade for existing Self Learning / Bili memory clients.

The authoritative data is always in Memory Branches. No legacy fallback writes.
"""

from __future__ import annotations

import hashlib
import json
import re

def public_source(scope):
    return scope in {"xiaoheihe:public", "bilibili:public"} or bool(re.fullmatch(r"group:\d+", str(scope)))


class PublicMemory:
    def __init__(self, plugin, bank):
        self.plugin, self.engine, self.bank = plugin, plugin.engine, bank
        self.path = self.engine.path

    def put(self, scope, key, content, *, role="memory", payload=None):
        if self.engine.route(scope) != self.bank:
            raise PermissionError("Public source not bound to this memory bank")
        payload = payload or {}
        if ":FriendMessage:" in str(
            payload.get("umo") or payload.get("session_id") or ""
        ):
            raise PermissionError("QQ private data cannot enter public memory")
        event_key = str(key)
        if role in {"memory", "reflection"}:
            event_key += (
                "@"
                + hashlib.sha256(
                    json.dumps(
                        [content, payload], sort_keys=True, ensure_ascii=False
                    ).encode()
                ).hexdigest()[:20]
            )
        self.engine.append(self.bank, scope, event_key, role, content, metadata=payload)
        self.plugin.kick(self.bank)
        return hashlib.sha256(f"{scope}:{key}:{role}".encode()).hexdigest()

    def rows(self, scope=None, limit=200, *, public_only=False):
        if scope is not None and self.engine.route(scope) != self.bank:
            raise PermissionError("Unknown public memory scope")
        with self.engine.connect() as db:
            rows = db.execute(
                "SELECT * FROM messages WHERE bank=? ORDER BY id DESC LIMIT ?",
                (self.bank, max(1, min(10000, int(limit)))),
            ).fetchall()
        return [
            {
                "id": str(r["id"]),
                "scope": r["source"],
                "event_key": r["event_key"],
                "role": r["role"],
                "content": r["content"],
                "payload": json.loads(r["metadata"]),
                "created": r["created"],
                "updated": r["created"],
            }
            for r in rows
            if (not scope or r["source"] == scope)
            and (not public_only or public_source(r["source"]))
        ]

    def search(self, query, scope, limit=5):
        if not public_source(scope) or self.engine.route(scope) != self.bank:
            return []
        from .engine import tokens

        q = tokens(query)
        matches = []
        for row in self.rows(limit=10000, public_only=True):
            overlap = q & tokens(row["content"])
            if overlap:
                matches.append((len(overlap), row))
        return [
            r
            for _, r in sorted(matches, key=lambda item: item[0], reverse=True)[
                : max(1, min(30, limit))
            ]
        ]

    def context(self, query, scope):
        # The new plugin's hook supplies context exactly once.
        return ""

    @staticmethod
    def bili_scope(record):
        scope = str(record.get("scope") or "")
        if scope == "bili_dm":
            return "bilibili:dm:" + str(record.get("user_id", ""))
        if scope in {"self", "admin"}:
            return "bilibili:self"
        if scope in {
            "bili_comment",
            "bili_live",
            "proactive",
            "analytics",
            "background",
        }:
            return "bilibili:public"
        raise PermissionError("Unclassified or private Bili source")

    def put_bili(self, record):
        scope = self.bili_scope(record)
        if self.engine.route(scope) != self.bank:
            raise PermissionError(
                "This Bili scope is not bound to the public memory bank"
            )
        return self.put(
            scope, str(record["rpid"]), record.get("text", ""), payload=dict(record)
        )

    def bili_records(self):
        result = {}
        keep = self.engine.dataset(self.bank, "bili_keep.json", None)
        for row in self.rows(limit=10000):
            payload = row["payload"]
            rpid = str(payload.get("rpid") or "")
            if (
                row["scope"].startswith("bilibili:")
                and rpid
                and rpid not in result
                and (keep is None or rpid in keep)
            ):
                result[rpid] = payload
        return list(result.values())

    def replace_bili(self, records):
        # Existing callers rewrite normalized metadata; do not remove original conversation records.
        for record in records:
            self.bili_scope(record)
        for record in records:
            self.put_bili(record)
        self.engine.put_dataset(
            self.bank, "bili_keep.json", [str(r["rpid"]) for r in records]
        )

    def get_dataset(self, name, default=None):
        return (
            self.bili_records()
            if name == "memory.json"
            else self.engine.dataset(self.bank, name, default)
        )

    def put_dataset(self, name, data):
        if name not in {
            "memory.json",
            "permanent_memory.json",
            "video_memory.json",
            "external_memory.json",
            "bangumi_memory.json",
        }:
            raise PermissionError("Unknown public dataset")
        if ":FriendMessage:" in json.dumps(data, ensure_ascii=False):
            raise PermissionError("Private provenance rejected")
        if name == "memory.json":
            self.replace_bili(data)
        else:
            self.engine.put_dataset(self.bank, name, data)

    def stats(self):
        from collections import Counter

        return {
            "backend": "memory_branches",
            "path": str(self.path),
            "scopes": dict(Counter(r["scope"] for r in self.rows(limit=10000))),
            "livingmemory_delegation": False,
            "qq_private_allowed": False,
        }

    def graph(self, scope=None, limit=120):
        graph = self.engine.graph(self.bank, limit=min(300, int(limit)))
        return {
            "success": True,
            "type": "memory",
            "data_source": "memory_branches",
            "nodes": [dict(n, name=n["label"]) for n in graph["nodes"]],
            "links": graph["edges"],
            "categories": [],
            "stats": {"nodes": len(graph["nodes"]), "links": len(graph["edges"])},
        }
