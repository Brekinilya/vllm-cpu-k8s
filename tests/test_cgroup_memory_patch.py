import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def patched_module():
    if not importlib.util.find_spec("vllm"):
        return None
    from vllm.utils import cpu_resource_utils

    return cpu_resource_utils if hasattr(cpu_resource_utils, "_read_reclaimable_page_cache") else None


module = patched_module()


@unittest.skipUnless(module, "patched vllm is not installed")
class CgroupMemoryPatchTest(unittest.TestCase):
    def write_stat(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".stat", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_reclaimable_cache_excludes_shared_memory(self):
        path = self.write_stat("anon 5000\nfile 3000\nshmem 500\ninactive_file 1000\n")
        self.assertEqual(module._read_reclaimable_page_cache(path, "file", "shmem"), 2500)

    def test_missing_stat_file_counts_nothing_as_reclaimable(self):
        self.assertEqual(module._read_reclaimable_page_cache("/nonexistent/memory.stat", "file", "shmem"), 0)

    def test_usage_does_not_count_page_cache_as_used(self):
        values = {"/sys/fs/cgroup/memory.max": 8 << 30, "/sys/fs/cgroup/memory.current": 7 << 30}
        with (
            mock.patch.object(module, "_read_int_file", side_effect=values.get),
            mock.patch.object(module, "_read_reclaimable_page_cache", return_value=3 << 30),
        ):
            self.assertEqual(module.get_cgroup_memory_usage(), 4 << 30)


if __name__ == "__main__":
    unittest.main()
