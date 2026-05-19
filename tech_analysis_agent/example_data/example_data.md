# NVIDIA Company Portfolio Example Data

This file documents the static mock data used when `USE_MOCK_PATENT=true` for
the `C_company_portfolio` Patent Agent mode.

The machine-readable data lives in:

```text
tech_analysis_agent/example_data/nvidia_company_portfolios.json
tech_analysis_agent/example_data/nvidia_actor_similarity_map.json
```

## Scenario

- Company: NVIDIA
- Industry: AI / Semiconductor / GPU
- Planning horizon: 2026-2030
- Strategic direction:
  - Maintain leadership in AI hardware (GPU)
  - Expand AI infrastructure and platform ecosystem
  - Strengthen end-to-end AI stack (hardware + software)

## Actor Set

The example portfolio uses NVIDIA as the center actor and compares it against:

- AMD
- Intel
- Google
- Broadcom
- Qualcomm
- TSMC
- Arm

## Technology Themes

The sample records are intentionally centered on themes that matter for an
AI/GPU roadmap:

- coherent GPU interconnects and NVLink-like fabrics
- chiplet GPU architecture
- HBM/interposer/CoWoS-style advanced packaging
- 3D die stacking and TSV-based packaging
- GPU memory compression and memory-capacity management
- rack-scale AI fabrics and accelerator cluster networking
- TPU/NPU-style inference accelerators
- co-packaged optical I/O and SerDes scale-out fabrics
- AI compiler/runtime scheduling

## Source Notes

This is not a legal patent dataset. It is a compact, deterministic example set
for validating the agent flow and the `actor_similarity_map` renderer without
KIPRIS/Tavily API access.

The themes are based on public technology signals such as NVIDIA NVLink-C2C,
Grace/Blackwell memory-coherent interconnects, Blackwell advanced packaging,
chiplet/3D GPU packaging discussion, GPU memory compression research, Google
TPU-style accelerator architectures, and AI cluster networking trends.

Use real KIPRIS/Tavily API data for production-grade claims.

## Quick Renderer Check

To render only the actor map without running an LLM:

```bash
PYTHONPATH=/workspace/tech_analysis_agent python - <<'PY'
import json
from tools.patent_map_renderer import render_patent_maps

maps = json.load(open('/workspace/tech_analysis_agent/example_data/nvidia_actor_similarity_map.json'))
print(render_patent_maps(maps, '/workspace/orchestration_agent/outputs/graphs', 'nvidia_example'))
PY
```
