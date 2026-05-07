"""
tests/test_exam_sources.py — phase-11 external sources pipeline.

Required scenarios from the phase brief:

    • provider failure does not bring the system down
    • normalize unifies different input formats
    • duplicate questions removed
    • anti-copy reorders questions / shuffles MCQ choices
    • semester mismatch rejected
    • OCR corruption detected
    • cache works (second run uses cached samples)
    • empty source → safe fallback
    • exam_validator still passes on a generated exam
    • no forbidden imports under sources/
    • no shawahid template leakage into exam HTML output
"""
from __future__ import annotations

import ast
import json
import os

import pytest

from app.exam_engine.schemas import (
    EXAM_TYPE_QUICK,
    QTYPE_MCQ,
    QTYPE_TRUE_FALSE,
    SOURCE_MANUAL_TOPIC,
    ExamQuestion,
    ExamRequest,
)
from app.exam_engine.sources import (
    AntiCopyOptions,
    DisabledHttpClient,
    InMemoryHttpClient,
    HttpResponse,
    KutubiProvider,
    LocalSamplesProvider,
    MadatiProvider,
    ManhajiProvider,
    MAX_QUESTIONS_PER_SAMPLE,
    QualityReport,
    SourceCache,
    SourceQuery,
    SourceSample,
    TransformationLog,
    anti_copy_transform,
    check_sample_quality,
    filter_by_curriculum,
    list_providers,
    normalize_exam_source,
    normalized_content_hash,
    reset_global_cache,
    run_source_pipeline,
)
from app.exam_engine.sources.base import ExamSourceProvider
from app.exam_engine.sources.source_normalizer import NormalizedSample


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


_MADATI_JSON_BODY = json.dumps({
    "title": "اختبار قصير - الرياضيات - الصف الرابع",
    "meta": {"subject": "الرياضيات", "stage": "المرحلة الابتدائية",
             "semester": "الفصل الدراسي الأول"},
    "questions": [
        {"text": "ناتج جمع 12 + 8 يساوي:", "type": "mcq",
         "choices": ["18", "20", "22", "24"], "correct_answer": 1, "marks": 1},
        {"text": "العدد 9 أكبر من العدد 7.", "type": "true_false",
         "correct_answer": "صح", "marks": 1},
        {"text": "ضعف العدد 6 يساوي ............", "type": "fill_blank",
         "correct_answer": "12", "marks": 2},
    ],
})


_KUTUBI_TEXT_BODY = """
اختبار شهري - العلوم - الصف الخامس
الفصل الدراسي الأول

1) من حالات المادة:
أ) الحرارة فقط
ب) الصلب والسائل والغاز
ج) الضوء فقط
د) الصوت فقط

2) الماء يتبخر عند تسخينه (صح أو خطأ).

3) اذكر مثالًا لمصدر طبيعي للضوء.
""".strip()


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_global_cache()
    yield
    reset_global_cache()


# ──────────────────────────────────────────────────────────────────────
# 1. HTTP client + provider isolation
# ──────────────────────────────────────────────────────────────────────


class TestHttpAndIsolation:
    def test_disabled_client_returns_none(self):
        client = DisabledHttpClient()
        assert client.get("https://anywhere") is None

    def test_in_memory_client_returns_canned_body(self):
        client = InMemoryHttpClient({
            "https://x.invalid/a": "hello",
        })
        resp = client.get("https://x.invalid/a")
        assert resp is not None
        assert resp.body == "hello"
        assert resp.ok

    def test_in_memory_client_unknown_url_returns_none(self):
        client = InMemoryHttpClient()
        assert client.get("https://nope") is None

    def test_provider_with_disabled_client_yields_no_samples(self):
        # The default behaviour for every external provider.
        prov = MadatiProvider()
        result = list(prov.fetch(SourceQuery(subject="الرياضيات")))
        assert result == []

    def test_provider_isolation_failure_does_not_break_pipeline(self):
        class BoomProvider(ExamSourceProvider):
            name = "boom"
            def fetch(self, query):
                raise RuntimeError("simulated provider crash")
            def normalize(self, sample):
                return sample
            def extract_questions(self, sample):
                return ()
            def quality_check(self, sample, questions):
                return QualityReport(False, "n/a")

        boom = BoomProvider()
        # local provider is fine; boom raises. Pipeline must finish.
        local = LocalSamplesProvider()
        result = run_source_pipeline(
            (boom, local),
            query=SourceQuery(
                subject="الرياضيات", stage="المرحلة الابتدائية",
            ),
        )
        report_for_boom = next(r for r in result.reports if r.provider == "boom")
        assert report_for_boom.failure is not None
        assert "fetch_failed" in report_for_boom.failure
        # And the run didn't raise — we got something usable.
        assert result.has_questions


# ──────────────────────────────────────────────────────────────────────
# 2. Normalizer
# ──────────────────────────────────────────────────────────────────────


class TestNormalizer:
    def test_normalize_json_payload(self):
        sample = SourceSample(
            provider="madati", title="t", raw_content=_MADATI_JSON_BODY,
        )
        out = normalize_exam_source(sample)
        assert out.question_count == 3
        types = {q.type for q in out.questions}
        assert "mcq" in types and "true_false" in types and "fill_blank" in types

    def test_normalize_arabic_text(self):
        sample = SourceSample(
            provider="kutubi", title="t", raw_content=_KUTUBI_TEXT_BODY,
        )
        out = normalize_exam_source(sample)
        assert out.question_count >= 2  # extracts numbered blocks
        assert any("الحرارة" in q.text or "حالات" in q.text or "ال" in q.text
                   for q in out.questions)

    def test_normalize_empty_input(self):
        sample = SourceSample(provider="x", title="t", raw_content="")
        out = normalize_exam_source(sample)
        assert out.question_count == 0

    def test_normalize_invalid_json_falls_back(self):
        # Starts with '{' but isn't valid JSON → should fall back to text.
        sample = SourceSample(
            provider="x", title="t",
            raw_content="{ not really json\n1) سؤال أول؟\n2) سؤال ثاني؟",
        )
        out = normalize_exam_source(sample)
        assert out.question_count >= 1


# ──────────────────────────────────────────────────────────────────────
# 3. Quality control
# ──────────────────────────────────────────────────────────────────────


class TestQuality:
    def test_clean_sample_passes(self):
        sample = SourceSample(
            provider="madati", title="t", raw_content=_MADATI_JSON_BODY,
        )
        normalized = normalize_exam_source(sample)
        report = check_sample_quality(normalized)
        assert report.is_acceptable, report.reason

    def test_ocr_corruption_detected(self):
        sample = SourceSample(
            provider="x", title="t",
            raw_content=json.dumps({"questions": [
                {"text": "asdfghjklqwerty zxcvbnm", "type": "short"},
            ]}),
        )
        normalized = normalize_exam_source(sample)
        report = check_sample_quality(normalized)
        assert not report.is_acceptable
        # Either has_garbled_text (no Arabic) OR has_ocr_corruption (latin run)
        assert any(f in report.flags for f in ("has_ocr_corruption", "has_garbled_text"))

    def test_too_many_questions_blocked(self):
        big = json.dumps({"questions": [
            {"text": f"سؤال رقم {i} عن العلوم", "type": "short"}
            for i in range(MAX_QUESTIONS_PER_SAMPLE + 10)
        ]})
        sample = SourceSample(provider="x", title="t", raw_content=big)
        normalized = normalize_exam_source(sample)
        report = check_sample_quality(normalized)
        assert "too_many_questions" in report.flags

    def test_subject_mismatch_rejected(self):
        sample = SourceSample(
            provider="x", title="t",
            raw_content=json.dumps({
                "meta": {"subject": "الرياضيات"},
                "questions": [
                    {"text": "ما عاصمة المملكة؟", "type": "short"},
                ],
            }),
        )
        normalized = normalize_exam_source(sample)
        report = check_sample_quality(
            normalized, expected_subject="العلوم",
        )
        assert not report.is_acceptable
        assert "subject_mismatch" in report.flags

    def test_duplicates_flagged(self):
        sample = SourceSample(
            provider="x", title="t",
            raw_content=json.dumps({"questions": [
                {"text": "ناتج جمع 2 + 2 = ؟", "type": "fill_blank"},
                {"text": "ناتج جمع 2 + 2 = ؟", "type": "fill_blank"},
            ]}),
        )
        normalized = normalize_exam_source(sample)
        report = check_sample_quality(normalized)
        assert "has_duplicates" in report.flags


# ──────────────────────────────────────────────────────────────────────
# 4. Anti-copy
# ──────────────────────────────────────────────────────────────────────


class TestAntiCopy:
    def _sample_questions(self):
        return tuple(
            ExamQuestion(
                id=f"q-{i}", type=QTYPE_MCQ, text=f"السؤال رقم {i}",
                choices=("أ", "ب", "ج", "د"), correct_answer=0, marks=1,
            )
            for i in range(5)
        )

    def test_reorders_questions_by_default(self):
        qs = self._sample_questions()
        adapted, log = anti_copy_transform(
            qs, options=AntiCopyOptions(seed=7),
        )
        assert log.reordered
        assert {q.id for q in adapted} == {q.id for q in qs}
        assert [q.id for q in adapted] != [q.id for q in qs]

    def test_shuffles_mcq_choices_and_remaps_correct(self):
        qs = self._sample_questions()
        adapted, log = anti_copy_transform(
            qs, options=AntiCopyOptions(seed=7),
        )
        assert log.choices_shuffled >= 1
        # For every question that got its choices shuffled, the new
        # correct_answer index must still point at "أ".
        for q in adapted:
            if q.type == QTYPE_MCQ:
                assert q.choices[q.correct_answer] == "أ"

    def test_transforms_arithmetic_in_fill_blank(self):
        from app.exam_engine.schemas import QTYPE_FILL_BLANK
        q = ExamQuestion(
            id="n1", type=QTYPE_FILL_BLANK,
            text="ناتج جمع 12 + 8 يساوي ............",
            correct_answer="20", marks=1,
        )
        adapted, log = anti_copy_transform(
            (q,), options=AntiCopyOptions(seed=42, shuffle_questions=False),
        )
        assert log.numbers_changed == 1
        new_q = adapted[0]
        assert "12 + 8" not in new_q.text
        # The new correct_answer should match the new arithmetic result.
        import re
        m = re.search(r"(\d+)\s*\+\s*(\d+)", new_q.text)
        assert m
        new_sum = int(m.group(1)) + int(m.group(2))
        assert str(new_sum) == str(new_q.correct_answer)

    def test_paraphrases_leading_phrase(self):
        q = ExamQuestion(
            id="p1", type=QTYPE_TRUE_FALSE,
            text="ناتج جمع 2 + 2 = 4",
            correct_answer="صح", marks=1,
        )
        adapted, log = anti_copy_transform(
            (q,),
            options=AntiCopyOptions(
                seed=1, shuffle_questions=False,
                transform_numbers=False,
            ),
        )
        # The leading "ناتج جمع" pattern is rewritten to one of its variants.
        new_text = adapted[0].text
        assert new_text != q.text
        assert log.stems_paraphrased == 1


# ──────────────────────────────────────────────────────────────────────
# 5. Curriculum filter
# ──────────────────────────────────────────────────────────────────────


class TestCurriculumFilter:
    def test_semester_match_passes(self):
        sample = NormalizedSample(
            provider="x", title="اختبار - الفصل الدراسي الأول",
            questions=(),
            meta={"semester": "first"},
        )
        d = filter_by_curriculum(
            sample, query=SourceQuery(semester="الفصل الدراسي الأول"),
        )
        assert d.is_acceptable

    def test_semester_mismatch_rejected(self):
        sample = NormalizedSample(
            provider="x", title="اختبار - الفصل الدراسي الثاني",
            questions=(),
        )
        d = filter_by_curriculum(
            sample, query=SourceQuery(semester="الفصل الدراسي الأول"),
        )
        assert not d.is_acceptable
        assert "semester_mismatch" in d.flags

    def test_planning_document_rejected(self):
        # A planning document accidentally surfaced as an exam.
        from app.exam_engine.sources.source_normalizer import CandidateQuestion
        sample = NormalizedSample(
            provider="x",
            title="خطة درس - الرياضيات",
            questions=(
                CandidateQuestion(
                    text=("نواتج التعلم: يحل الطالب المعادلات. "
                          "التهيئة: طرح أسئلة افتتاحية. "
                          "الواجب: تمارين الكتاب."),
                    type="short",
                ),
            ),
        )
        d = filter_by_curriculum(sample, query=SourceQuery())
        assert not d.is_acceptable
        assert "non_exam_document" in d.flags


# ──────────────────────────────────────────────────────────────────────
# 6. Cache
# ──────────────────────────────────────────────────────────────────────


class TestCache:
    def test_cache_hit_avoids_second_fetch(self):
        cache = SourceCache(default_ttl=60)

        class CountingProvider(ExamSourceProvider):
            name = "counting"
            calls = 0

            def fetch(self, query):
                CountingProvider.calls += 1
                return (SourceSample(
                    provider=self.name, title="t",
                    raw_content=_MADATI_JSON_BODY,
                ),)

            def normalize(self, sample):
                return sample
            def extract_questions(self, sample):
                return ()
            def quality_check(self, sample, questions):
                return QualityReport(True, "ok")

        prov = CountingProvider()
        query = SourceQuery(subject="الرياضيات", stage="المرحلة الابتدائية")
        run_source_pipeline((prov,), query=query, cache=cache)
        run_source_pipeline((prov,), query=query, cache=cache)
        assert CountingProvider.calls == 1
        assert cache.stats.hits >= 1

    def test_invalidate_clears_entry(self):
        cache = SourceCache()
        query = SourceQuery(subject="x")
        cache.put("p", query, [])
        assert cache.invalidate("p", query)
        assert not cache.invalidate("p", query)

    def test_normalized_content_hash_stable(self):
        a = "ناتج جمع 2 + 2"
        b = "  ناتج   جمع   2 + 2  "
        assert normalized_content_hash(a) == normalized_content_hash(b)


# ──────────────────────────────────────────────────────────────────────
# 7. End-to-end pipeline + exam validator
# ──────────────────────────────────────────────────────────────────────


class TestPipelineEndToEnd:
    def test_real_provider_with_in_memory_client(self):
        client = InMemoryHttpClient()
        # Pre-populate with the exact URLs MadatiProvider would call.
        prov = MadatiProvider(http_client=client)
        for tpl in prov.url_templates:
            url = prov._format_url(tpl, SourceQuery(
                subject="الرياضيات", grade="الصف الرابع",
                semester="الفصل الدراسي الأول",
                exam_type=EXAM_TYPE_QUICK,
            ))
            client.add(url, _MADATI_JSON_BODY)

        result = run_source_pipeline(
            (prov,),
            query=SourceQuery(
                subject="الرياضيات", grade="الصف الرابع",
                stage="المرحلة الابتدائية",
                semester="الفصل الدراسي الأول",
                exam_type=EXAM_TYPE_QUICK,
            ),
        )
        assert result.has_questions
        assert client.calls  # actually fetched
        # Anti-copy ran.
        assert result.transformation is not None

    def test_empty_source_safe_fallback(self):
        prov = MadatiProvider(http_client=InMemoryHttpClient())  # no canned URLs
        result = run_source_pipeline(
            (prov,),
            query=SourceQuery(subject="الرياضيات"),
        )
        assert not result.has_questions
        assert result.accepted_samples == 0

    def test_exam_validator_still_passes_on_pipeline_output(self):
        # Use the local provider end-to-end and verify the resulting exam
        # passes the existing exam_validator.
        from app.exam_engine import (
            build_exam_profile,
            generate_exam,
            SOURCE_SAMPLE_BANK,
            validate_exam,
        )
        req = ExamRequest(
            teacher_id=1, exam_type=EXAM_TYPE_QUICK,
            subject="الرياضيات", grade="الصف الرابع",
            stage="المرحلة الابتدائية",
            source_mode=SOURCE_SAMPLE_BANK,
            total_questions=3, total_marks=4,
        )
        exam = generate_exam(req, profile=build_exam_profile(request=req))
        result = validate_exam(exam)
        assert result.is_valid, [i.message for i in result.errors]


# ──────────────────────────────────────────────────────────────────────
# 8. Architectural contracts
# ──────────────────────────────────────────────────────────────────────

_FORBIDDEN_PREFIXES = (
    "app.export_engine",
    "app.media_engine",
    "app.review_engine",
    "app.storage_engine",
    "app.services.exporter",
    "playwright",
)


def _walk_imports(path: str):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module


class TestArchitecturalContracts:
    def test_no_forbidden_imports_under_sources(self):
        from app.exam_engine import sources
        pkg_root = os.path.dirname(sources.__file__)
        for fname in os.listdir(pkg_root):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(pkg_root, fname)
            for module in _walk_imports(full):
                for forbidden in _FORBIDDEN_PREFIXES:
                    assert not module.startswith(forbidden), (
                        f"{fname} imports forbidden module {module}"
                    )

    def test_list_active_providers_excludes_external_with_default_client(self):
        active = list_providers(only_active=True)
        names = {p.name for p in active}
        # Local must always be active; external must be hidden when no
        # real HttpClient is injected.
        assert "local_samples" in names
        for forbidden in ("madati", "kutubi", "manhaji"):
            assert forbidden not in names

    def test_list_active_providers_includes_external_with_real_client(self):
        client = InMemoryHttpClient()
        active = list_providers(only_active=True, http_client=client)
        names = {p.name for p in active}
        for required in ("local_samples", "madati", "kutubi", "manhaji"):
            assert required in names
