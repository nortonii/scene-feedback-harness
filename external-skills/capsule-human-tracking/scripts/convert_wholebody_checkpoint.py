#!/usr/bin/env python3
"""Convert an official ViTPose++ Base multi-task .pth to a local HF133 head.

This copies the *bitwise matching* public Hugging Face 17-joint backbone and
replaces the entire decoder with the official WholeBody associate head 4. Both
inputs must already exist locally; this script performs no downloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

from wholebody_profile import WHOLEBODY133_NAMES, WHOLEBODY133_EDGES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _official_tensor(hf_key: str, state: dict):
    if hf_key == "backbone.embeddings.position_embeddings":
        return state["backbone.pos_embed"]
    if hf_key.startswith("backbone.embeddings.patch_embeddings.projection."):
        suffix = hf_key.rsplit(".", 1)[-1]
        return state[f"backbone.patch_embed.proj.{suffix}"]
    if hf_key.startswith("backbone.layernorm."):
        # The HF port has a final layernorm absent from the official training
        # checkpoint. It is not trained in the source and is not replaced.
        return None
    match = re.fullmatch(r"backbone\.encoder\.layer\.(\d+)\.(.*)", hf_key)
    if match:
        layer, rest = match.groups()
        prefix = f"backbone.blocks.{layer}."
        for source, target in (("layernorm_before.", "norm1."),
                               ("layernorm_after.", "norm2."),
                               ("attention.output.dense.", "attn.proj."),
                               ("mlp.", "mlp.")):
            if rest.startswith(source):
                return state[prefix + target + rest[len(source):]]
        attention = re.fullmatch(r"attention\.attention\.(query|key|value)\.(weight|bias)", rest)
        if attention:
            component, suffix = attention.groups()
            return state[prefix + "attn.qkv." + suffix].chunk(3, dim=0)[{"query": 0, "key": 1, "value": 2}[component]]
    if hf_key.startswith("head."):
        rest = hf_key[5:]
        mapping = (("deconv1.", "deconv_layers.0."),
                   ("batchnorm1.", "deconv_layers.1."),
                   ("deconv2.", "deconv_layers.3."),
                   ("batchnorm2.", "deconv_layers.4."),
                   ("conv.", "final_layer."))
        for target, source in mapping:
            if rest.startswith(target):
                return state["keypoint_head." + source + rest[len(target):]]
    raise ValueError("unmapped HF tensor: " + hf_key)


def _wholebody_head_tensor(hf_key: str, state: dict):
    rest = hf_key[5:]
    for target, source in (("deconv1.", "deconv_layers.0."),
                           ("batchnorm1.", "deconv_layers.1."),
                           ("deconv2.", "deconv_layers.3."),
                           ("batchnorm2.", "deconv_layers.4."),
                           ("conv.", "final_layer.")):
        if rest.startswith(target):
            return state["associate_keypoint_heads.4." + source + rest[len(target):]]
    raise ValueError("unmapped WholeBody head tensor: " + hf_key)


def convert(source: Path, base_hf: Path, output: Path) -> dict:
    import torch
    from safetensors.torch import load_file, save_file

    source = source.expanduser().resolve(strict=True)
    base_hf = base_hf.expanduser().resolve(strict=True)
    output = output.expanduser().resolve()
    if output.exists():
        raise ValueError("output model directory already exists")
    for name in ("model.safetensors", "config.json", "preprocessor_config.json"):
        if not (base_hf / name).is_file():
            raise ValueError("base HF model is missing " + name)
    original = torch.load(source, map_location="cpu", weights_only=True)
    if not isinstance(original, dict) or not isinstance(original.get("state_dict"), dict):
        raise ValueError("official checkpoint needs a state_dict")
    state = original["state_dict"]
    head = state.get("associate_keypoint_heads.4.final_layer.weight")
    if head is None or tuple(head.shape) != (133, 256, 1, 1):
        raise ValueError("official checkpoint has no trained 133-channel WholeBody head 4")
    hf = load_file(str(base_hf / "model.safetensors"), device="cpu")
    matched = 0
    skipped = []
    for key, tensor in hf.items():
        source_tensor = _official_tensor(key, state)
        if source_tensor is None:
            skipped.append(key)
            continue
        if not torch.equal(tensor, source_tensor):
            raise ValueError("HF Base does not bitwise match this official checkpoint: " + key)
        matched += 1
    if matched < 350 or sorted(skipped) != ["backbone.layernorm.bias", "backbone.layernorm.weight"]:
        raise ValueError("official/HF weight comparison coverage is incomplete")
    output_state = dict(hf)
    replaced = []
    for key, old in hf.items():
        if not key.startswith("head."):
            continue
        replacement = _wholebody_head_tensor(key, state)
        if key.startswith("head.conv."):
            expected = (133, 256, 1, 1) if key.endswith("weight") else (133,)
            if tuple(replacement.shape) != expected:
                raise ValueError("WholeBody final head has incorrect channel shape")
        elif replacement.shape != old.shape:
            raise ValueError("WholeBody decoder tensor differs in shape: " + key)
        output_state[key] = replacement.contiguous()
        replaced.append(key)
    if len(replaced) != 14:
        raise ValueError("WholeBody decoder must replace all 14 HF head tensors")
    config = json.loads((base_hf / "config.json").read_text(encoding="utf-8"))
    if config.get("backbone_config", {}).get("num_experts") != 6:
        raise ValueError("base HF model does not declare six ViTPose++ experts")
    config["id2label"] = {str(index): name for index, name in enumerate(WHOLEBODY133_NAMES)}
    config["label2id"] = {name: index for index, name in enumerate(WHOLEBODY133_NAMES)}
    config["edges"] = [list(edge) for edge in WHOLEBODY133_EDGES]
    config["num_labels"] = 133
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix="vitpose-wholebody-", dir=output.parent))
    try:
        save_file(output_state, str(staged / "model.safetensors"))
        (staged / "config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        shutil.copy2(base_hf / "preprocessor_config.json", staged / "preprocessor_config.json")
        report = {"source_checkpoint": str(source), "source_sha256": _sha256(source),
                  "base_hf": str(base_hf), "base_hf_sha256": _sha256(base_hf / "model.safetensors"),
                  "matched_source_tensors": matched, "replaced_wholebody_head_tensors": len(replaced),
                  "wholebody_joints": 133,
                  "source": "official ViTPose++ Base multi-task associate_keypoint_heads.4"}
        (staged / "conversion.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        staged.rename(output)
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-checkpoint", type=Path, required=True)
    parser.add_argument("--base-hf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(convert(args.official_checkpoint, args.base_hf, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()
