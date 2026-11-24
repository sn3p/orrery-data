# Schema-2 transition and rollback fixture

`comets-schema2/` retains the exact pre-activation catalogue from producer commit
`b0909248536811f65be3d4f5405041cf83ef59f6`, index pin
`b1b10e6d1fba1cb94cd1e03c2e39ea7ec93b584a89f74ece193dcdf8ba379567`.
It supports strict schema-2 reader, refresh, candidate-builder and rollback tests.
It is test input only, never staged as a public legacy endpoint.

The six modern payloads are unchanged by historical activation. The real schema-3
reader test verifies them against this fixture, while numerical midpoint tests
read the modern segments of the actual committed production product. Complete-site
publication tests continue to use `data/comets`.
