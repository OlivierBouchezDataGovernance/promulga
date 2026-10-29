# Integrations

## Online editor (OnlyOffice)

One shared version of the document, edited and commented in the browser,
the closest to Word (native .docx, ribbon interface). Reviewers comment and
suggest changes (track changes) **in the same file**; nobody juggles copies.

| Stage | Owner | Reviewers | Others |
|---|---|---|---|
| Draft, Revision | edits, replies to comments | – | read |
| Review | comments only | comment and suggest changes | read |
| Approval, Published | read | read | read |

Start it with the `editor` profile and set `ONLYOFFICE_URL=/onlyoffice` in `.env`:

```bash
docker compose --profile editor up -d --build
```

- The free Community edition accepts up to **20 simultaneous editors**.
- It needs about 2 GB of RAM more (4 GB in total is comfortable).
- The browser reaches it through Caddy at `/onlyoffice`; Promulga and
  OnlyOffice talk over the internal Docker network, every call signed with the
  secret generated in `secrets/onlyoffice_jwt_secret`.
- Saves arrive as new revisions ("Saved from the online editor"), when the last
  person closes the document and periodically (force save, Ctrl+S).
- Check-out / check-in stays available for people who prefer Word on their PC.

## Public export to a web site (GitHub)

*Admin > Export* builds a static package of the published documents carrying
the tag `EXPORT_TAG` (default `public`, in any taxonomy):

| File | Content |
|---|---|
| `index.html` | searchable list in the site colours, links to the PDFs |
| `*.pdf` | every published version, named with `FILENAME_PATTERN` |
| `catalogue.json`, `catalogue.csv` | metadata, versions, SHA-256 |
| `catalogue.jsonld` | **DCAT-AP 3** catalogue (European standard) |
| `SHA256SUMS` | checksums of all files |

Download it as a zip, or push it to GitHub in **one commit** that replaces a
folder of a repository (removed documents disappear, unchanged files are not
re-uploaded). Configure in `.env`:

```
GITHUB_REPO=owner/repository
GITHUB_BRANCH=main
GITHUB_PATH=policies
EXPORT_SITE_URL=https://www.example.org/policies
```

and put a **fine-grained personal access token** in `secrets/github_token`:
GitHub > Settings > Developer settings > Fine-grained tokens > *Only select
repositories*: your site repository > *Repository permissions*: **Contents:
Read and write** (nothing else). Optionally tick *Settings > Integrations >
Push automatically after each publication*. Command line:
`docker compose exec app promulga export --github`.

If the site is built by GitHub Pages, Netlify or Cloudflare Pages from that
repository, the new folder is online a minute later.

## Read-only REST API

*Admin > API* creates tokens (shown once, only a hash is stored):

```bash
curl -H "Authorization: Bearer prm_..." https://docs.example.org/api/v1/documents
```

| Endpoint | Content |
|---|---|
| `GET /api/v1/documents?status=&type=&tag=` | all documents: metadata, stage, versions, tags |
| `GET /api/v1/documents/{ref}` | one document with its publications (PDF links, SHA-256) |
| `GET /api/v1/taxonomies` | taxonomies and tags, with their codes |

Working files and comments are never exposed. This is the entry point for a
data catalogue: an OpenMetadata ingestion script can read the documents and
link them to glossary terms through their tags (the `catalogue.jsonld` of the
export serves the same purpose for DCAT-aware catalogues).

## Webhooks (Teams, Slack, any JSON endpoint)

*Settings > Integrations > Webhook URLs*: one `https://` URL per line. At each
workflow event (submitted, review done, approved, rejected, published,
retired...) Promulga posts:

```json
{"text": "POL-0007 Data Quality Policy: Published v1.0 by Anna (stage Published). https://...",
 "event": "Published v1.0", "stage": "Published", "ref": "POL-0007", "version": "1.0", "url": "https://..."}
```

`text` is what Slack and Microsoft Teams incoming webhooks display. Deliveries
are retried up to 5 times by the background job.

## Electronic seal of published PDFs

`SIGN_PDFS=true` adds a PAdES signature to every published PDF: any later
change to the file breaks the signature visibly in PDF readers.

- Without `SIGNING_CERT_FILE`, Promulga creates a self-signed certificate in
  the data volume: the integrity check works, but readers see "signer
  unknown" until they trust the certificate.
- With your organisation's certificate (`.p12`), mount it in the container,
  set `SIGNING_CERT_FILE` and put its password in `secrets/signing_cert_password`.

Independently of the signature, every publication records its SHA-256, shown
on the document page and in the export.
