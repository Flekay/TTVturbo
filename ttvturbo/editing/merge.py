"""State diff and deterministic three-way merge helpers."""
from __future__ import annotations

import copy
from typing import Any

_MISSING = object()


def _path_join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def three_way_merge(base: Any, ours: Any, theirs: Any, path: str = "") -> tuple[Any, list[dict[str, Any]]]:
    if ours == theirs:
        return copy.deepcopy(ours), []
    if ours == base:
        return copy.deepcopy(theirs), []
    if theirs == base:
        return copy.deepcopy(ours), []

    if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
        result: dict[str, Any] = {}
        conflicts: list[dict[str, Any]] = []
        keys = sorted(set(base) | set(ours) | set(theirs))
        for key in keys:
            b = base.get(key, _MISSING); o = ours.get(key, _MISSING); t = theirs.get(key, _MISSING)
            p = _path_join(path, str(key))
            if o is _MISSING and t is _MISSING:
                continue
            if o is _MISSING:
                if b is _MISSING:
                    result[key] = copy.deepcopy(t)
                elif t == b:
                    pass
                else:
                    conflicts.append(_conflict(p, "DELETE_MODIFY", b, None, t))
                continue
            if t is _MISSING:
                if b is _MISSING:
                    result[key] = copy.deepcopy(o)
                elif o == b:
                    pass
                else:
                    result[key] = copy.deepcopy(o)
                    conflicts.append(_conflict(p, "MODIFY_DELETE", b, o, None))
                continue
            if b is _MISSING:
                if o == t: result[key] = copy.deepcopy(o)
                else:
                    result[key] = copy.deepcopy(o)
                    conflicts.append(_conflict(p, "ADD_ADD", None, o, t))
                continue
            merged, sub = three_way_merge(b, o, t, p)
            result[key] = merged
            conflicts.extend(sub)
        return result, conflicts

    # Lists are treated atomically. This is intentional: order changes on both
    # branches need an explicit user decision instead of a lossy union.
    kind = "VALUE_CONFLICT"
    if isinstance(ours, list) and isinstance(theirs, list): kind = "ORDER_CONFLICT"
    return copy.deepcopy(ours), [_conflict(path or "$", kind, base, ours, theirs)]


def _conflict(path: str, kind: str, base: Any, ours: Any, theirs: Any) -> dict[str, Any]:
    return {"path": path, "kind": kind, "base": copy.deepcopy(base), "ours": copy.deepcopy(ours), "theirs": copy.deepcopy(theirs)}


def set_path(root: dict[str, Any], path: str, value: Any, *, delete: bool = False) -> None:
    if path in ("", "$"):
        if delete:
            root.clear()
        else:
            root.clear(); root.update(copy.deepcopy(value))
        return
    parts = path.split(".")
    current: Any = root
    for key in parts[:-1]:
        if key not in current or not isinstance(current[key], dict): current[key] = {}
        current = current[key]
    if delete or value is None and parts[-1] not in current:
        current.pop(parts[-1], None)
    else:
        current[parts[-1]] = copy.deepcopy(value)


def ancestor_distances(conn, commit_id: str) -> dict[str, int]:
    distances = {commit_id: 0}; queue = [commit_id]
    while queue:
        cid = queue.pop(0); distance = distances[cid]
        rows = conn.execute("SELECT parent_commit_id FROM edit_commit_parents WHERE commit_id=?", (cid,)).fetchall()
        for row in rows:
            parent = row[0]
            if parent not in distances or distance + 1 < distances[parent]:
                distances[parent] = distance + 1; queue.append(parent)
    return distances


def find_merge_base(conn, ours: str, theirs: str) -> str:
    oa = ancestor_distances(conn, ours); ta = ancestor_distances(conn, theirs)
    common = set(oa) & set(ta)
    if not common:
        raise RuntimeError("branches do not share a common ancestor")
    return min(common, key=lambda cid: (oa[cid] + ta[cid], max(oa[cid], ta[cid]), cid))


def state_diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    """Return a deterministic path-level diff for history inspection."""
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            p = _path_join(path, str(key))
            if key not in before:
                changes.append({"path": p, "kind": "ADDED", "before": None, "after": copy.deepcopy(after[key])})
            elif key not in after:
                changes.append({"path": p, "kind": "REMOVED", "before": copy.deepcopy(before[key]), "after": None})
            else:
                changes.extend(state_diff(before[key], after[key], p))
        return changes
    return [{"path": path or "$", "kind": "CHANGED", "before": copy.deepcopy(before), "after": copy.deepcopy(after)}]
