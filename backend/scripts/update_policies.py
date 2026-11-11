from mcp_server import knowledge_pdf
import json

parsed = knowledge_pdf.parse(r'E:\equester-project\PlanAssistant\backend\app\data\SAG_AFTRA_RAG_Policy_Reference.pdf')

policies = []
for p in parsed['policies']:
    policies.append({
        "topic": p["id"],
        "content": p["text"]
    })

# Sort by topic id
policies.sort(key=lambda x: x["topic"])

output_path = r'E:\equester-project\PlanAssistant\backend\app\data\policies.json'
with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(policies, f, indent=2, ensure_ascii=False)

print(f"Written {len(policies)} policies to {output_path}")
for p in policies:
    print(f"  {p['topic']}: {p['content'][:80]}...")