# Unpublished discovery metadata

See [the contract, source assessment and Orrery handoff](../../docs/comet-discovery-metadata.md).

Pinned input: schema-4 directory `907f8cb552c65db81eb3ec3167b0d422e764ab5fef1fcbbb948e63cae21f1070`.
Candidate: `metadata.json`, SHA-256
`9eb39577942768eb76d483ed466c8db4772bc890d7a2ec4f00cc41f265b706e5`, 1,072,072 bytes.
These are review artifacts, not hosted assets or an activated descriptor.

| Coverage | Entries |
|---|---:|
| Full pinned catalogue | 4,089 |
| Original histories preserved | 6 |
| Additional sourced metadata | 120 |
| Total with sourced metadata | 126 |
| Numbered source responses empty/all-null | 486 |
| Remaining outside assessed source scope | 3,477 |
| Trajectory-bearing entries with curated events | 14 / 14 |
| All entries with curated events | 16 |
| Curated events, including original histories | 29 |

The 610 numbered SBDB responses contain 124 meaningful discovery records,
including the original six (whose older provenance is deliberately preserved).
Of the 118 new catalogue dates, 114 have day precision and four month precision
(source day `00`, not a valid day). Two additional unnumbered entries have
NASA-attributed curated events. Counts refer to source coverage, not complete
historical knowledge or publishable trajectories. All events explicitly retain
an incomplete inventory flag. Unknown dates remain null; all-null raw discovery
objects remain available for inspection and are counted as empty.

`baseline.json` prevents accidental rebinding to a future catalogue.
`events.json` holds curated input and citations. `sbdb-responses.json.gz` retains
610 canonicalized API responses, request URLs and actual retrieval times.
Per-response hashes cover the documented canonical serialization, not original
HTTP bytes. Empty records and original-six current responses remain in evidence;
no current response replaces those six histories. This snapshot was retrieved
serially (one request at a time) on 25 November 2026. Repeating acquisition is a
new assessment, not a reproducibility test; rebuild the candidate offline from
these committed inputs.

No orbit, period, observation-fit date, designation or comet name is used to
invent discovery circumstances. No parent/fragment discovery is inherited.
