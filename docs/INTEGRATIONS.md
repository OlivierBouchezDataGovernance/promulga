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

*Admin > Export* builds a static package of the published documents whose
**Website publication** allows it: `Public` for a public site (external and
commercial publications only), `Internal` or `Public` for an intranet site
(scope "all"). Until Promulga 0.5 the tag `EXPORT_TAG` selected them; the
migration to 0.5 set Website publication = Public on those documents.

Option *Also list every other published document*: the other published
documents (Audit ones excepted) are listed too, with their metadata, without
PDF, checksum nor text (`"listed_only": true` in `catalogue.json`), for a
library that shows the whole set. Their titles become visible on the site,
limited and confidential ones included: tick it only if that is acceptable.
Each entry of `catalogue.json` carries `parent`, `keywords`, `domain` and the
`text` of the version in force (60,000 characters at most).

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
GITHUB_FOLDER=policies
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
| `GET /api/v1/documents?status=&type=&tag=&chatbot=&website=&domain=&linked=` | all documents: metadata, stage, versions, tags, chatbot use and website publication (`no`, `internal`, `public`), data `domain`, `parent`, `catalog_links` (OpenMetadata glossary terms and data products), last periodic review confirmed; `linked` = fully qualified name of a glossary term or data product |
| `GET /api/v1/documents/{ref}` | one document with its publications (PDF links, SHA-256) |
| `GET /api/v1/corpus?audience=&since=` | knowledge feed of a chatbot (chatbot token only): clean text of the versions in force whose Chatbot use reaches the audience (`public`, or `internal` = Internal or Public; default: the token's), capped by the classification. With `since` (ISO date or date and time): only what was opened or republished since, plus `withdrawals` (ref, date, reason: retired, chatbot use, classification, audit, deleted) |
| `GET /api/v1/taxonomies` | taxonomies and tags, with their codes |

Working files and comments are never exposed. This is the entry point for a
data catalogue: an OpenMetadata ingestion script can read the documents and
link them to glossary terms through their tags (the `catalogue.jsonld` of the
export serves the same purpose for DCAT-aware catalogues).

## Data catalogue: OpenMetadata

Every document has a **data domain** (one per document, *Edit details*): the
name of its domain in OpenMetadata, or any name your tools agree on. The
migration to 0.6 fills it from the tag of the taxonomy *Domain* when there is
one (the top of the tag tree). Other tools read it from the API (`domain`,
`?domain=`) and from the export catalogue (`domain`).

On the document page, *Data catalogue* links the document to **glossary terms**
and **data products**, by their fully qualified name (`Business Glossary.Customer`).

```bash
OPENMETADATA_URL=https://openmetadata.example.org     # in .env
nano secrets/openmetadata_token                       # token of a bot (Settings > Bots in OpenMetadata)
```

- Without `OPENMETADATA_URL`, the names are kept as typed.
- With the URL, the domain and the links open the entity in OpenMetadata.
- With a bot token as well, Promulga checks that the entity exists (and shows
  its display name), and adds the document to the **References** of each
  linked glossary term (name "REF Title", link to the portal page) once it is
  published; unlinking removes it. Limited, confidential and Audit documents
  are never named in OpenMetadata. The bot needs the right to edit glossary terms.
- Domains and data products have no references in OpenMetadata: a catalogue
  shows their documents by reading `GET /api/v1/documents?domain=...` or
  `?linked=<data product FQN>`.

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
