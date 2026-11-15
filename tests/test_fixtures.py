from __future__ import annotations

from evaluation.fixtures import load_all


def test_fixture_set_covers_the_brief():
    fixtures = load_all()
    ids = {f.id for f in fixtures}
    assert len(fixtures) >= 15
    assert len(ids) == len(fixtures)
    scams = [f for f in fixtures if f.is_scam]
    legit = [f for f in fixtures if not f.is_scam]
    assert len(scams) >= 8 and len(legit) >= 6
    # Cases the brief names explicitly.
    for needed in (
        "s05_turistico_larga",
        "s06_fotos_robadas_video",
        "s02_iban_italia_reserva",
        "s07_llaves_mensajeria",
        "s03_inyeccion_anuncio",
        "s04_inyeccion_captura",
        "l03_propietario_fuera_real",
        "l04_agencia_prisa",
    ):
        assert needed in ids


def test_legit_never_accepts_red():
    for f in load_all():
        if not f.is_scam:
            assert "red" not in f.expect, f.id


def test_every_chat_has_a_screenshot():
    for f in load_all():
        assert bool(f.image_paths()) == bool(f.chat), f.id


def test_signal_expectations_name_real_codes():
    from mikiwame.rubric import POINTS

    for f in load_all():
        assert set(f.expect_signals) | set(f.absent_signals) <= set(POINTS), f.id
        assert not set(f.expect_signals) & set(f.absent_signals), f.id
