# Browser reference adapter

`CatalogSource.js` and `contract.js` derive from MIT-licensed
[Orrery3D f8c914c](https://github.com/sn3p/Orrery3D/tree/f8c914c96534abf94ed9b33c55f389e13b2d2faf/src/js/catalog).
Copyright (c) 2016 Matthijs Kuiper; see the repository [MIT license](../LICENSE).

These are a tested migration reference, not automatic adoption by either app.
The existing read/count/cancellation/identity semantics and indexed/whole v1
support remain. The additions are browser-distribution v1 validation and
`CatalogSource.openLatest(absoluteLatestURL, { signal })`. Browser distributions
support indexed reads only. See [the distribution contract](../docs/browser-delivery.md).

The consumer owner should apply these changes to its own adapter, run both
old and new fixtures and its loader/browser suite, and connect source failures
to its existing buffering/error UI with a reload action. Do not transparently
replace the source or append new-source rows into an existing scene. This
reference is tested through real HTTP and browser workflows in this repository.
