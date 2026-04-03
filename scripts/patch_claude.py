#!/usr/bin/env python3
"""
Patch Claude Code binary — extract JS bundle, apply patches, write back.

Usage:
    python scripts/patch_claude.py runtime/bin/claude-2.1.87 runtime/bin/claude-patched

The script:
1. Parses Mach-O headers to locate the __bun section
2. Extracts the JS bundle from within it
3. Applies patches defined in anchors.json
4. Writes a new binary with the patched JS bundle
"""

import json
import platform
import struct
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Mach-O / Bun parsing
# ---------------------------------------------------------------------------

def find_bun_section(data: bytes) -> tuple[int, int, int]:
    """Find __bun section in Mach-O binary.

    Returns (section_offset, section_size, js_offset_within_section).
    """
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != 0xFEEDFACF:
        raise ValueError(f"Not a 64-bit Mach-O binary (magic: 0x{magic:08x})")

    ncmds = struct.unpack_from("<I", data, 16)[0]
    offset = 32  # skip Mach-O header

    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, offset)
        if cmd == 0x19:  # LC_SEGMENT_64
            segname = data[offset+8:offset+24].split(b'\x00')[0].decode('ascii')
            if segname == "__BUN":
                nsects = struct.unpack_from("<I", data, offset+64)[0]
                sect_offset_pos = offset + 72
                for _ in range(nsects):
                    sectname = data[sect_offset_pos:sect_offset_pos+16].split(b'\x00')[0].decode('ascii')
                    if sectname == "__bun":
                        s_size = struct.unpack_from("<Q", data, sect_offset_pos+40)[0]
                        s_offset = struct.unpack_from("<I", data, sect_offset_pos+48)[0]

                        # Find JS start within section (skip Bun metadata header)
                        bun_data = data[s_offset:s_offset+s_size]
                        js_start = bun_data.find(b"var ")
                        if js_start < 0:
                            raise ValueError("Could not find JS bundle start in __bun section")

                        return s_offset, s_size, js_start
                    sect_offset_pos += 80
        offset += cmdsize

    raise ValueError("__bun section not found in binary")


def extract_js(data: bytes, section_offset: int, section_size: int, js_offset: int) -> bytes:
    """Extract JS bundle from __bun section."""
    start = section_offset + js_offset
    end = section_offset + section_size
    return data[start:end]


# ---------------------------------------------------------------------------
# Patching
# ---------------------------------------------------------------------------

def load_anchors(anchors_path: Path) -> list[dict]:
    """Load patch definitions from anchors.json."""
    with open(anchors_path) as f:
        return json.load(f)["patches"]


def apply_patches(js: bytes, patches: list[dict]) -> tuple[bytes, list[str]]:
    """Apply patches to JS bundle. Returns (patched_js, list of applied patch names).

    Each patch dict has:
        - name: human-readable identifier
        - anchor: byte string to find (exact match)
        - replacement: byte string to replace with
        - required: if True, fail if anchor not found
    """
    applied = []
    failed = []

    for patch in patches:
        name = patch["name"]
        anchor = patch["anchor"].encode("utf-8")
        replacement = patch["replacement"].encode("utf-8")
        required = patch.get("required", True)

        count = js.count(anchor)
        if count == 0:
            if required:
                failed.append(f"REQUIRED anchor not found: {name}")
            else:
                print(f"  SKIP (not found): {name}")
            continue

        if count > 1 and not patch.get("replace_all", False):
            failed.append(f"Ambiguous anchor ({count} occurrences): {name}")
            continue

        if patch.get("replace_all", False):
            js = js.replace(anchor, replacement)
            applied.append(f"{name} ({count} occurrences)")
        else:
            js = js.replace(anchor, replacement, 1)
            applied.append(name)

        print(f"  APPLIED: {name}")

    if failed:
        print("\nFailed patches:")
        for f_msg in failed:
            print(f"  ERROR: {f_msg}")
        raise RuntimeError(f"{len(failed)} patch(es) failed")

    return js, applied


# ---------------------------------------------------------------------------
# Binary reconstruction
# ---------------------------------------------------------------------------

def rebuild_binary(
    original: bytes,
    patched_js: bytes,
    section_offset: int,
    section_size: int,
    js_offset: int,
) -> bytes:
    """Rebuild binary with patched JS bundle.

    Strategy: replace JS content in-place within the __bun section.
    If patched JS is same size or smaller, pad with whitespace.
    If larger, we have a problem (Mach-O section sizes are in headers).
    """
    original_js_size = section_size - js_offset
    new_js_size = len(patched_js)

    if new_js_size > original_js_size:
        raise ValueError(
            f"Patched JS ({new_js_size} bytes) is larger than original ({original_js_size} bytes). "
            f"Patches must not increase total JS size. Reduce replacement text or use shorter strings."
        )

    # Pad with spaces to maintain exact section size
    padding = original_js_size - new_js_size
    if padding > 0:
        # Use spaces for padding — JS-safe and won't affect execution
        patched_js = patched_js + b" " * padding
        print(f"  Padded {padding} bytes to maintain section size")

    # Reconstruct: [before __bun JS] [patched JS] [after __bun section]
    js_abs_offset = section_offset + js_offset
    result = (
        original[:js_abs_offset]
        + patched_js
        + original[section_offset + section_size:]
    )

    assert len(result) == len(original), \
        f"Binary size mismatch: {len(result)} != {len(original)}"

    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <input-binary> <output-binary> [--anchors anchors.json]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    # Find anchors file
    anchors_path = Path(__file__).parent / "anchors.json"
    for i, arg in enumerate(sys.argv):
        if arg == "--anchors" and i + 1 < len(sys.argv):
            anchors_path = Path(sys.argv[i + 1])

    print(f"Input:   {input_path}")
    print(f"Output:  {output_path}")
    print(f"Anchors: {anchors_path}")
    print()

    # Read binary
    data = input_path.read_bytes()
    print(f"Binary size: {len(data)} bytes ({len(data)/1024/1024:.1f} MB)")

    # Find __bun section
    section_offset, section_size, js_offset = find_bun_section(data)
    print(f"__bun section: offset={section_offset}, size={section_size}")
    print(f"JS starts at: +{js_offset} within section")
    print()

    # Extract JS
    js = extract_js(data, section_offset, section_size, js_offset)
    print(f"JS bundle: {len(js)} bytes ({len(js)/1024/1024:.1f} MB)")
    print()

    # Load and apply patches
    patches = load_anchors(anchors_path)
    print(f"Applying {len(patches)} patch(es):")
    patched_js, applied = apply_patches(js, patches)
    print(f"\n{len(applied)} patch(es) applied successfully")
    print()

    # Rebuild binary
    print("Rebuilding binary...")
    result = rebuild_binary(data, patched_js, section_offset, section_size, js_offset)

    # Write output
    output_path.write_bytes(result)
    output_path.chmod(0o755)
    print(f"Patched binary written to: {output_path}")
    print(f"Output size: {len(result)} bytes (should match input: {len(data)} bytes)")

    # Re-sign on macOS (Apple Silicon requires valid code signature)
    #
    # Bun single-file executables locate their JS bundle using offsets that
    # depend on the total file size. codesign with ad-hoc signature produces
    # a smaller __LINKEDIT than the original Developer ID signature, changing
    # the file size and breaking Bun's loader.
    #
    # Solution: binary-search for a --signature-size value that produces
    # exactly the same file size as the original.
    if platform.system() == "Darwin":
        original_size = len(result)
        _resign_matching_size(output_path, original_size)


def _resign_matching_size(binary_path: Path, target_size: int) -> None:
    """Ad-hoc codesign a binary, tuning --signature-size to match target file size."""
    import shutil
    import tempfile

    print(f"\nRe-signing binary (ad-hoc, target size: {target_size})...")

    def try_sign(sig_size: int) -> int:
        tmp = Path(tempfile.mktemp(suffix=".bin"))
        shutil.copy2(binary_path, tmp)
        subprocess.run(
            ["codesign", "--force", "--sign", "-", "--signature-size", str(sig_size), str(tmp)],
            check=True, capture_output=True,
        )
        result_size = tmp.stat().st_size
        tmp.unlink()
        return result_size

    # Binary search for the right --signature-size
    lo, hi = 0, 2_000_000
    best_size = None
    best_diff = float("inf")

    for _ in range(30):  # converges in ~20 iterations
        mid = (lo + hi) // 2
        actual = try_sign(mid)
        diff = actual - target_size

        if abs(diff) < abs(best_diff):
            best_diff = diff
            best_size = mid

        if diff == 0:
            break
        elif diff < 0:
            lo = mid + 1
        else:
            hi = mid - 1

    if best_diff != 0:
        raise RuntimeError(
            f"Could not find signature-size that matches target. "
            f"Best: --signature-size {best_size} (diff: {best_diff})"
        )

    # Apply the winning signature size
    subprocess.run(
        ["codesign", "--force", "--sign", "-", "--signature-size", str(best_size), str(binary_path)],
        check=True,
    )
    final_size = binary_path.stat().st_size
    assert final_size == target_size, f"Size mismatch after signing: {final_size} != {target_size}"
    print(f"Code signature applied (--signature-size {best_size}, file size: {final_size})")


if __name__ == "__main__":
    main()
