from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
def test_catalog_references_include_current_metadata_gates():
    ddae = (
        ROOT
        / "dell-data-analytics-engine/skills/configure-dell-data-analytics-engine/references/validated-tool-catalog.md"
    ).read_text(encoding="utf-8")
    assert "OpenAI metadata audit — August 12, 2026" in ddae
    assert "does not mark it required" in ddae
    assert "without reading customer data" in ddae

    ddse = (
        ROOT
        / "dell-data-search-engine/skills/configure-dell-data-search-engine/references/validated-tool-catalog.md"
    ).read_text(encoding="utf-8")
    assert "OpenAI metadata audit — August 12, 2026" in ddse
    assert "None exposes an output schema" in ddse
    assert "That textual observation does not replace required server annotations" in ddse
