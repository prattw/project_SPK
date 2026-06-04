# Next steps — Project SPK

Local server is **stopped**. Work through these in order.

## 1. API keys (required for upload & chat)

```bash
cd "/Users/willpratt/Library/Mobile Documents/com~apple~CloudDocs/Project SPK"
cp .env.example .env
```

Edit `.env`:

| Key | Where to get it |
|-----|-----------------|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `VOYAGE_API_KEY` | [voyageai.com](https://www.voyageai.com) |

Optional for local dev: leave `APP_API_KEY` empty.

Test locally:

```bash
./start.sh
# Open http://127.0.0.1:8000 — upload a PDF, ask a question
# Ctrl+C to stop
```

## 2. Push to GitHub

```bash
git add .
git commit -m "Project SPK: construction document RAG"
git branch -M main
git remote add origin https://github.com/YOUR_USER/YOUR_REPO.git
git push -u origin main
```

## 3. Deploy on Railway

Follow **[DEPLOY.md](DEPLOY.md)**:

1. New project → deploy from GitHub  
2. Variables: `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `APP_API_KEY` (generate with `openssl rand -hex 32`)  
3. **Volumes:** `/app/chroma_db` and `/app/data`  
4. Public domain → share URL + access key with your team  

## 4. After go-live

- [ ] Test upload of a large PDF (background indexing + progress)  
- [ ] Confirm answers cite page numbers  
- [ ] Plan OCR later if most PDFs are scanned drawings  
