"""Tests for training core-field registry and mention-bound logic (PDC.7)."""

from datetime import datetime, timezone

from kura_workers.training_core_fields import (
    core_field_registry,
    evaluate_set_context_rows,
    extract_set_context_mentions,
)


def _row(
    event_id: str,
    *,
    data: dict,
    session_id: str = "s1",
    timestamp: str = "2026-02-11T10:00:00+00:00",
) -> dict:
    return {
        "id": event_id,
        "timestamp": datetime.fromisoformat(timestamp),
        "data": data,
        "metadata": {"session_id": session_id},
    }


def test_core_field_registry_contains_modality_classes():
    registry = core_field_registry()
    assert "strength" in registry
    assert "mention_bound" in registry["strength"]
    assert "rest_seconds" in registry["strength"]["mention_bound"]


def test_extract_set_context_mentions_is_deterministic():
    text = "Pause 90 sec, Tempo 3-1-1-0, RIR 2, warmup"
    mentions = extract_set_context_mentions(text)
    assert mentions["rest_seconds"] == 90.0
    assert mentions["tempo"] == "3-1-1-0"
    assert mentions["rir"] == 2.0
    assert mentions["set_type"] == "warmup"


def test_extract_set_context_mentions_supports_mmss_and_minutes():
    mmss = extract_set_context_mentions("rest 1:30 before next set")
    assert mmss["rest_seconds"] == 90.0

    minutes = extract_set_context_mentions("pause 2 min")
    assert minutes["rest_seconds"] == 120.0


def test_session_defaults_apply_until_override_within_session_exercise_scope():
    rows = [
        _row(
            "e1",
            data={
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "notes": "pause 90 sec",
            },
        ),
        _row(
            "e2",
            data={"exercise_id": "barbell_back_squat", "reps": 5},
            timestamp="2026-02-11T10:02:00+00:00",
        ),
        _row(
            "e3",
            data={
                "exercise_id": "barbell_back_squat",
                "reps": 5,
                "rest_seconds": 120,
            },
            timestamp="2026-02-11T10:04:00+00:00",
        ),
        _row(
            "e4",
            data={"exercise_id": "barbell_bench_press", "reps": 5},
            timestamp="2026-02-11T10:06:00+00:00",
        ),
    ]

    evaluated = evaluate_set_context_rows(rows)
    by_id = {item["event_id"]: item for item in evaluated}

    assert "rest_seconds" in by_id["e1"]["missing_fields"]
    assert "rest_seconds" in by_id["e2"]["missing_fields"]
    assert by_id["e3"]["missing_fields"] == []
    assert by_id["e4"]["missing_fields"] == []


# --- Prime notation ---


def test_double_prime_ascii_seconds():
    """90'' (two apostrophes) = 90 seconds."""
    assert extract_set_context_mentions("rest 90''")["rest_seconds"] == 90.0


def test_double_prime_unicode_seconds():
    """90\u2033 (Unicode double prime) = 90 seconds."""
    assert extract_set_context_mentions("rest 90\u2033")["rest_seconds"] == 90.0


def test_double_quote_seconds():
    """90" (ASCII double quote) = 90 seconds."""
    assert extract_set_context_mentions('rest 90"')["rest_seconds"] == 90.0


def test_single_prime_ascii_minutes():
    """2' (apostrophe) = 2 minutes = 120 seconds."""
    assert extract_set_context_mentions("rest 2'")["rest_seconds"] == 120.0


def test_single_prime_unicode_minutes():
    """2\u2032 (Unicode prime) = 2 minutes."""
    assert extract_set_context_mentions("rest 2\u2032")["rest_seconds"] == 120.0


def test_combined_prime_mmss():
    """1'30'' = 1 min 30 sec = 90 seconds."""
    assert extract_set_context_mentions("rest 1'30''")["rest_seconds"] == 90.0


def test_combined_prime_double_quote():
    """1'30" = 1 min 30 sec = 90 seconds."""
    assert extract_set_context_mentions('rest 1\'30"')["rest_seconds"] == 90.0


def test_combined_prime_unicode():
    """1\u203230\u2033 (Unicode primes) = 90 seconds."""
    assert extract_set_context_mentions("rest 1\u203230\u2033")["rest_seconds"] == 90.0


def test_curly_quotes_normalized():
    """Typographic curly quotes treated as primes."""
    assert extract_set_context_mentions("rest 90\u201C")["rest_seconds"] == 90.0  # left "
    assert extract_set_context_mentions("rest 2\u2019")["rest_seconds"] == 120.0  # right '


def test_acute_accent_as_prime():
    """Acute accent (common keyboard substitute) treated as single prime."""
    assert extract_set_context_mentions("rest 2\u00B4")["rest_seconds"] == 120.0


def test_backtick_as_prime():
    """Backtick treated as single prime."""
    assert extract_set_context_mentions("rest 2`")["rest_seconds"] == 120.0


# --- International rest keywords ---


def test_french_repos():
    assert extract_set_context_mentions("repos 90 sec")["rest_seconds"] == 90.0


def test_french_recup_accented():
    assert extract_set_context_mentions("récup 2 min")["rest_seconds"] == 120.0


def test_french_recup_unaccented():
    assert extract_set_context_mentions("recup 90s")["rest_seconds"] == 90.0


def test_french_recuperation():
    assert extract_set_context_mentions("récupération 1:30")["rest_seconds"] == 90.0


def test_spanish_descanso():
    assert extract_set_context_mentions("descanso 90s")["rest_seconds"] == 90.0


def test_italian_riposo():
    assert extract_set_context_mentions("riposo 2 min")["rest_seconds"] == 120.0


def test_italian_pausa():
    assert extract_set_context_mentions("pausa 90 sec")["rest_seconds"] == 90.0


def test_russian_otdykh():
    assert extract_set_context_mentions("отдых 90 сек")["rest_seconds"] == 90.0


def test_russian_pauza():
    assert extract_set_context_mentions("пауза 2 мин")["rest_seconds"] == 120.0


def test_dutch_pauze():
    assert extract_set_context_mentions("pauze 90s")["rest_seconds"] == 90.0


def test_dutch_rust():
    assert extract_set_context_mentions("rust 2 min")["rest_seconds"] == 120.0


def test_polish_przerwa():
    assert extract_set_context_mentions("przerwa 90 sek")["rest_seconds"] == 90.0


def test_swedish_vila():
    assert extract_set_context_mentions("vila 2 min")["rest_seconds"] == 120.0


def test_swedish_paus():
    assert extract_set_context_mentions("paus 90s")["rest_seconds"] == 90.0


def test_turkish_dinlenme():
    assert extract_set_context_mentions("dinlenme 90 sn")["rest_seconds"] == 90.0


def test_turkish_ara():
    assert extract_set_context_mentions("ara 2 dk")["rest_seconds"] == 120.0


# --- International units ---


def test_german_sekunden():
    assert extract_set_context_mentions("pause 90 sekunden")["rest_seconds"] == 90.0


def test_german_minuten():
    assert extract_set_context_mentions("pause 2 minuten")["rest_seconds"] == 120.0


def test_spanish_segundos():
    assert extract_set_context_mentions("descanso 90 segundos")["rest_seconds"] == 90.0


def test_spanish_minutos():
    assert extract_set_context_mentions("descanso 2 minutos")["rest_seconds"] == 120.0


def test_russian_sekund():
    assert extract_set_context_mentions("пауза 90 секунд")["rest_seconds"] == 90.0


def test_russian_minuty():
    assert extract_set_context_mentions("отдых 2 минуты")["rest_seconds"] == 120.0


def test_turkish_saniye():
    assert extract_set_context_mentions("dinlenme 90 saniye")["rest_seconds"] == 90.0


def test_turkish_dakika():
    assert extract_set_context_mentions("dinlenme 2 dakika")["rest_seconds"] == 120.0


# --- CJK ---


def test_japanese_kyukei_seconds():
    """休憩90秒 = rest 90 seconds."""
    assert extract_set_context_mentions("休憩90秒")["rest_seconds"] == 90.0


def test_japanese_resuto_minutes():
    """レスト2分 = rest 2 minutes."""
    assert extract_set_context_mentions("レスト2分")["rest_seconds"] == 120.0


def test_korean_hyusik_seconds():
    """휴식 90초 = rest 90 seconds."""
    assert extract_set_context_mentions("휴식 90초")["rest_seconds"] == 90.0


def test_korean_hyusik_minutes():
    """휴식 2분 = rest 2 minutes."""
    assert extract_set_context_mentions("휴식 2분")["rest_seconds"] == 120.0


def test_korean_reseuteu_seconds():
    """레스트 90초 = rest 90 seconds (Korean transliteration)."""
    assert extract_set_context_mentions("레스트 90초")["rest_seconds"] == 90.0


def test_chinese_xiuxi_seconds():
    """休息90秒 = rest 90 seconds."""
    assert extract_set_context_mentions("休息90秒")["rest_seconds"] == 90.0


# --- Edge cases / no false positives ---


def test_no_false_positive_without_rest_keyword():
    """Prime notation alone (no rest keyword) should not match."""
    result = extract_set_context_mentions("5'10\" tall")
    assert "rest_seconds" not in result


def test_no_false_positive_on_possessive():
    """Apostrophe in possessive should not trigger."""
    result = extract_set_context_mentions("john's set was good")
    assert "rest_seconds" not in result


def test_prime_with_german_satzpause():
    """Satzpause (existing keyword) + prime notation."""
    assert extract_set_context_mentions("satzpause 90''")["rest_seconds"] == 90.0


def test_absent_mentions_do_not_fabricate_optional_fields():
    rows = [
        {
            "id": "e1",
            "timestamp": datetime(2026, 2, 11, 10, 0, 0, tzinfo=timezone.utc),
            "data": {"exercise_id": "barbell_back_squat", "reps": 5},
            "metadata": {"session_id": "s1"},
        }
    ]
    evaluated = evaluate_set_context_rows(rows)
    assert evaluated[0]["mentioned_fields"] == {}
    assert evaluated[0]["effective_defaults"] == {}
    assert evaluated[0]["missing_fields"] == []

