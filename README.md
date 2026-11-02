# INDUSTRIX AI - Manufacturing Manual Assistant

Round 1 hackathon project: a web application where engineers select a machine, ask a maintenance question,
and get an answer grounded **only** in that machine's PDF manuals, with verifiable citations (document,
revision, original PDF page, supporting passage). Requirements come from
`challenge-pack/Round1_Student_Challenge_Brief.pdf`.

## Status

| Part | Branch | Status |
|---|---|---|
| RAG engine (`rag/`): PDF extraction, chunking, embeddings, ChromaDB, machine-filtered retrieval, revisions, grounded answering, citation validation | `feature/rag-engine` | Implemented; 233 automated tests |
| LLM provider | `feature/rag-engine` | Groq adapter (`LLM_PROVIDER=groq`, `GROQ_API_KEY`); unit-tested with a mocked SDK |
| Django + DRF backend, auth/roles, PostgreSQL models, Bootstrap UI | `feature/django-product` | Not started on that branch |

## Required technologies

Mandated by the challenge brief (do not replace): see `docs/TECH_STACK.md` for versions and rationale.

| Layer | Technology |
|---|---|
| Backend / API | Python 3, Django, Django REST Framework |
| Frontend | Django templates, HTML5, CSS3, JavaScript, Bootstrap 5 |
| Database | PostgreSQL (design kept MySQL-compatible) |
| PDF extraction | PyMuPDF |
| Embeddings | Sentence Transformers `all-MiniLM-L6-v2` |
| Vector store | ChromaDB (persistent, local) |
| Answer generation | Groq API (official `groq` SDK), configured by environment variables |

## Repository layout

```
rag/               RAG engine (no Django imports) + tests — see docs/RAG_INTEGRATION_CONTRACT.md
docs/              Architecture, database, API, tech stack, plan, integration contract and examples
challenge-pack/    Supplied challenge material (do not modify); only Manuals/*.pdf are indexed
.env.example       Environment variable template (copy to .env; never commit secrets)
CLAUDE.md          Project rules for AI-assisted development
```

## Setup on Kali Linux (Python 3.13)

Kali's system Python is "externally managed" (PEP 668): never install packages globally, with `sudo pip`
or with `--break-system-packages`. Everything goes into the project virtual environment `.venv/`.

If `python3 -m venv` fails because the venv module is missing, install the system packages once:

```bash
sudo apt update && sudo apt install -y python3-venv python3-pip
```

Create and activate the virtual environment from the repository root, then install the dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate          # run in every new terminal; deactivate with `deactivate`
python -m pip install --upgrade pip
# CPU-only PyTorch first (avoids several GB of CUDA libraries):
python -m pip install "torch>=2.15,<3" --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r rag/requirements-dev.txt
```

Never install with `sudo pip` or `--break-system-packages`. The first run downloads the embedding model
`sentence-transformers/all-MiniLM-L6-v2` (~90 MB) into `~/.cache/huggingface`; afterwards `HF_HUB_OFFLINE=1`
works.

The tests and the evaluation read only `challenge-pack/Manuals/*.pdf` (three manuals) and
`challenge-pack/machine_manual_index.csv`. These are not committed until redistribution of the challenge
material is confirmed: copy them from the official challenge distribution into `challenge-pack/` (same
names and folder layout). The challenge brief, `START_HERE.txt` and the original ZIP are deliberately never
committed; keep them locally in `challenge-pack/` too (the brief is the source of truth for requirements).

## Tests and evaluation

Run from the repository root with the virtual environment activated:

```bash
python -m pytest rag/tests -v     # from the repository root
python -m rag.evaluation          # 13-question retrieval evaluation on the supplied manuals
```

## Configuration

All settings are environment variables with safe defaults; see `.env.example`. The vector index is stored in
`chroma_db/` (git-ignored, rebuildable from the PDFs).

LLM (Groq): copy `.env.example` to `.env` and set `GROQ_API_KEY` there (never commit it). `rag/config.py`
loads the project-root `.env` automatically; variables already set in the shell take precedence.
`GROQ_MODEL` defaults to `llama-3.3-70b-versatile`. Unit tests mock the Groq SDK and need no key; a live
smoke test is in `docs/PROJECT_ARCHITECTURE.md`.

## Documentation

- `docs/PROJECT_ARCHITECTURE.md` — architecture, RAG pipeline, abstention, evaluation results
- `docs/DATABASE_ARCHITECTURE.md` — relational schema and ChromaDB metadata
- `docs/API_CONTRACT.md` — REST endpoints (proposed) and RAG status mapping
- `docs/TECH_STACK.md` — technologies and tested versions
- `docs/IMPLEMENTATION_PLAN.md` — phases, acceptance criteria, open questions
- `docs/RAG_INTEGRATION_CONTRACT.md` — exact `rag/` interface for Django
- `docs/DJANGO_INTEGRATION_EXAMPLE.md` — illustrative Django service code

## Branches and ownership

| Branch | Owner | Scope |
|---|---|---|
| `main` | shared | Docs, `CLAUDE.md`, `README.md`, `.gitignore`, `.env.example`; integration target |
| `feature/rag-engine` | Developer A | `rag/`: extraction, chunking, embeddings, ChromaDB, retrieval, LLM interface, citation validation, RAG tests |
| `feature/django-product` | Developer B | Django project: auth/roles, models, DRF APIs, manual upload, Bootstrap UI, history, supervisor view, RAG integration |

Do not edit the other developer's files without coordinating. The `rag/` ↔ Django boundary is defined in
`docs/RAG_INTEGRATION_CONTRACT.md`. Equipment and data in the challenge pack are fictional training material.
