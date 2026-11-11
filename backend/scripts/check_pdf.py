from mcp_server import knowledge_pdf

parsed = knowledge_pdf.parse(r'E:\equester-project\PlanAssistant\backend\app\data\SAG_AFTRA_RAG_Policy_Reference.pdf')
print('Policies found:', len(parsed['policies']))
for p in parsed['policies']:
    print(f"  {p['id']}: {p['title'][:80]}...")
print()
print('FAQs found:', len(parsed['faqs']))