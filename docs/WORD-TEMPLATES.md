# Word templates, variables and cover page

Promulga keeps the metadata of a document **inside the Word file**: a cover
page and variables that are refreshed automatically. This works with **.docx**
files only; other formats get a "limited capability" warning (their published
PDF still receives a generated cover page).

## The cover page

At the first check-in (and at every later check-in, download and publication)
Promulga inserts or refreshes a cover page at the very beginning of the
document, followed by a page break:

- reference and type, title, description;
- a table: reference, type, domain, version, status, owner, reviewers,
  approvers, created, last update, published, next review;
- the **version history**: version, date, author, change note, approved by.

The cover page is one *locked content control* named "Promulga cover page":
Word and OnlyOffice show it as a block that cannot be edited. Changes made to
it would be overwritten anyway. Without any file, Promulga creates a blank
Word document that already has the cover page and standard section headings.

## Variables in the text

Anywhere in a template or a document, write a tag between double braces:

| Tag | Value |
|---|---|
| `{{REF}}` | reference, e.g. POL-0007 |
| `{{ID}}` | global document number, 0007 (5 digits from 10000) |
| `{{TITLE}}` / `{{DESCRIPTION}}` | title / summary |
| `{{TYPE}}` / `{{TYPE_CODE}}` | Policy / POL |
| `{{DOMAIN}}` | tag of the document in the taxonomy whose slug is `domain` |
| `{{VERSION}}` / `{{SUBVERSION}}` / `{{FULL_VERSION}}` | 1 / 2 / 1.2 |
| `{{STATUS}}` | workflow stage |
| `{{AUTHOR}}` / `{{REVIEWERS}}` / `{{APPROVERS}}` | participants |
| `{{CREATED}}` / `{{UPDATED}}` / `{{PUBLISHED}}` / `{{NEXT_REVIEW}}` | dates (yyyy-mm-dd) |

At the first check-in each tag becomes a Word **content control** (tag
`promulga:TITLE`, ...). From then on Promulga rewrites its content, so the
header "Version 1.2 - Published 2026-11-05" is always right, also in the PDF.
Advanced users can insert content controls themselves (Developer tab >
Plain text content control > Properties > Tag `promulga:VERSION`).

Tip: put `{{REF}} v{{FULL_VERSION}} - {{STATUS}}` in the page header of the
template.

## File names

Downloads, published PDFs and the public export are named with
`FILENAME_PATTERN` (in `.env`), for example:

```
FILENAME_PATTERN={CODE:domain}-{TYPE_CODE}-{VERSION}-{SUBVERSION}-{TITLE}
```

gives `DG-POL-1-0-Data Quality Policy.pdf`. `{CODE:<taxonomy>}` is the
**code** of the document's tag in that taxonomy (set codes in *Tags*). While
a document is being prepared, `-draft` is appended. Keep `{REF}` or `{ID}` in
the pattern if two documents could otherwise get the same name.

The reference itself follows `REF_PATTERN` (default `{TYPE_CODE}-{ID}`); it
must contain `{ID}`, the global number, which keeps every reference unique.
