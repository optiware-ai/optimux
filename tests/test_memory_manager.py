import os
import tempfile
import pytest
from optimux_backend.core import memory_manager

def test_init_memory(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(memory_manager, "GLOBAL_DIR", tmpdir)
        monkeypatch.setattr(memory_manager, "PROJECTS_DIR", os.path.join(tmpdir, "projects"))
        monkeypatch.setattr(memory_manager, "PERSONAL_MD", os.path.join(tmpdir, "personal.md"))
        monkeypatch.setattr(memory_manager, "PROFESSIONAL_MD", os.path.join(tmpdir, "professional.md"))
        
        memory_manager.init_memory()
        
        assert os.path.exists(os.path.join(tmpdir, "projects"))
        assert os.path.exists(os.path.join(tmpdir, "personal.md"))
        assert os.path.exists(os.path.join(tmpdir, "professional.md"))
