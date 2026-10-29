# Promulga – visual identity

<img src="promulga-logo.svg" alt="Promulga" height="48">

**Spirit:** precision, structure, benevolent authority, clarity. Nothing
superfluous: the state of a document must speak for itself, in any language.

## Logo

The mark is **the seal**: a slate square holding a bold **P**, whose counter
carries the emerald **check** of an approved, published document. Flat shapes
only, no gradient, no glow, so it stays legible down to 16 px (favicon).

| File | Use |
|---|---|
| `promulga/static/promulga-mark.svg` | favicon, navigation bar, footer, PDF and e-mail headers |
| `docs/promulga-logo.svg` | mark + wordmark, for the README and presentations |

Keep a clear space of a quarter of the mark around it; do not recolour the
check, do not put the mark on a busy photo. An installation replaces it with
its own logo in *Settings > Appearance*.

## Colours

Brand colours are defaults: each installation can change them. The text
colour on buttons is computed automatically (white or near-black) so any
accent colour stays readable.

| Role | Hex | Contrast on white | Use |
|---|---|---|---|
| Primary – "the seal" (Slate 800) | `#1E293B` | 14.6:1 | headings, banner, mark |
| Primary dark (Slate 900) | `#0F172A` | 17.9:1 | navigation bar, footer, body text |
| Accent (Blue 600) | `#2563EB` | 5.2:1 | buttons, links, focus ring |
| Success (Emerald 700) | `#047857` | 5.5:1 | approved, published, official seal |
| Background (Slate 50) | `#F8FAFC` | – | page background; surfaces are white |
| Surface alt (Slate 100) | `#F1F5F9` | – | table headers, alternate sections |
| Secondary text (Slate 600) | `#475569` | 7.6:1 | hints, metadata |

The lighter Blue 500 (`#3B82F6`, 3.7:1) and Emerald 600 (`#059669`, 3.8:1)
are kept for large shapes only (the check of the mark uses Emerald 500
`#10B981` on slate): below 4.5:1 they fail WCAG AA for normal text.

## Workflow stages

Fixed on every installation, so that a colour always means the same thing.
Badges use the tint as background and the dark shade as text (all ≥ 5.5:1);
the stepper uses the strong colour with white text (all ≥ 5:1; Retired is never shown that way).

| Stage | FR / NL | Strong | Tint | Text on tint | Why |
|---|---|---|---|---|---|
| Draft | Rédaction / Concept | `#475569` | `#F1F5F9` | `#334155` | neutral: work in progress |
| Review | Relecture / In beoordeling | `#B45309` (amber) | `#FEF3C7` | `#92400E` | "waiting for action": stands out from the blues |
| Revision | Révision / Herziening | `#C2410C` (orange) | `#FFEDD5` | `#9A3412` | back to the owner |
| Approval | Approbation / Goedkeuring | `#2563EB` (blue) | `#DBEAFE` | `#1D4ED8` | institutional decision |
| Published | Publié / Gepubliceerd | `#047857` (emerald) | `#D1FAE5` | `#065F46` | official, in force |
| Retired | Retiré / Ingetrokken | `#94A3B8` (dot only) | `#F1F5F9` | `#475569` | no longer in force |

"Approval" is the stage (waiting for the approvers), not the result:
the result of a successful approval is "Published".

## Typography

**Inter** (variable, weights 100–900), **served by Promulga itself** – no call
to Google Fonts or any CDN, so no visitor data leaves the server (GDPR).
Latin and Latin Extended subsets cover English, French and Dutch.
Fallback: Segoe UI, system UI, Arial. Licence: SIL Open Font License 1.1
(`promulga/static/fonts/OFL-LICENSE.txt`).

## Icons

Line icons from **Lucide** (ISC licence, see `promulga/icons.py`), 24 × 24
grid, 2 px stroke, round caps – never mixed with filled icons. An icon is
always shown **with its label**: icons support reading, they do not replace words.

Default document types (editable in *Settings*, format `Name=PREFIX=icon`):

| Type | FR / NL | Icon | Meaning |
|---|---|---|---|
| Policy | Politique / Beleid | `shield-check` | the framework rule, mandatory compliance |
| Standard | Standard / Standaard | `ruler` | the measurable norm to respect |
| Guideline | Ligne directrice / Richtlijn | `compass` | recommended direction, good practice |
| Procedure | Procédure / Procedure | `list-ordered` | sequential, step-by-step process |
| Work instruction | Mode opératoire / Werkinstructie | `wrench` | how to operate a tool or a data treatment |
| Tutorial | Tutoriel / Handleiding | `graduation-cap` | learning, getting started |
| Reference | Document de référence / Referentiedocument | `library` | permanent source of truth, data dictionary |

Also available: `target` (e.g. a *Data Quality Standard* type), `pin`
(information note), `scale`, `book-open`, `clipboard-check`, `file-text`.

Two choices compared with the initial brief: *Data Quality Standard* is a
Standard tagged "Data quality" rather than a separate type, and an
information *Note* rarely needs a formal approval circuit. Both can be added
back in one line in *Settings*.

## The official seal

On the portal, the version in force is shown with the seal – emerald tint,
`shield-check` icon, "Official version 1.0 – in force since … – approved by …".
The published PDF carries the same facts in its footer on every page.
A withdrawn document shows a grey seal instead.
