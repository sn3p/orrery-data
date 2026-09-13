# Optional indexed delivery trial

Tool 0.4.0 adds `export-indexed` and `verify-indexed`. The full JSON exporter and
release schemas retain their existing behavior; indexed delivery is a separate
offline derivative of an explicitly selected export. The concrete descriptor,
adapter operations and common fixtures are in [consumer contract v1](consumer-contract.md).

Next-agent instructions: [consumer adapter trial handoff](consumer-handoff.md).

## Measured producer result

A local run on 13 September 2026 used the retained full known-date export from
the saved 12 September snapshot. It contains **895,910 discovery-dated records**;
667,585 undated records remain available in the complete master. The source
export was produced by tool 0.3.0 and read without modification by tool 0.4.0.

With a **1 MiB decoded cap**, the derivative contains 114 chunks. The largest is
exactly 1,048,576 bytes. Verification established that every chunk reconstructs
the same selected numeric values and stable discovery order as the full export.

| Artifact | Plain/decoded bytes | Stored/local gzip bytes |
| --- | ---: | ---: |
| Original full catalog | 118,824,557 | 34,197,622 |
| All 114 chunks combined | 118,824,783 | 34,259,404 |
| Complete index, including 12,365 date counts | 265,372 | 82,298 |

Chunk gzip files are produced by the command. Index gzip is a separate local
level-6 size measurement; the command emits plain `index.json`. These are file
sizes, **not verified HTTP transfer sizes**. Browser/host compression behavior
remains part of the consumer/distribution trial.

Whole files needed for complete populations through representative dates:

| Through calendar date | Included records | Required chunks | Stored chunk gzip bytes |
| --- | ---: | ---: | ---: |
| 1980-02-01 | 9,262 | 2 | 621,884 |
| 2000-01-01 | 84,467 | 11 | 3,364,532 |
| 2010-01-01 | 657,774 | 84 | 25,334,961 |
| 2020-01-01 | 891,410 | 113 | 34,155,796 |

These counts use explicit calendar-midnight Julian days and inclusive `disc <= T`.
The chunk bytes include boundary-file over-fetch, but exclude the index, HTTP
headers, prefetch and cache reuse. The consumers' current local-time starting
`Date` can differ at a discovery boundary; adapter inputs use numerical Julian
days with explicit calendar conversion.

This supports an early-start trial: the February 1980 population needs about
0.62 MB of stored compressed chunks plus the index. A recent-date population or
full run still requires nearly the entire selected catalog. No browser startup,
peak memory, frame-rate, buffering or mobile improvement is established yet.

The producer run took about 56 seconds with a reported maximum resident size of
1.19 GB on this Mac, including input and output verification. Other task work
overlapped; this is an operational observation, not a controlled benchmark and
not browser memory. See [exact metrics and identities](indexed-validation-result.json).

## Verification and remaining work

The CLI suite covers unchanged inputs, full and limited selection, empty profiles,
equal-date boundaries, strict decoded byte caps, different chunk sizes/pins,
repeat activation, bad pins, corrupt/truncated/missing files and failed retries.
Small retained bundles plus request and lifecycle vectors are available under
[consumer fixtures](../tests/fixtures/consumer-v1/README.md). An independent
review checked the contract against both consumer implementations and reviewed
the producer changes and CLI boundary tests.

Next, each consumer implements the whole-file adapter against the same pinned
trial bundle, runs shared conformance through its real boundary, and adds the
indexed adapter/loading changes. Consumer acknowledgement and runtime tests are
pending independently for Orrery and Orrery3D. Production catalog selection,
release distribution, Pages compression and possible server/service migration
remain later steps in [the delivery plan](consumer-delivery-plan.md).
