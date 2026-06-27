# Headless Linux image for the SmartTwinModeller MCP server (CAD generation +
# reverse-engineering + manufacturing analysis) — NO UI/GL/Qt stack.
#
# The full MCP surface (cad_generate / cad_analyze / cad_estimate_cost /
# cad_recommend_process / cad_export + discovery) is verified to load no
# vtk/PySide6/pyvista, so this image omits those heavy GL/Qt deps. A build-time
# smoke test PROVES generation + STEP/STL export work on Linux — if the image
# builds, the core pipeline is cross-platform-confirmed.
#
#   docker build -t stm-mcp .
#   docker run --rm stm-mcp python scripts/headless_smoke.py   # re-run the smoke
#   docker run -i --rm stm-mcp                                  # MCP server (stdio)
FROM python:3.13-slim

# OCCT (bundled inside the cadquery-ocp wheel) links against these X11/GL system
# libraries even for purely-headless geometry work — without them OCP fails to load.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglu1-mesa libxrender1 libxext6 libsm6 libice6 \
        libx11-6 libfontconfig1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PHONE_DESIGNER_UI_HEADLESS=1 \
    PHONE_DESIGNER_MCP_WORKSPACE=/workspace

WORKDIR /app

# 1) headless deps first (best layer caching)
COPY requirements-headless.txt ./
RUN pip install --no-cache-dir -r requirements-headless.txt

# 2) the package itself, WITHOUT re-resolving its pyproject deps (which include the
#    UI/GL stack) — --no-deps keeps the install headless.
COPY pyproject.toml ./
COPY src ./src
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps -e .

RUN mkdir -p /workspace

# 3) build-time smoke — generation + STEP/STL + cost, asserts no UI stack loads.
#    Fails the build on any regression, so a green build == Linux-verified core.
RUN python scripts/headless_smoke.py

# Default: the MCP server over stdio. An MCP client launches this image with
# `docker run -i ...` and speaks JSON-RPC on stdin/stdout.
CMD ["python", "-m", "phone_designer.mcp_server"]
