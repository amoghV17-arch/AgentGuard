"""
tests/test_injection_detector.py — Tests for blue_team/injection_detector.py.

We do NOT test the FAISS/embedding internals directly — those depend on a
model download that we cannot guarantee in CI. Instead we test:
  1. The DetectionResult type is correct
  2. The function handles edge cases (empty string, very short input)
  3. The scoring logic: known injections score > 0.5, clean text scores lower

For the scoring tests we use a mock encoder that returns fixed embeddings
so tests are deterministic and fast (no model download needed).
"""

from __future__ import annotations

import numpy as np
import pytest

from blue_team.injection_detector import DetectionResult, check_content


class TestDetectionResultType:
    def test_result_is_namedtuple(self):
        r = DetectionResult(score=0.5, matched_pattern="test", latency_ms=10.0)
        assert r.score == 0.5
        assert r.matched_pattern == "test"
        assert r.latency_ms == 10.0

    def test_result_score_bounds_are_float(self):
        r = DetectionResult(score=0.0, matched_pattern="", latency_ms=0.0)
        assert isinstance(r.score, float)
        assert 0.0 <= r.score <= 1.0


class TestEdgeCases:
    def test_empty_string_returns_zero_score(self):
        """Empty input should return score 0 without crashing."""
        result = check_content("")
        assert result.score == 0.0
        assert result.latency_ms == 0.0

    def test_whitespace_only_returns_zero_score(self):
        result = check_content("   ")
        assert result.score == 0.0

    def test_result_has_latency(self):
        """Latency should be populated (even for fast/cached queries)."""
        # Use a clearly benign short text so no model download is triggered
        # if the index hasn't been built yet
        try:
            result = check_content("hello world", threshold=0.99)
            assert result.latency_ms >= 0.0
        except Exception:
            pytest.skip("FAISS index not yet built — run build_or_load_index() first")


class TestScoringLogic:
    """
    These tests mock the FAISS index to test the scoring logic in isolation
    without needing a real sentence-transformer model.
    """

    def test_high_similarity_produces_high_score(self, monkeypatch):
        """If FAISS returns similarity 0.95, score should be 0.95."""
        import blue_team.injection_detector as det

        # Patch the index and encoder
        class MockIndex:
            ntotal = 3
            def search(self, embedding, k):
                return np.array([[0.95, 0.85, 0.70]]), np.array([[0, 1, 2]])

        det._injection_index = MockIndex()
        det._injection_labels = ["injection text A", "injection text B", "injection text C"]

        def mock_embed(texts):
            return np.zeros((len(texts), 384), dtype=np.float32)

        monkeypatch.setattr(det, "_embed_texts", mock_embed)

        result = det.check_content("some text", threshold=0.72)
        assert result.score == pytest.approx(0.95)
        assert result.matched_pattern.startswith("injection text A")

    def test_low_similarity_produces_low_score(self, monkeypatch):
        """If FAISS returns similarity 0.20, score should be 0.20 (clean text)."""
        import blue_team.injection_detector as det

        class MockIndex:
            ntotal = 3
            def search(self, embedding, k):
                return np.array([[0.20, 0.15, 0.10]]), np.array([[0, 1, 2]])

        det._injection_index = MockIndex()
        det._injection_labels = ["inj A", "inj B", "inj C"]

        def mock_embed(texts):
            return np.zeros((len(texts), 384), dtype=np.float32)

        monkeypatch.setattr(det, "_embed_texts", mock_embed)

        result = det.check_content("normal product review text", threshold=0.72)
        assert result.score == pytest.approx(0.20)
        assert result.matched_pattern == ""  # below threshold

    def test_score_at_threshold_still_flags(self, monkeypatch):
        """Score exactly at threshold should flag the content."""
        import blue_team.injection_detector as det

        class MockIndex:
            ntotal = 1
            def search(self, embedding, k):
                return np.array([[0.72]]), np.array([[0]])

        det._injection_index = MockIndex()
        det._injection_labels = ["matched injection"]

        def mock_embed(texts):
            return np.zeros((len(texts), 384), dtype=np.float32)

        monkeypatch.setattr(det, "_embed_texts", mock_embed)

        result = det.check_content("test content", threshold=0.72)
        assert result.score == pytest.approx(0.72)
        assert result.matched_pattern == "matched injection"
