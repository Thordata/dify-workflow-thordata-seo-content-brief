"""Build the Dify Creator Center application package (.ifpkg) from the DSL.

The package is the version 2 container Dify exports for an app: a ZIP archive
with a ``manifest.yaml`` that records the DSL size and SHA-256, plus the DSL
itself as ``app.yaml``. Creator Center accepts this file directly (10 MB max).
"""

import argparse
import hashlib
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
DSL = ROOT / "thordata-seo-content-brief.yml"
DEFAULT_OUT = ROOT / "submission" / "thordata-seo-content-brief.ifpkg"


def build(dsl_path: Path, out_path: Path) -> Path:
    payload = dsl_path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    manifest = {
        "format": "dify.app",
        "format_version": 2,
        "apps": [{"path": "app.yaml", "size": len(payload), "sha256": digest}],
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr(
            "manifest.yaml", yaml.safe_dump(manifest, sort_keys=False).encode("utf-8")
        )
        package.writestr("app.yaml", payload)

    with zipfile.ZipFile(out_path) as package:
        read_manifest = yaml.safe_load(package.read("manifest.yaml"))
        app = read_manifest["apps"][0]
        assert app["sha256"] == digest and app["size"] == len(payload)
        assert package.read("app.yaml") == payload
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsl", type=Path, default=DSL)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    out = build(args.dsl, args.out)
    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
