# Headless Linux / Docker deployment (MCP server)

Run the SmartTwinModeller **MCP server** (CAD generation + reverse-engineering +
manufacturing analysis) on Linux / any server with **no display** — so an LLM
client (Claude Desktop, an agent, …) can model + analyse parts.

## Why this works headless
The full MCP surface — `cad_generate`, `cad_analyze`, `cad_estimate_cost`,
`cad_recommend_process`, `cad_export`, `cad_list_skills`, `cad_get_skill_schema` —
is verified to load **no UI/GL stack** (vtk / PySide6 / pyvista). So the headless
build omits those heavy GL/Qt packages (`requirements-headless.txt`). The geometry
kernel (OCCT, via the `cadquery-ocp` wheel) + `build123d` + `mcp` are all
cross-platform with Linux/macOS/Windows wheels — there is **no Windows-specific
code in the core** (the one `os.name=="nt"` branch is in the desktop UI launcher,
which this deployment does not use).

> NOT available headless (by design): the desktop UI panels and **rendered PNG
> section views** in the HTML report (those need vtk + a display / EGL). The HTML
> report is still produced, just without embedded rendered images.

## A) Docker (recommended)

```bash
docker build -t stm-mcp .          # build-time smoke PROVES it works on Linux
```
The build runs `scripts/headless_smoke.py` (generate a part → STEP/STL → cost,
asserting no UI stack loads). **A green build == the core pipeline is
Linux-verified.** Then:

```bash
# re-run the smoke any time
docker run --rm stm-mcp python scripts/headless_smoke.py

# run the MCP server over stdio (a client launches it like this and speaks JSON-RPC)
docker run -i --rm stm-mcp
```

Mount a host dir for the generated CAD files:
```bash
docker run -i --rm -v "$PWD/out:/workspace" stm-mcp
```

### Connecting an MCP client (e.g. Claude Desktop)
Point the client's MCP config at the container command:
```json
{ "mcpServers": { "stm-cad": {
    "command": "docker",
    "args": ["run", "-i", "--rm", "-v", "/abs/host/out:/workspace", "stm-mcp"]
} } }
```
The client then calls `cad_list_skills` / `cad_get_skill_schema` to discover ops,
`cad_generate` to build a part (returns a `body_id` + STEP/STL paths +
`resource_uris`), and `cad_analyze` / `cad_estimate_cost` / `cad_recommend_process`
(by `body_id`) to analyse it. The LLM client is the natural-language → spec
interpreter — no custom parser needed.

## B) Bare Linux (no Docker)

```bash
# OCCT (inside cadquery-ocp) needs these X11/GL libs even headless:
sudo apt-get install -y libgl1 libglu1-mesa libxrender1 libxext6 libsm6 \
                        libice6 libx11-6 libfontconfig1 libgomp1

python -m venv .venv && . .venv/bin/activate
pip install -r requirements-headless.txt
pip install --no-deps -e .          # the package, headless deps already installed

export PHONE_DESIGNER_UI_HEADLESS=1
python scripts/headless_smoke.py    # verify
python -m phone_designer.mcp_server # run the MCP server (stdio)
```

## Notes / honest limits
- `pip install --no-deps -e .` installs the package WITHOUT re-resolving the
  pyproject dependencies (which include the UI/GL stack). The headless deps come
  from `requirements-headless.txt`.
- `recommend_process` is slower than the other tools (multiple cost models per
  candidate); it is closed-form, not a per-lot sweep, so still seconds, not minutes,
  for typical parts.
- This was authored on Windows; the Docker build is the cross-platform proof. If
  the build's smoke step fails on your host, it will say which call broke.
