"""Parse the SAG-AFTRA RAG policy reference PDF into policy (Pxx), FAQ (Qxxx) and source (Sxx) records.

Layout of the PDF: Part 2 holds the policy records ("P05 Title", "Record type: ... | Sources: ...", "Effective scope: ...",
then the text); Part 3 holds the questions ("Q001 Question", "Mode: ... | Policies: ... | Sources: ...", then the answer
guidance); Part 4 is the source register ("S01 Title" followed by its URL).
"""
import re
from pathlib import Path

from pypdf import PdfReader

_HEADER = re.compile(r"^SAG AFTRA Plans Knowledge Reference \| v[\d.]+ \| .+$")
_PAGE_NUMBER = re.compile(r"^\d{1,3}$")
_POLICY_HEADING = re.compile(r"^(P\d{2}) (.+)$")
_FAQ_HEADING = re.compile(r"^(Q\d{3}) (.+)$")
_SOURCE_HEADING = re.compile(r"^(S\d{2}) (.+)$")
_SOURCE_CONTINUATION = re.compile(r"^S\d{2}(, S\d{2})*,?$")
_ID_LIST = re.compile(r"[PS]\d{2}")
MODES = {"REFERENCE", "LIVE", "APP_GAP", "REVIEW"}


def _lines(path: Path) -> list[str]:
    lines: list[str] = []
    for page in PdfReader(str(path)).pages:
        page_lines = [line.strip() for line in (page.extract_text() or "").splitlines()]
        if page_lines and _HEADER.match(page_lines[0]):
            page_lines.pop(0)
            if page_lines and _PAGE_NUMBER.match(page_lines[0]):
                page_lines.pop(0)
        lines.extend(line for line in page_lines if line)
    return lines


def _heading(lines: list[str], i: int, pattern: re.Pattern, meta_prefix: str) -> tuple[str, str, int] | None:
    """A heading is an ID line (its title may wrap) followed by the metadata line; returns (id, title, metadata index)."""
    match = pattern.match(lines[i])
    if match is None:
        return None
    title = match.group(2)
    for j in range(i + 1, min(i + 4, len(lines))):
        if lines[j].startswith(meta_prefix):
            return match.group(1), title, j
        title += " " + lines[j]
    return None


def _meta(lines: list[str], index: int) -> tuple[str, int]:
    """Metadata line plus any continuation of a long source list."""
    text = lines[index]
    index += 1
    while index < len(lines) and _SOURCE_CONTINUATION.match(lines[index]):
        text += " " + lines[index]
        index += 1
    return text, index


def _fields(meta: str) -> dict[str, str]:
    return {k.strip(): v.strip() for k, v in (part.split(":", 1) for part in meta.split("|") if ":" in part)}


def parse(path: Path) -> dict:
    lines = _lines(path)
    state, section = "front", ""
    policies: list[dict] = []
    faqs: list[dict] = []
    sources: dict[str, dict] = {}
    current: dict | None = None
    source_lines: list[str] = []

    def close() -> None:
        nonlocal current
        if current is not None:
            current["text"] = " ".join(current.pop("_body")).strip()
            current = None

    i = 0
    while i < len(lines):
        line = lines[i]
        if line == "Part 2 Policy and guidance records":
            state = "policies"
        elif state in ("policies", "faq") and line.startswith("Part 3 "):
            close()
            state, section = "faq", line[len("Part 3 "):]
        elif line.startswith("Part 4 Source register"):
            close()
            state = "sources"
        elif state == "faq" and re.fullmatch(r"Questions \d{3}-\d{3}", line):
            if current is not None and current["_body"]:
                section = current["_body"].pop()  # the group title sits just before its "Questions NNN-NNN" marker
            close()
        elif state == "policies" and (head := _heading(lines, i, _POLICY_HEADING, "Record type:")):
            close()
            pid, title, meta_at = head
            meta, i = _meta(lines, meta_at)
            fields = _fields(meta)
            scope = ""
            if i < len(lines) and lines[i].startswith("Effective scope:"):
                scope = lines[i].removeprefix("Effective scope:").strip()
                i += 1
            current = {
                "id": pid, "title": title, "record_type": fields.get("Record type", ""),
                "source_ids": _ID_LIST.findall(fields.get("Sources", "")), "scope": scope, "_body": [],
            }
            policies.append(current)
            continue
        elif state == "faq" and (head := _heading(lines, i, _FAQ_HEADING, "Mode:")):
            close()
            qid, question, meta_at = head
            meta, i = _meta(lines, meta_at)
            fields = _fields(meta)
            current = {
                "id": qid, "question": question, "mode": fields.get("Mode", ""), "section": section,
                "policy_ids": _ID_LIST.findall(fields.get("Policies", "")),
                "source_ids": _ID_LIST.findall(fields.get("Sources", "")),
                "sources_note": "" if _ID_LIST.search(fields.get("Sources", "")) else fields.get("Sources", ""),
                "_body": [],
            }
            faqs.append(current)
            continue
        elif state == "sources":
            if _SOURCE_HEADING.match(line):
                _flush_source(source_lines, sources)
                source_lines = [line]
            elif source_lines:
                source_lines.append(line)
        elif current is not None:
            current["_body"].append(line)
        i += 1
    close()
    _flush_source(source_lines, sources)
    return {"policies": policies, "faqs": faqs, "sources": sources}


def _flush_source(source_lines: list[str], sources: dict[str, dict]) -> None:
    if not source_lines:
        return
    sid = source_lines[0][:3]
    text = " ".join(source_lines)[4:]
    title, _, url = text.partition("http")
    url = ("http" + url).replace(" ", "") if url else ""
    sources[sid] = {"id": sid, "title": title.strip(), "url": url}


def validate(parsed: dict) -> list[str]:
    """Problems that mean the PDF was not parsed as expected (empty list when it is consistent)."""
    problems = []
    policy_ids = {p["id"] for p in parsed["policies"]}
    for n, p in enumerate(parsed["policies"], 1):
        if p["id"] != f"P{n:02d}" or not p["text"]:
            problems.append(f"policy {p['id']} out of order or empty")
    for n, q in enumerate(parsed["faqs"], 1):
        if q["id"] != f"Q{n:03d}":
            problems.append(f"question {q['id']} out of order")
        if q["mode"] not in MODES or not q["text"]:
            problems.append(f"question {q['id']} has mode {q['mode']!r} or no answer guidance")
        if not set(q["policy_ids"]) <= policy_ids:
            problems.append(f"question {q['id']} cites an unknown policy")
        if not set(q["source_ids"]) <= set(parsed["sources"]):
            problems.append(f"question {q['id']} cites an unknown source")
    return problems
