"""Standalone script: read a JSON file of policies, embed each one and store it in Qdrant (policy_topics).

Run from backend/:
  python -m mcp_server.embed_json                          # uses app/data/policies.json
  python -m mcp_server.embed_json path\\to\\my_policies.json
  python -m mcp_server.embed_json --show "pension eligibility"   # embed a question and show the nearest topic

JSON format: [{"topic": "pension", "content": "text of the policy ..."}, ...]
"""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from qdrant_client import models  # noqa: E402

from mcp_server import embeddings, vectordb  # noqa: E402  (needs .env loaded first)

DEFAULT_JSON = Path(__file__).resolve().parents[1] / "app" / "data" / "policies.json"


def store(path: Path) -> int:
    items = json.loads(path.read_text(encoding="utf-8"))
    vectordb.create_schema()
    # 1. call the embedding function: list of texts in -> list of 384-number vectors out
    vectors = embeddings.embed([f"{p['topic']}: {p['content']}" for p in items])
    # 2. store text + vector in Qdrant
    vectordb._upsert(vectordb.TOPICS, [
        models.PointStruct(
            id=vectordb._id("topic", p["topic"]), vector=vec,
            payload={"topic": p["topic"], "content": p["content"], "content_hash": vectordb._hash(p["topic"], p["content"])},
        )
        for p, vec in zip(items, vectors)
    ])
    return len(items)


def show(question: str) -> None:
    vec = embeddings.embed([question])[0]
    row = vectordb._nearest(vectordb.TOPICS, question)
    print(f"vector length={len(vec)}, first 5 numbers={[round(x, 4) for x in vec[:5]]}")
    print(f"nearest topic: {row['topic']} (distance {row['distance']:.3f})" if row else "no topics stored")


if __name__ == "__main__":
    if not vectordb.enabled():
        raise SystemExit("Set QDRANT_URL or QDRANT_PATH in .env first")
    args = sys.argv[1:]
    if args and args[0] == "--show":
        show(" ".join(args[1:]) or "pension eligibility")
    else:
        file = Path(args[0]) if args else DEFAULT_JSON
        print(f"embedded and stored {store(file)} policies from {file}")
