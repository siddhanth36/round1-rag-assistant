# RAG ↔ Django integration issues

Integration of `origin/feature/rag-engine` at `b02668d` into `feature/django-product`. `rag/` is imported only by
`backend/assistant/rag_adapter.py` and is unchanged (`git diff origin/feature/rag-engine -- rag/` is empty).

## Interface as implemented (matches docs/RAG_INTEGRATION_CONTRACT.md)

- Entry point: `answer_question(question, machine_id, *, store, llm, config=None) -> AnswerResult` (not `ask_question`).
- Statuses: `answered`, `insufficient_evidence`, `provider_unavailable`, `generation_error`. Django maps the first two
  one-to-one and everything else (plus any raised `RagError`/exception) to `error`, with a fixed safe message.
- Citation fields: `source_tag, chunk_id, machine_id, document_id, revision, pdf_page, source_filename, passage`.
- Indexing: `extract_pdf(...)` -> `chunk_document(doc)` -> `ChromaVectorStore(get_embedder()).index_manual(chunks, make_current=...)`.
- LLM: `create_llm_client()` reads `LLM_PROVIDER=groq`, `GROQ_API_KEY`, `GROQ_MODEL` (default `llama-3.3-70b-versatile`).

No contract difference required a change on either side.

## Items for the team

| # | Item | Status / action |
|---|---|---|
| 1 | Env names: Django's `backend/.env.example` still used `LLM_MODEL` / `LLM_API_KEY`; rag reads `GROQ_MODEL` / `GROQ_API_KEY`. | Fixed in `backend/.env.example`. |
| 2 | Two env files: rag loads `<repo>/.env`; Django loads `backend/.env` first (real env wins in both). Inside Django the values from `backend/.env` are already in `os.environ` when rag reads them. | Keep Groq keys in `backend/.env`; standalone `python -m rag.evaluation` needs them in `<repo>/.env` or the shell. |
| 3 | Upload uses `index_manual(make_current=True)` in one call (contract §8 permits it). The staged `make_current=False` -> DB commit -> `set_current_revision` flow is not used because the DB has no `is_current` column yet. | Revisit when revision switching is built. |
| 4 | Single process: embedded ChromaDB + Supabase session pooler (15 clients). `runserver` threads each keep a DB connection with `conn_max_age=60`, exhausting the pool. | Run `python manage.py runserver --nothreading` for the demo. |
| 5 | Cold start: the first question after server start took ~28 s (embedding model load + first Groq call); later ones 0.3–5 s. | Ask one warm-up question before the demo. |
| 6 | Answers may contain Unicode non-breaking hyphens (`HP‑MNT‑01`, U+2011) from the LLM; rag's validator handles them since `b02668d`. | No action; consoles with cp1252 can't print them (use UTF-8). |
| 7 | Windows: chromadb keeps index files open until process exit, so temp-dir cleanup fails in tests. | Tests use `TemporaryDirectory(ignore_cleanup_errors=True)`. |
| 8 | `challenge-pack/` (Manuals + CSV) is still not committed, although `.gitignore` whitelists it; rag tests and the isolation tests skip/fail without it. | Team decision. |
