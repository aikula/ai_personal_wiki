User question
     │
     ▼
QueryAgent.stream()
     │
     ├─ _is_meta_question? ──► WikiFS.get_wiki_tree() ──► instant answer
     │
     ├─ _classify() ──► "factual" / "comparison" / "exploratory"
     │
     ├─ factual ──► _retrieve_pages(grep) ──► _generate_answer ──► stream chunks
     │
     ├─ comparison ──► _retrieve_pages(all projects) ──► side-by-side ──► stream
     │
     └─ exploratory ──► ReAct loop:
            ├─ search_wiki ──► WikiFS.search_pages()
            ├─ read_page   ──► WikiFS.read_page()
            ├─ execute_code──► CodeInterpreter.execute()
            └─ answer      ──► return + stream

Ingest trigger
     │
     ▼
IngestAgent.run()
     ├─ Step1: _find_related_pages (CodeInterpreter) ──► LLM analysis
     ├─ Step2: LLM generates pages ──► WikiFS.write_page() (validates)
     ├─ _record_conflicts() ──► WikiFS.append_conflict()
     └─ WikiLinter.lint(incremental)

Scheduled / manual audit
     │
     ▼
AuditAgent.run(llm_audit=True/False)
     │
     asyncio.gather:
     ├─ WikiLinter (structural) ────────────────────────────────────┐
     ├─ LLM semantic cluster_1 (project A)                          ├──► AuditReport
     ├─ LLM semantic cluster_2 (project B)                          │
     └─ LLM semantic cluster_3 (cross-project)  ────────────────────┘
                                    │
                         auto_conflict=True?
                                    │
                         WikiFS.append_conflict()