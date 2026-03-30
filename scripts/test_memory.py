"""
Quick validation that memory store read/write works.

Usage:
    python scripts/test_memory.py
"""

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nekobot.memory.store import MemoryStore
from nekobot.memory.extractor import extract_memory_writes
from nekobot.memory.search import search_archive

test_dir = Path("/tmp/nekobot_test_memory")

# Clean slate
if test_dir.exists():
    shutil.rmtree(test_dir)

store = MemoryStore(test_dir)

# Test write
store.write_fact("profile", "name", "test_user")
store.write_fact("project", "nekobot", "testing memory layer")
store.write_fact("learning", "python_asyncio", "asyncio.Queue is useful for message passing")

print("=== Core ===")
print(store.render_core())
print("\n=== Active ===")
print(store.render_active())

# Test extractor
response = """Here's my analysis.

<memory_write>
- profile.editor: VS Code
- project.nekobot: memory layer works
</memory_write>

Let me know if you need more details."""

cleaned, facts = extract_memory_writes(response)
print(f"\n=== Extractor ===")
print(f"Cleaned: {cleaned}")
print(f"Facts: {facts}")

# Write extracted facts
store.write_facts(facts)
print(f"\n=== Core after extraction ===")
print(store.render_core())

# Test search
print(f"\n=== Search: 'asyncio' ===")
results = search_archive(store._archive_path, "asyncio")
for r in results:
    print(f"  {r['title']}: {r['snippet'][:60]}...")

# Test journal
store.append_journal("First test session")
store.append_journal("Memory layer validated")
entries = store.load_journal()
print(f"\n=== Journal ({len(entries)} entries) ===")
for e in entries:
    print(f"  [{e['timestamp'][:19]}] {e['summary']}")

# Cleanup
shutil.rmtree(test_dir)
print("\nAll tests passed!")
