"""Export layer: flatten a SIA run into the keystone `demo_data.json` contract.

`flatten.py` is the decoupling seam between the `sia/` generation loop and the
static web app: it walks `runs/<run>/gen_*` (read-only), pulls metrics, computes
target-agent diffs, regex-flags SQL self-repair, attaches the R2 taxonomy (from
`hackathon_demo.analyze`), and emits one self-contained JSON file the web app reads
without importing anything from `sia/`.
"""
